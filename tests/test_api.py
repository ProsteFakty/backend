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


def test_swagger_lists_ported_endpoints(client_for):
    paths = client_for("local").get("/openapi.json").json()["paths"]
    assert set(paths["/api/v1/sittings"]) == {"get"}
    assert set(paths["/api/v1/sittings/{number}"]) == {"get"}
    assert set(paths["/api/v1/articles/search"]) == {"post"}
    assert set(paths["/api/v1/articles/{slug}"]) == {"get"}
