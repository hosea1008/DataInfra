"""Version-progress service: unpivot status_statistic into per-version items.

The ``status_statistic`` table holds EXACTLY ONE row whose columns are
progress counters: builtin counters (``download_finish``, ``register_finish``)
plus one column per scored version named ``s_<step_key>_v_<version_no>``.
This service reads that row and returns one progress item per column, with
``step_explain`` / ``version_note`` joined from the metadata tables.

All SQL lives here. Statistic columns are whitelisted through
``fetch_table_columns`` (pg_catalog) and the row is read with ``SELECT *`` -
no user input is ever interpolated into SQL. The ``total_lerobots`` count
(``COUNT(*) FROM status``) is cached per env in a small thread-safe TTL cache.
"""

import re
import threading
import time
from collections.abc import Callable

from app.db import PoolManager
from app.schemas import VersionKeyInfo, VersionProgressItem, VersionProgressResponse
from app.sqlutil import fetch_table_columns, validate_identifier

STATISTIC_TABLE = "status_statistic"
STATUS_TABLE = "status"
STEP_KEY_TABLE = "step_key"
VERSION_INFO_TABLE = "version_info"

#: s_<step_key>_v_<version_no> statistic column pattern.
VERSION_KEY_RE = re.compile(r"^s_(.+)_v_(\d+)$")

TOTAL_CACHE_TTL_S = 60.0


class VersionServiceError(Exception):
    """Base class for version-progress failures with an HTTP-mapped message."""


class StatusStatisticNotFoundError(VersionServiceError):
    """The env has no ``status_statistic`` table (e.g. test env before reset)."""

    def __init__(self, env: str) -> None:
        super().__init__(f"status_statistic not found in env '{env}'")


class StatusStatisticEmptyError(VersionServiceError):
    """``status_statistic`` exists but holds zero rows (contract: one row)."""

    def __init__(self, env: str) -> None:
        super().__init__(f"status_statistic in env '{env}' has no rows")


class InvalidKeysError(VersionServiceError):
    """Requested keys that are not safe SQL identifiers."""

    def __init__(self, keys: list[str]) -> None:
        super().__init__(f"invalid keys: {', '.join(keys)}")


class UnknownKeysError(VersionServiceError):
    """Requested keys that are not columns of ``status_statistic``."""

    def __init__(self, keys: list[str]) -> None:
        super().__init__(f"unknown keys: {', '.join(keys)}")


class _TtlCache:
    """Thread-safe per-key TTL cache for a single integer value each."""

    def __init__(self, ttl_s: float) -> None:
        self._ttl_s = ttl_s
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[float, int]] = {}

    def get_or_load(self, key: str, loader: Callable[[], int]) -> int:
        """Return the cached value for ``key`` or compute and store it.

        The loader runs outside the lock (DB I/O must not block readers);
        concurrent misses may both load - last write wins, both are correct.
        """
        now = time.monotonic()
        with self._lock:
            hit = self._entries.get(key)
            if hit is not None and hit[0] > now:
                return hit[1]
        value = loader()
        with self._lock:
            self._entries[key] = (now + self._ttl_s, value)
        return value


_total_lerobots_cache = _TtlCache(TOTAL_CACHE_TTL_S)


def _parse_keys_filter(keys_csv: str | None) -> list[str] | None:
    """Split the optional ``keys`` csv; None (or blank) means no filter."""
    if keys_csv is None:
        return None
    keys = [part.strip() for part in keys_csv.split(",")]
    keys = [key for key in keys if key]
    return keys or None


def _validate_identifiers(requested: list[str] | None) -> None:
    """Reject requested keys that are not safe SQL identifiers (400)."""
    if requested is None:
        return
    invalid = []
    for key in requested:
        try:
            validate_identifier(key)
        except ValueError:
            invalid.append(key)
    if invalid:
        raise InvalidKeysError(invalid)


def _check_known(requested: list[str], available: list[VersionKeyInfo]) -> None:
    """Reject requested keys that are not statistic columns (400)."""
    known = {info.key for info in available}
    unknown = [key for key in requested if key not in known]
    if unknown:
        raise UnknownKeysError(unknown)


def _build_available(columns: list[str]) -> list[VersionKeyInfo]:
    """One VersionKeyInfo per statistic column, builtin keys first."""
    infos = []
    for column in columns:
        match = VERSION_KEY_RE.match(column)
        if match:
            infos.append(
                VersionKeyInfo(
                    key=column,
                    step_key=match.group(1),
                    version_no=int(match.group(2)),
                )
            )
        else:
            infos.append(VersionKeyInfo(key=column))
    # Builtin (unparsed) keys first; parsed keys by (step_key, version_no).
    # The sort is stable, so builtin keys keep their pg_catalog column order.
    infos.sort(
        key=lambda info: (
            info.step_key is not None,
            info.step_key or "",
            info.version_no if info.version_no is not None else 0,
        )
    )
    return infos


def _fetch_step_explains(cur, step_keys: list[str]) -> dict[str, str | None]:
    """Map step_key -> step_explain (LEFT JOIN semantics via lookup)."""
    if not step_keys:
        return {}
    cur.execute(
        "SELECT step_key, step_explain FROM step_key WHERE step_key = ANY(%s)",
        (step_keys,),
    )
    return {row["step_key"]: row["step_explain"] for row in cur.fetchall()}


def _fetch_version_notes(cur, version_keys: list[str]) -> dict[str, str | None]:
    """Map version_key -> version_note (LEFT JOIN semantics via lookup)."""
    if not version_keys:
        return {}
    cur.execute(
        "SELECT version_key, version_note FROM version_info WHERE version_key = ANY(%s)",
        (version_keys,),
    )
    return {row["version_key"]: row["version_note"] for row in cur.fetchall()}


def _count_lerobots(pools: PoolManager, env: str) -> int:
    with pools.cursor(env) as cur:
        cur.execute("SELECT COUNT(*) FROM status")
        return int(cur.fetchone()[0])


def build_version_progress(
    pools: PoolManager, env: str, keys_csv: str | None
) -> VersionProgressResponse:
    """Build the VersionProgressResponse for ``env``.

    Raises StatusStatisticNotFoundError / StatusStatisticEmptyError (404
    material) and InvalidKeysError / UnknownKeysError (400 material); the
    router maps them to HTTP responses.
    """
    requested = _parse_keys_filter(keys_csv)
    _validate_identifiers(requested)

    with pools.cursor(env, dict_cursor=True) as cur:
        columns = fetch_table_columns(cur, STATISTIC_TABLE)
        if not columns:
            raise StatusStatisticNotFoundError(env)

        cur.execute("SELECT * FROM status_statistic LIMIT 1")
        row = cur.fetchone()
        if row is None:
            raise StatusStatisticEmptyError(env)
        counts = dict(row)

        available = _build_available(columns)
        if requested is not None:
            _check_known(requested, available)
        wanted = requested if requested is not None else [info.key for info in available]
        infos = {info.key: info for info in available}

        # Metadata lookups are guarded: an env may legitimately lack the
        # metadata tables (test env mid-reset) - LEFT JOIN then yields NULLs.
        step_keys = sorted(
            {infos[key].step_key for key in wanted if infos[key].step_key is not None}
        )
        step_explains = (
            _fetch_step_explains(cur, step_keys)
            if fetch_table_columns(cur, STEP_KEY_TABLE)
            else {}
        )
        version_notes = (
            _fetch_version_notes(cur, wanted)
            if fetch_table_columns(cur, VERSION_INFO_TABLE)
            else {}
        )

    total_lerobots = _total_lerobots_cache.get_or_load(
        env, lambda: _count_lerobots(pools, env)
    )

    items = []
    for key in wanted:
        info = infos[key]
        count = counts.get(key)
        if count is None:
            ratio = None
        elif total_lerobots:
            ratio = round(count / total_lerobots, 6)
        else:
            ratio = 0
        items.append(
            VersionProgressItem(
                key=key,
                step_key=info.step_key,
                version_no=info.version_no,
                count=count,
                ratio=ratio,
                step_explain=step_explains.get(info.step_key),
                version_note=version_notes.get(key),
            )
        )

    return VersionProgressResponse(
        env=env,
        total_lerobots=total_lerobots,
        available=available,
        items=items,
    )
