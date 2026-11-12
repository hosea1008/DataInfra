"""SQL layer + TTL cache for the pivot-statistics feature (routers/stats.py).

Everything here reads the PRODUCTION env only. All dynamic SQL identifiers go
through ``qident`` (whitelist-validated + psycopg2 sql.Identifier composition);
the data-column whitelist is read live from pg_catalog on every cache miss.

Caching: one process-wide TTL cache (600 s).

- Dataset stats are cached as a whole under ``("datasets",)``.
- Column distributions cache the *full bucket aggregation* per column under
  ``("column_dist", column)``; ``weight`` / ``top_k`` slicing happens on every
  request in Python, so switching the measure or Top-K never re-runs SQL.

Single-flight: a per-key lock ensures only one thread computes a cold/expired
entry; concurrent callers wait and then take the freshly cached result instead
of piling duplicate multi-minute scans onto the pool.

Sampling: the ``data`` table holds tens of millions of rows, so a full-table
GROUP BY can run for minutes. When pg_class estimates it above
``SAMPLE_THRESHOLD_ROWS`` rows, aggregates run against
``TABLESAMPLE SYSTEM (pct)`` targeting ~``SAMPLE_TARGET_ROWS`` sampled rows and
the row/hour counts are scaled back up (1/pct); ratios are unaffected. The
response is flagged ``sampled``/``sample_pct`` so the UI can label estimates.

Recompute modes (both endpoints):
- ``refresh=True`` (the "刷新" button): recompute NOW with sampling, replacing
  the cached entry with a fresh (re-drawn) estimate.
- ``exact=True`` (the "全量统计" button, implies refresh): recompute NOW with a
  full exact scan, replacing the cached entry with exact numbers.
``cached_at`` is the moment the payload was computed (and stored).
"""

import re
import threading
import time
from collections.abc import Hashable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from psycopg2 import sql

from app.db import PoolManager
from app.schemas import (
    ColumnDistResponse,
    ColumnInfoItem,
    ColumnListResponse,
    DatasetStatItem,
    DatasetStatsResponse,
    DatasetTotals,
    DistItem,
)
from app.sqlutil import fetch_table_columns, qident, validate_identifier

CACHE_TTL_S = 600.0

#: Dynamic (step-output) columns look like ``s_<step>_v_<n>_c_<name>``.
DYNAMIC_COLUMN_RE = re.compile(r"^s_.+_v_\d+_c_.+$")

#: Sentinel value of the aggregated "everything past top_k" bucket.
OTHER_VALUE = "__other__"

#: Tables with at least this many live rows (pg_class.reltuples estimate) are
#: aggregated via TABLESAMPLE instead of a full scan.
SAMPLE_THRESHOLD_ROWS = 1_000_000

#: Rough number of rows a sampled aggregation aims to read.
SAMPLE_TARGET_ROWS = 300_000


class TTLCache:
    """Thread-safe key -> payload store with per-entry TTL expiry."""

    def __init__(self, ttl_s: float = CACHE_TTL_S) -> None:
        self._ttl_s = ttl_s
        self._lock = threading.Lock()
        self._entries: dict[Hashable, tuple[float, Any]] = {}

    def get(self, key: Hashable) -> Any | None:
        """Return the payload for ``key``, or None when missing/expired."""
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            stored_at, payload = entry
            if time.monotonic() - stored_at > self._ttl_s:
                del self._entries[key]
                return None
            return payload

    def set(self, key: Hashable, payload: Any) -> None:
        """Store ``payload`` under ``key`` with a fresh TTL."""
        with self._lock:
            self._entries[key] = (time.monotonic(), payload)

    def clear(self) -> None:
        """Drop every cached payload."""
        with self._lock:
            self._entries.clear()


_CACHE = TTLCache()


def clear_cache() -> None:
    """Drop every cached statistics payload (used by tests and manual resets)."""
    _CACHE.clear()


# --- single-flight ---------------------------------------------------------

_flight_registry_lock = threading.Lock()
_flight_locks: dict[Hashable, threading.Lock] = {}


@contextmanager
def _singleflight(key: Hashable) -> Iterator[None]:
    """Serialize cache population per ``key``.

    Concurrent callers of a cold entry block here; the winner computes and
    stores, the losers re-read the cache inside the critical section instead
    of launching duplicate heavy scans. Entries are never removed: the key
    space is bounded by the column inventory of the ``data`` table.
    """
    with _flight_registry_lock:
        lock = _flight_locks.setdefault(key, threading.Lock())
    with lock:
        yield


def _round6(value: float) -> float:
    return round(value, 6)


# --- sampling --------------------------------------------------------------


def _sample_pct(cur) -> float | None:
    """TABLESAMPLE percentage for table ``data``, or None when a full scan is fine.

    The size estimate comes from pg_class.reltuples (kept fresh by autovacuum);
    a negative/missing estimate means "unknown" and falls back to a full scan.
    """
    cur.execute("SELECT reltuples FROM pg_class WHERE oid = to_regclass(%s)", ("data",))
    row = cur.fetchone()
    est = float(row["reltuples"]) if row and row["reltuples"] is not None else -1.0
    if est < SAMPLE_THRESHOLD_ROWS:
        return None
    return max(0.5, min(100.0, SAMPLE_TARGET_ROWS * 100.0 / est))


def _sample_clause(pct: float | None) -> sql.Composable:
    """``TABLESAMPLE SYSTEM (pct)`` SQL fragment; empty for a full scan.

    ``pct`` is a locally clamped float rendered via sql.Literal (never quoted,
    never user input), so this composition is injection-safe.
    """
    if pct is None:
        return sql.SQL("")
    return sql.SQL(" TABLESAMPLE SYSTEM ({})").format(sql.Literal(pct))


# ---------------------------------------------------------------------------
# Dataset pies
# ---------------------------------------------------------------------------


def _fetch_dataset_values(cur, query: str | sql.Composable) -> list[tuple[str, float]]:
    """Run a ``dataset_name, value`` GROUP BY query; return pairs sorted by name."""
    cur.execute(query)
    pairs = [(row["dataset_name"], float(row["value"])) for row in cur.fetchall()]
    pairs.sort(key=lambda pair: pair[0])
    return pairs


def _stat_items(pairs: list[tuple[str, float]]) -> list[DatasetStatItem]:
    """Build pie items with ratio = value / sum(values) (rounded to 6 decimals)."""
    total = sum(value for _, value in pairs)
    return [
        DatasetStatItem(
            dataset_name=name,
            value=value,
            ratio=_round6(value / total) if total else None,
        )
        for name, value in pairs
    ]


def _compute_dataset_stats(pools: PoolManager, *, exact: bool) -> DatasetStatsResponse:
    """Run the three per-dataset aggregations (hours optionally sampled)."""
    with pools.cursor("production", dict_cursor=True) as cur:
        lerobots = _fetch_dataset_values(
            cur, "SELECT dataset_name, count(*) AS value FROM status GROUP BY 1"
        )
        episodes = _fetch_dataset_values(
            cur,
            "SELECT dataset_name, sum(episode_count) AS value "
            "FROM lerobot_episode GROUP BY 1",
        )
        pct = None if exact else _sample_pct(cur)
        hours = _fetch_dataset_values(
            cur,
            sql.SQL(
                "SELECT dataset_name, sum(origin_video_duration_s)/3600.0 AS value "
                "FROM data{sample} GROUP BY 1"
            ).format(sample=_sample_clause(pct)),
        )

    scale = 100.0 / pct if pct is not None else 1.0
    hours = [(name, value * scale) for name, value in hours]

    return DatasetStatsResponse(
        cached_at=datetime.now(UTC),
        totals=DatasetTotals(
            lerobots=int(sum(value for _, value in lerobots)),
            episodes=int(sum(value for _, value in episodes)),
            hours=sum(value for _, value in hours),
        ),
        lerobots=_stat_items(lerobots),
        episodes=_stat_items(episodes),
        hours=_stat_items(hours),
        sampled=pct is not None,
        sample_pct=pct,
    )


def get_dataset_stats(
    pools: PoolManager, refresh: bool = False, exact: bool = False
) -> DatasetStatsResponse:
    """Per-dataset pies: lerobot count (status), episode count (lerobot_episode),
    total hours (data.origin_video_duration_s / 3600, sampled on big tables).

    ``refresh=True`` recomputes now (sampled on big tables); ``exact=True``
    recomputes with a full unsampled scan and implies refresh."""
    key = ("datasets",)
    recompute = refresh or exact
    if not recompute:
        cached = _CACHE.get(key)
        if cached is not None:
            return cached
    with _singleflight(key):
        if not recompute:
            cached = _CACHE.get(key)
            if cached is not None:
                return cached
        response = _compute_dataset_stats(pools, exact=exact)
        _CACHE.set(key, response)
        return response


# ---------------------------------------------------------------------------
# Column list
# ---------------------------------------------------------------------------


def list_columns(pools: PoolManager, refresh: bool = False) -> ColumnListResponse:
    """Column inventory of table ``data``: static block first (attnum order),
    then dynamic (attnum order), with notes joined from ``column_info``."""
    key = ("columns",)
    if not refresh:
        cached = _CACHE.get(key)
        if cached is not None:
            return cached

    with pools.cursor("production", dict_cursor=True) as cur:
        names = fetch_table_columns(cur, "data")
        cur.execute("SELECT column_key, column_note FROM column_info")
        notes = {row["column_key"]: row["column_note"] for row in cur.fetchall()}

    def is_dynamic(name: str) -> bool:
        return DYNAMIC_COLUMN_RE.match(name) is not None

    columns = [
        ColumnInfoItem(name=name, kind=kind, note=notes.get(name))
        for kind, group in (
            ("static", [n for n in names if not is_dynamic(n)]),
            ("dynamic", [n for n in names if is_dynamic(n)]),
        )
        for name in group
    ]
    response = ColumnListResponse(columns=columns)
    _CACHE.set(key, response)
    return response


# ---------------------------------------------------------------------------
# Column distribution
# ---------------------------------------------------------------------------


def _dist_item(bucket: dict, total_rows: int, total_hours: float) -> DistItem:
    return DistItem(
        value=bucket["value"],
        rows=bucket["rows"],
        hours=bucket["hours"],
        ratio_rows=_round6(bucket["rows"] / total_rows) if total_rows else None,
        ratio_hours=_round6(bucket["hours"] / total_hours) if total_hours else None,
    )


def _compute_dist_buckets(
    pools: PoolManager, column: str, *, exact: bool
) -> dict[str, Any]:
    """Aggregate every value bucket of ``data.<column>`` (sampled on big tables).

    Returns the cache entry: full bucket list (rows/hours scaled back up when
    sampled), sampling flags, and the computation timestamp.
    """
    with pools.cursor("production", dict_cursor=True) as cur:
        if column not in fetch_table_columns(cur, "data"):
            raise ValueError(f"unknown column of 'data': {column!r}")
        pct = None if exact else _sample_pct(cur)
        cur.execute(
            sql.SQL(
                "SELECT {col} AS value, count(*) AS rows, "
                "sum(origin_video_duration_s)/3600.0 AS hours "
                "FROM data{sample} GROUP BY 1"
            ).format(col=qident(column), sample=_sample_clause(pct))
        )
        raw = cur.fetchall()

    scale = 100.0 / pct if pct is not None else 1.0
    buckets = [
        {
            "value": row["value"],
            "rows": int(round(row["rows"] * scale)),
            "hours": float(row["hours"] or 0.0) * scale,
        }
        for row in raw
    ]
    return {
        "buckets": buckets,
        "sampled": pct is not None,
        "sample_pct": pct,
        "cached_at": datetime.now(UTC),
    }


def get_column_distribution(
    pools: PoolManager,
    *,
    column: str,
    weight: str,
    top_k: int,
    refresh: bool = False,
    exact: bool = False,
) -> ColumnDistResponse:
    """Duration-aware value distribution of one whitelisted ``data`` column.

    Buckets are ordered by the ``weight`` measure ("rows" or "hours") DESC;
    the first ``top_k`` become items, the remainder is aggregated into
    ``other`` (None when the remainder is empty). NULL surfaces as a bucket
    with value None. Raises ValueError for a non-whitelisted column.

    The expensive part (the full/sample aggregation) is cached per column;
    weight/top_k only affect the cheap Python-side slicing, so switching them
    re-runs no SQL. ``refresh=True`` recomputes now (sampled on big tables);
    ``exact=True`` recomputes with a full unsampled scan and implies refresh.
    """
    validate_identifier(column)
    key = ("column_dist", column)
    recompute = refresh or exact
    entry = None if recompute else _CACHE.get(key)
    if entry is None:
        with _singleflight(key):
            entry = None if recompute else _CACHE.get(key)
            if entry is None:
                entry = _compute_dist_buckets(pools, column, exact=exact)
                _CACHE.set(key, entry)

    buckets = sorted(entry["buckets"], key=lambda bucket: bucket[weight], reverse=True)
    total_rows = sum(bucket["rows"] for bucket in buckets)
    total_hours = sum(bucket["hours"] for bucket in buckets)

    items = [_dist_item(bucket, total_rows, total_hours) for bucket in buckets[:top_k]]
    rest = buckets[top_k:]
    other = None
    if rest:
        other = _dist_item(
            {
                "value": OTHER_VALUE,
                "rows": sum(bucket["rows"] for bucket in rest),
                "hours": sum(bucket["hours"] for bucket in rest),
            },
            total_rows,
            total_hours,
        )

    return ColumnDistResponse(
        column=column,
        cached_at=entry["cached_at"],
        sampled=entry["sampled"],
        sample_pct=entry["sample_pct"],
        items=items,
        other=other,
        total_rows=total_rows,
        total_hours=total_hours,
    )
