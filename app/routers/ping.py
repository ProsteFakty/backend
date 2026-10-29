from fastapi import APIRouter

from app.config import get_settings

router = APIRouter(tags=["test"])


@router.get("/ping")
def ping():
    """Test endpoint: confirms the API is reachable through Caddy."""
    return {"message": "pong", "env": get_settings().app_env}
