"""FastAPI application factory for the visualize service.

Exposes ``create_app()`` and a module-level ``app`` for uvicorn. The lifespan
creates one :class:`app.db.PoolManager` (stored on ``app.state.pools``) and
closes all pools on shutdown. Feature routers listed in ``ROUTER_MODULES``
are auto-included (a not-yet-written module is skipped with a warning, so the
app stays importable while features land; a broken existing module raises).

JSON responses use Starlette's default JSONResponse (``ensure_ascii=False``),
so non-ASCII payloads (e.g. Chinese dataset notes) are served as UTF-8.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
import logging

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from app.db import PoolManager

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

logger = logging.getLogger("visualize")

# Feature routers, each exposing a module-level ``router`` APIRouter.
ROUTER_MODULES = (
    "app.routers.tables",
    "app.routers.stats",
    "app.routers.versions",
    "app.routers.reset",
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create the PoolManager on startup; close all pools on shutdown."""
    pools = PoolManager()
    app.state.pools = pools
    yield
    pools.close_all()


def _include_feature_routers(app: FastAPI) -> None:
    """Include each router module in ROUTER_MODULES (skip missing modules)."""
    for mod_name in ROUTER_MODULES:
        try:
            mod = import_module(mod_name)
        except ModuleNotFoundError as exc:
            missing = exc.name or ""
            if mod_name == missing or mod_name.startswith(missing + "."):
                logger.warning("router module %s not present; skipping", mod_name)
                continue
            raise
        router = getattr(mod, "router", None)
        if router is None:
            logger.warning("%s has no 'router' attribute; skipping", mod_name)
            continue
        app.include_router(router)


def create_app() -> FastAPI:
    """Build the FastAPI app: API routes first, static mount LAST at '/'."""
    app = FastAPI(title="DataInfra Visualize", lifespan=lifespan)

    _include_feature_routers(app)

    @app.get("/api/health")
    def health(request: Request) -> dict:
        """Liveness + per-env DB reachability (SELECT 1 via the pools)."""
        pools: PoolManager = request.app.state.pools
        return {
            "status": "ok",
            "envs": {
                "production": pools.health_check("production"),
                "test": pools.health_check("test"),
            },
            "time": datetime.now(UTC).isoformat(),
        }

    # Mounted LAST so API routes keep priority over the catch-all '/' mount.
    # Skipped silently when the directory is absent (frontend added later).
    if STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

    return app


app = create_app()
