"""Version-progress router: GET /api/envs/{env}/versions/progress.

Unpivots the single-row ``status_statistic`` table into per-step/version
progress items (see app.services.version_service for the SQL). The router
only maps service errors to HTTP status codes:
- unknown env, invalid/unknown ``keys`` -> 400
- missing/empty ``status_statistic``    -> 404
"""

from fastapi import APIRouter, HTTPException, Request

from app.schemas import VersionProgressResponse
from app.services import version_service

router = APIRouter(prefix="/api/envs", tags=["versions"])

VALID_ENVS = ("production", "test")


@router.get("/{env}/versions/progress", response_model=VersionProgressResponse)
def get_version_progress(
    request: Request, env: str, keys: str | None = None
) -> VersionProgressResponse:
    """Return per-version progress items for ``env`` (optional csv ``keys``)."""
    if env not in VALID_ENVS:
        raise HTTPException(
            status_code=400,
            detail=f"unknown env: '{env}' (expected one of {list(VALID_ENVS)})",
        )
    try:
        return version_service.build_version_progress(
            request.app.state.pools, env, keys
        )
    except version_service.InvalidKeysError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except version_service.UnknownKeysError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except version_service.StatusStatisticNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except version_service.StatusStatisticEmptyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
