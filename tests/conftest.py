import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.session import get_session
from app.main import create_app
from app.services.sejm_client import get_sejm_client


@pytest.fixture
def client_for(monkeypatch):
    def make(app_env: str) -> TestClient:
        monkeypatch.setenv("APP_ENV", app_env)
        get_settings.cache_clear()
        return TestClient(create_app())

    yield make
    get_settings.cache_clear()


@pytest.fixture
def api(client_for) -> TestClient:
    """Local app with the DB session and the Sejm client stubbed out; service functions are monkeypatched per test.

    Not entered as a context manager, so the lifespan (create_all, embedding warm-up) does not run.
    """
    client = client_for("local")

    async def fake_session():
        yield object()

    client.app.dependency_overrides[get_session] = fake_session
    client.app.dependency_overrides[get_sejm_client] = lambda: object()
    return client
