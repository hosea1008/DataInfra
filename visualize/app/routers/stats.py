"""Pivot-statistics endpoints (PRODUCTION env only - no env parameter).

Routes (mounted by app.main auto-include):
- GET /api/stats/datasets?refresh=0                          -> DatasetStatsResponse
- GET /api/stats/columns                                     -> ColumnListResponse
- GET /api/stats/column-distribution?column=X&weight=...     -> ColumnDistResponse

All SQL lives in app.services.stats_service behind a 600 s TTL cache.
Recompute modes: ``refresh=1`` recomputes now (re-sampling big tables, the
fast "刷新" path); ``exact=1`` recomputes with a full unsampled scan (implies
refresh, the slow "全量统计" path).
"""

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request

from app.db import PoolManager
from app.schemas import ColumnDistResponse, ColumnListResponse, DatasetStatsResponse
from app.services import stats_service, dataset_path_service
from app.schemas import DatasetPathsResponse

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("/datasets", response_model=DatasetStatsResponse)
def dataset_stats(
    request: Request, refresh: bool = False, exact: bool = False
) -> DatasetStatsResponse:
    """Per-dataset pies: lerobot count, episode count, total hours.

    ``refresh=1`` re-samples and replaces the cache; ``exact=1`` runs a full
    unsampled scan (implies refresh)."""
    pools: PoolManager = request.app.state.pools
    return stats_service.get_dataset_stats(pools, refresh=refresh, exact=exact)


@router.get("/columns", response_model=ColumnListResponse)
def column_list(request: Request) -> ColumnListResponse:
    """Column inventory of table ``data`` (static first, then dynamic)."""
    pools: PoolManager = request.app.state.pools
    return stats_service.list_columns(pools)


@router.get("/column-distribution", response_model=ColumnDistResponse)
def column_distribution(
    request: Request,
    column: str = Query(...),
    weight: Literal["rows", "hours"] = "rows",
    top_k: int = Query(default=12, ge=1, le=100),
    refresh: bool = False,
    exact: bool = False,
) -> ColumnDistResponse:
    """Value distribution of one whitelisted ``data`` column.

    ``column`` must be a live column of table ``data`` (400 otherwise, which
    also rejects injection attempts); ``weight`` selects the measure used to
    order buckets (rows|hours, 422 otherwise). ``refresh=1`` recomputes now
    (sampled on big tables); ``exact=1`` recomputes exactly and implies
    refresh.
    """
    pools: PoolManager = request.app.state.pools
    try:
        return stats_service.get_column_distribution(
            pools,
            column=column,
            weight=weight,
            top_k=top_k,
            refresh=refresh,
            exact=exact,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/dataset-paths", response_model=DatasetPathsResponse)
def dataset_paths(request: Request, refresh: bool = False) -> DatasetPathsResponse:
    """Sample production status paths, cached for 600 s; refresh re-samples."""
    return dataset_path_service.get_dataset_paths(request.app.state.pools, refresh)
