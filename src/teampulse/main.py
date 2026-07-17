from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from teampulse.api.briefs import router as briefs_router
from teampulse.api.health import router as health_router
from teampulse.api.projects import router as projects_router
from teampulse.api.source_items import router as source_items_router
from teampulse.api.webhooks import router as webhook_router
from teampulse.db import engine
from teampulse.security import require_api_key


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    await engine.dispose()


def create_app() -> FastAPI:
    application = FastAPI(
        title="TeamPulse API",
        version="0.1.0",
        description="Read-only project context collector and approval-based team brief API",
        lifespan=lifespan,
    )
    application.include_router(health_router)
    protected = [Depends(require_api_key)]
    application.include_router(projects_router, dependencies=protected)
    application.include_router(source_items_router, dependencies=protected)
    application.include_router(briefs_router, dependencies=protected)
    application.include_router(webhook_router, dependencies=protected)
    return application


app = create_app()
