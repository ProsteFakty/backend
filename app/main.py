from fastapi import FastAPI

from app.config import get_settings
from app.routers import health, ping, politicians


def create_app() -> FastAPI:
    settings = get_settings()

    # Swagger / ReDoc / OpenAPI schema only locally, never on prod.
    docs = settings.is_local
    app = FastAPI(
        title="Proste Fakty",
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
    )

    app.include_router(health.router)
    app.include_router(ping.router, prefix="/api")
    app.include_router(politicians.router, prefix="/api")
    return app


app = create_app()
