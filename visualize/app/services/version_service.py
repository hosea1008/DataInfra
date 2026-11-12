"""Version progress: builtin counts from status_statistic, version hours from data.

Each version's live columns come from version_info joined to column_info.
One exact aggregate scan of data computes total duration and every version's
duration (any one column populated); the result is cached for ten minutes and
can be manually refreshed. Filtering the visible keys reuses that same scan.
"""

import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime

from psycopg2 import sql

from app.db import PoolManager
from app.schemas import VersionKeyInfo, VersionProgressItem, VersionProgressResponse
from app.sqlutil import fetch_table_columns
from app.services.stats_service import TTLCache, _singleflight

STATISTIC_TABLE = "status_statistic"
STATUS_TABLE = "status"
STEP_KEY_TABLE = "step_key"
VERSION_INFO_TABLE = "version_info"

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
_duration_cache = TTLCache()


def _fetch_versions(cur, version_keys: list[str]) -> dict[str, dict]:
    """Look up version metadata by its complete key, without parsing version_no."""
    if not version_keys:
        return {}
    if not fetch_table_columns(cur, VERSION_INFO_TABLE):
        return {}
    cur.execute(
        "SELECT version_key, step_key, version_no, version_note, "
        "created_data_columns FROM version_info WHERE version_key = ANY(%s)",
        (version_keys,),
    )
    return {row["version_key"]: dict(row) for row in cur.fetchall()}


def _created_column_names(raw) -> list[str]:
    """Accept array/JSON-like or the documented '[aaa, bbb]' text format."""
    if isinstance(raw, (list, tuple)):
        parts = raw
    elif isinstance(raw, str):
        parts = raw.strip().strip("[]").split(",")
    else:
        return []
    return [str(part).strip().strip("'\"") for part in parts if str(part).strip().strip("'\"")]


def _version_columns(cur, version_keys: list[str], versions: dict[str, dict]) -> dict[str, list[str]]:
    """Resolve real data columns from version_info and column_info metadata."""
    result = {key: [] for key in version_keys}
    if not versions:
        return result
    live = set(fetch_table_columns(cur, "data"))
    info_keys = []
    if fetch_table_columns(cur, "column_info"):
        cur.execute("SELECT column_key FROM column_info")
        info_keys = [row["column_key"] for row in cur.fetchall()]
    for key in version_keys:
        version = versions.get(key)
        if version is None:
            continue
        prefix = key + "_c_"
        candidates = {name for name in info_keys if isinstance(name, str) and name.startswith(prefix)}
        for name in _created_column_names(version.get("created_data_columns")):
            candidates.add(name if name.startswith(prefix) else prefix + name)
        result[key] = sorted(name for name in candidates if name in live)
    return result


def _compute_durations(pools: PoolManager, env: str, columns: dict[str, list[str]]) -> dict:
    """One exact data scan for total and every version, no row transfer."""
    with pools.cursor(env, dict_cursor=True) as cur:
        fields = [sql.SQL("COALESCE(sum(origin_video_duration_s), 0) AS total")]
        keys = [key for key, cols in columns.items() if cols]
        for index, key in enumerate(keys):
            # OR short-circuits on the first populated column; one row is
            # counted once even when several version columns are populated.
            # Every name was checked against live pg_catalog columns. Identifier
            # quotes it safely even when a user-chosen version contains punctuation.
            condition = sql.SQL(" OR ").join(
                sql.SQL("{} IS NOT NULL").format(sql.Identifier(col)) for col in columns[key]
            )
            fields.append(sql.SQL("COALESCE(sum(origin_video_duration_s) FILTER (WHERE {}), 0) AS {}")
                          .format(condition, sql.Identifier(f"v{index}")))
        query = sql.SQL("SELECT {} FROM data").format(sql.SQL(", ").join(fields))
        cur.execute(query)
        row = cur.fetchone()
    return {
        "total_hours": float(row["total"] or 0) / 3600,
        "hours": {key: float(row[f"v{index}"] or 0) / 3600
                  for index, key in enumerate(keys)},
        "cached_at": datetime.now(UTC),
    }


def _get_durations(pools: PoolManager, env: str, columns: dict[str, list[str]], *, refresh: bool) -> dict:
    # The metadata signature forces a new scan when versions or their columns change.
    key = ("version_durations", env, tuple((k, tuple(v)) for k, v in sorted(columns.items())))
    if not refresh:
        hit = _duration_cache.get(key)
        if hit is not None:
            return hit
    with _singleflight(key):
        if not refresh:
            hit = _duration_cache.get(key)
            if hit is not None:
                return hit
        result = _compute_durations(pools, env, columns)
        _duration_cache.set(key, result)
        return result


def _parse_keys_filter(keys_csv: str | None) -> list[str] | None:
    """Split the optional ``keys`` csv; None (or blank) means no filter."""
    if keys_csv is None:
        return None
    keys = [part.strip() for part in keys_csv.split(",")]
    keys = [key for key in keys if key]
    return keys or None


def _check_known(requested: list[str], available: list[VersionKeyInfo]) -> None:
    """Reject requested keys that are not statistic columns (400)."""
    known = {info.key for info in available}
    unknown = [key for key in requested if key not in known]
    if unknown:
        raise UnknownKeysError(unknown)


def _build_available(columns: list[str], versions: dict[str, dict]) -> list[VersionKeyInfo]:
    """Treat every s_ key as a version; use version_info for its metadata."""
    infos = []
    for column in columns:
        if column.startswith("s_"):
            version = versions.get(column, {})
            infos.append(
                VersionKeyInfo(
                    key=column,
                    step_key=version.get("step_key"),
                    version_no=version.get("version_no"),
                )
            )
        else:
            infos.append(VersionKeyInfo(key=column))
    # Builtin keys first, then versions by metadata and complete key.
    # The sort is stable, so builtin keys keep their pg_catalog column order.
    infos.sort(
        key=lambda info: (
            info.key.startswith("s_"),
            info.step_key or "",
            str(info.version_no) if info.version_no is not None else "",
            info.key,
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


def _count_lerobots(pools: PoolManager, env: str) -> int:
    with pools.cursor(env) as cur:
        cur.execute("SELECT COUNT(*) FROM status")
        return int(cur.fetchone()[0])


def build_version_progress(
    pools: PoolManager, env: str, keys_csv: str | None,
    *, refresh: bool = False,
) -> VersionProgressResponse:
    """Build the VersionProgressResponse for ``env``.

    Raises StatusStatisticNotFoundError / StatusStatisticEmptyError (404
    material) and InvalidKeysError / UnknownKeysError (400 material); the
    router maps them to HTTP responses.
    """
    requested = _parse_keys_filter(keys_csv)

    with pools.cursor(env, dict_cursor=True) as cur:
        columns = fetch_table_columns(cur, STATISTIC_TABLE)
        if not columns:
            raise StatusStatisticNotFoundError(env)

        cur.execute("SELECT * FROM status_statistic LIMIT 1")
        row = cur.fetchone()
        if row is None:
            raise StatusStatisticEmptyError(env)
        counts = dict(row)

        version_keys = [column for column in columns if column.startswith("s_")]
        versions = _fetch_versions(cur, version_keys)
        available = _build_available(columns, versions)
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
        columns_by_version = _version_columns(cur, version_keys, versions)

    durations = _get_durations(pools, env, columns_by_version, refresh=refresh) if version_keys else None

    total_lerobots = _total_lerobots_cache.get_or_load(
        env, lambda: _count_lerobots(pools, env)
    )

    items = []
    for key in wanted:
        info = infos[key]
        count = counts.get(key)
        is_version = key.startswith("s_")
        completed_hours = durations["hours"].get(key) if is_version and durations else None
        total_hours = durations["total_hours"] if is_version and durations else None
        if is_version and completed_hours is not None and total_hours is not None:
            ratio = round(completed_hours / total_hours, 6) if total_hours else None
        elif is_version:
            ratio = None
        elif count is None:
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
                count=None if is_version else count,
                completed_hours=completed_hours,
                total_hours=total_hours,
                progress_kind="hours" if is_version else "count",
                ratio=ratio,
                step_explain=step_explains.get(info.step_key),
                version_note=versions.get(key, {}).get("version_note"),
            )
        )

    return VersionProgressResponse(
        env=env,
        total_lerobots=total_lerobots,
        cached_at=durations["cached_at"] if durations else None,
        available=available,
        items=items,
    )
