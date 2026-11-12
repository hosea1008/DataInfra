"""Table-preview endpoints: table list + paginated, truncated row preview.

Routes (mounted by app.main auto-include):
- GET /api/envs/{env}/tables                    -> TableListResponse
- GET /api/envs/{env}/tables/{table}/rows       -> PreviewResponse

All user input is validated at this boundary (env name, table whitelist,
order_by whitelist, order_dir keyword) before any SQL is built in
app.services.preview_service. Bad input is a 400/404/422, never a 500.
"""

from fastapi import APIRouter, HTTPException, Query, Request

from app.db import PoolManager
from app.schemas import PreviewResponse, TableInfo, TableListResponse
from app.services import preview_service
from app.sqlutil import fetch_table_columns, fetch_table_names

router = APIRouter(prefix="/api/envs", tags=["tables"])

VALID_ENVS = ("production", "test")
ORDER_DIRS = ("asc", "desc")


def _check_env(env: str) -> str:
    """Return ``env`` if valid, else raise 400 (unknown environment name)."""
    if env not in VALID_ENVS:
        raise HTTPException(
            status_code=400,
            detail=f"unknown env: {env!r} (expected one of {sorted(VALID_ENVS)})",
        )
    return env


@router.get("/{env}/tables", response_model=TableListResponse)
def list_tables(request: Request, env: str) -> TableListResponse:
    """List public-schema tables of ``env`` with catalog row/column estimates."""
    _check_env(env)
    pools: PoolManager = request.app.state.pools
    with pools.cursor(env) as cur:
        tables = preview_service.list_tables(cur)
    return TableListResponse(env=env, tables=[TableInfo(**t) for t in tables])


@router.get("/{env}/tables/{table}/rows", response_model=PreviewResponse)
def preview_rows(
    request: Request,
    env: str,
    table: str,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    order_by: str = Query(default=""),
    order_dir: str = Query(default="asc"),
) -> PreviewResponse:
    """Return up to ``limit`` rows of ``table`` (serialize + truncate pipeline).

    ``table`` must be in the live table whitelist (404 otherwise). ``order_by``
    is optional and must be one of the table's live columns (400 otherwise);
    ``order_dir`` must be asc|desc (400 otherwise). No order_by -> no ORDER BY.
    """
    _check_env(env)
    direction = order_dir.lower()
    if direction not in ORDER_DIRS:
        raise HTTPException(
            status_code=400,
            detail=f"invalid order_dir: {order_dir!r} (expected asc|desc)",
        )
    pools: PoolManager = request.app.state.pools
    with pools.cursor(env) as cur:
        if table not in fetch_table_names(cur):
            raise HTTPException(status_code=404, detail=f"unknown table: {table!r}")
        order_col: str | None = None
        if order_by:
            if order_by not in fetch_table_columns(cur, table):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"invalid order_by: {order_by!r} "
                        f"(not a column of {table!r})"
                    ),
                )
            order_col = order_by
        columns, rows, est_rows = preview_service.fetch_preview(
            cur,
            table=table,
            order_by=order_col,
            order_dir=direction,
            limit=limit,
            offset=offset,
        )
    return PreviewResponse(
        table=table,
        columns=columns,
        rows=rows,
        est_rows=est_rows,
        limit=limit,
        offset=offset,
    )
