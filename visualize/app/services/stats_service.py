"""SQL layer + TTL cache for the pivot-statistics feature (routers/stats.py).

Everything here reads the PRODUCTION env only. All dynamic SQL identifiers go
through ``qident`` (whitelist-validated + psycopg2 sql.Identifier composition);
the data-column whitelist is read live from pg_catalog on every cache miss.

Caching: one process-wide TTL cache (600 s). Endpoints pass ``refresh=1`` to
bypass a hit and repopulate the entry; ``cached_at`` on the responses is the
moment the payload was computed (and stored), not served.
"""

import re
import threading
import time
from collections.abc import Hashable
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


_CACHE = TTLCache()


def _round6(value: float) -> float:
    return round(value, 6)


# ---------------------------------------------------------------------------
# Dataset pies
# ---------------------------------------------------------------------------


def _fetch_dataset_values(cur, query: str) -> list[tuple[str, float]]:
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


def get_dataset_stats(pools: PoolManager, refresh: bool = False) -> DatasetStatsResponse:
    """Per-dataset pies: lerobot count (status), episode count (lerobot_episode),
    total hours (data.origin_video_duration_s / 3600)."""
    key = ("datasets",)
    if not refresh:
        cached = _CACHE.get(key)
        if cached is not None:
            return cached

    with pools.cursor("production", dict_cursor=True) as cur:
        lerobots = _fetch_dataset_values(
            cur, "SELECT dataset_name, count(*) AS value FROM status GROUP BY 1"
        )
        episodes = _fetch_dataset_values(
            cur,
            "SELECT dataset_name, sum(episode_count) AS value "
            "FROM lerobot_episode GROUP BY 1",
        )
        hours = _fetch_dataset_values(
            cur,
            "SELECT dataset_name, sum(origin_video_duration_s)/3600.0 AS value "
            "FROM data GROUP BY 1",
        )

    response = DatasetStatsResponse(
        cached_at=datetime.now(UTC),
        totals=DatasetTotals(
            lerobots=int(sum(value for _, value in lerobots)),
            episodes=int(sum(value for _, value in episodes)),
            hours=sum(value for _, value in hours),
        ),
        lerobots=_stat_items(lerobots),
        episodes=_stat_items(episodes),
        hours=_stat_items(hours),
    )
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


def get_column_distribution(
    pools: PoolManager,
    *,
    column: str,
    weight: str,
    top_k: int,
    refresh: bool = False,
) -> ColumnDistResponse:
    """Duration-aware value distribution of one whitelisted ``data`` column.

    Buckets are ordered by the ``weight`` measure ("rows" or "hours") DESC;
    the first ``top_k`` become items, the remainder is aggregated into
    ``other`` (None when the remainder is empty). NULL surfaces as a bucket
    with value None. Raises ValueError for a non-whitelisted column.
    """
    validate_identifier(column)
    key = ("column_dist", column, weight, top_k)
    if not refresh:
        cached = _CACHE.get(key)
        if cached is not None:
            return cached

    with pools.cursor("production", dict_cursor=True) as cur:
        if column not in fetch_table_columns(cur, "data"):
            raise ValueError(f"unknown column of 'data': {column!r}")
        cur.execute(
            sql.SQL(
                "SELECT {col} AS value, count(*) AS rows, "
                "sum(origin_video_duration_s)/3600.0 AS hours "
                "FROM data GROUP BY 1"
            ).format(col=qident(column))
        )
        buckets = [
            {
                "value": row["value"],
                "rows": int(row["rows"]),
                "hours": float(row["hours"] or 0.0),
            }
            for row in cur.fetchall()
        ]

    buckets.sort(key=lambda bucket: bucket[weight], reverse=True)
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

    response = ColumnDistResponse(
        column=column,
        cached_at=datetime.now(UTC),
        items=items,
        other=other,
        total_rows=total_rows,
        total_hours=total_hours,
    )
    _CACHE.set(key, response)
    return response
