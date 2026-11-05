import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.db.models import Base
from app.db.session import get_engine
from app.routers import articles, health, ping, sittings
from app.services import articles as articles_service

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # No migrations yet: create missing tables on startup. Replace with alembic once the schema settles.
    try:
        async with get_engine().begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    except Exception:
        logger.exception("Could not create database tables")
    # Load the embedding model in the background so the first article search does not wait for it.
    warm_up = asyncio.create_task(asyncio.to_thread(articles_service.warm_up))
    yield
    warm_up.cancel()


def create_app() -> FastAPI:
    settings = get_settings()

    # Swagger / ReDoc / OpenAPI schema only locally, never on prod.
    docs = settings.is_local
    app = FastAPI(
        title="Proste Fakty",
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
        lifespan=lifespan,
    )

    app.include_router(health.router)
    app.include_router(ping.router, prefix="/api")
    app.include_router(sittings.router)
    app.include_router(articles.router)
    return app


app = create_app()
