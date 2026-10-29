import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app


@pytest.fixture
def client_for(monkeypatch):
    def make(app_env: str) -> TestClient:
        monkeypatch.setenv("APP_ENV", app_env)
        get_settings.cache_clear()
        return TestClient(create_app())

    yield make
    get_settings.cache_clear()


def test_ping(client_for):
    resp = client_for("local").get("/api/ping")
    assert resp.status_code == 200
    assert resp.json() == {"message": "pong", "env": "local"}


def test_docs_enabled_locally(client_for):
    client = client_for("local")
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200


def test_docs_disabled_on_prod(client_for):
    client = client_for("prod")
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404
