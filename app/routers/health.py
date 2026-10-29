import httpx
import psycopg
from fastapi import APIRouter, Response

from app.config import get_settings

router = APIRouter(tags=["health"])


@router.get("/health")
def health(response: Response):
    settings = get_settings()
    checks = {}

    try:
        with psycopg.connect(settings.database_url, connect_timeout=3) as conn:
            conn.execute("SELECT 1")
        checks["postgres"] = "ok"
    except Exception:
        checks["postgres"] = "error"

    try:
        httpx.get(f"{settings.qdrant_url}/readyz", timeout=3).raise_for_status()
        checks["qdrant"] = "ok"
    except Exception:
        checks["qdrant"] = "error"

    if any(v != "ok" for v in checks.values()):
        response.status_code = 503
    return checks
