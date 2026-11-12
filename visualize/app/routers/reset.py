"""HTTP layer for the reset-test-environment wizard.

Validation-only: all logic lives in :mod:`app.services.reset_service`; this
router translates its ResetValidationError into HTTP 400 and ResetConfigError
into HTTP 500.
"""

from fastapi import APIRouter, HTTPException, Request

from app.db import PoolManager
from app.schemas import (
    ResetDatasetsResponse,
    ResetExecuteRequest,
    ResetExecuteResponse,
    ResetPlanRequest,
    ResetPlanResponse,
)
from app.services import reset_service

router = APIRouter(prefix="/api/reset", tags=["reset"])


def _pools(request: Request) -> PoolManager:
    return request.app.state.pools


@router.get("/datasets", response_model=ResetDatasetsResponse)
def get_reset_datasets(request: Request) -> ResetDatasetsResponse:
    """Wizard step 1: production datasets + lerobot counts + default n."""
    return reset_service.list_datasets(_pools(request))


@router.post("/plan", response_model=ResetPlanResponse)
def post_reset_plan(request: Request, body: ResetPlanRequest) -> ResetPlanResponse:
    """Wizard step 2: sample lerobots, estimate copy volumes, warn."""
    try:
        return reset_service.build_plan(_pools(request), body)
    except reset_service.ResetValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/execute", response_model=ResetExecuteResponse)
def post_reset_execute(
    request: Request, body: ResetExecuteRequest
) -> ResetExecuteResponse:
    """Wizard step 3: rebuild the TEST schema and copy the sampled data."""
    try:
        return reset_service.execute_reset(_pools(request), body)
    except reset_service.ResetValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except reset_service.ResetConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
