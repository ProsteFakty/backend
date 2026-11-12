import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app
from app.routers import politicians as router
from app.services.faces import Face
from app.services.politicians import Match, Politician, SejmUnavailableError

MP = {
    "id": 1,
    "firstName": "Andrzej",
    "lastName": "Adamczyk",
    "firstLastName": "Andrzej Adamczyk",
    "club": "PiS",
    "districtName": "Kraków",
    "active": True,
}


class FakeEngine:
    def __init__(self, faces):
        self._faces = faces

    def faces(self, image):
        return self._faces


@pytest.fixture
def client():
    get_settings.cache_clear()
    yield TestClient(create_app())
    get_settings.cache_clear()


def jpeg() -> bytes:
    return cv2.imencode(".jpg", np.zeros((50, 50, 3), np.uint8))[1].tobytes()


def post(client, data: bytes):
    return client.post("/api/v1/politicians/recognize", files={"photo": ("p.jpg", data, "image/jpeg")})


def face(box=(10, 20, 30, 40)) -> Face:
    return Face(box=box, score=0.9, embedding=np.ones(128) / np.sqrt(128))


def test_from_sejm_builds_links():
    p = Politician.from_sejm(MP, 10)
    assert p.photo_url == "/api/v1/politicians/1/photo"
    assert p.profile_url == "https://www.sejm.gov.pl/Sejm10.nsf/posel.xsp?id=001&type=A"
    assert p.point_id == 10001


def test_rejects_non_image(client):
    assert post(client, b"not an image").status_code == 400


def test_match_above_threshold(client, monkeypatch):
    monkeypatch.setattr(router, "get_engine", lambda: FakeEngine([face()]))
    monkeypatch.setattr(
        router, "search_faces", lambda e: [Match(politician=Politician.from_sejm(MP, 10), similarity=0.7)]
    )
    body = post(client, jpeg()).json()
    assert body["faces"][0]["box"] == [10, 20, 30, 40]
    assert body["faces"][0]["politician"]["full_name"] == "Andrzej Adamczyk"
    assert body["faces"][0]["similarity"] == 0.7


def test_no_match_below_threshold(client, monkeypatch):
    monkeypatch.setattr(router, "get_engine", lambda: FakeEngine([face()]))
    monkeypatch.setattr(
        router, "search_faces", lambda e: [Match(politician=Politician.from_sejm(MP, 10), similarity=0.2)]
    )
    body = post(client, jpeg()).json()
    assert body["faces"][0]["politician"] is None
    assert body["faces"][0]["similarity"] == 0.2


def test_no_faces(client, monkeypatch):
    monkeypatch.setattr(router, "get_engine", lambda: FakeEngine([]))
    assert post(client, jpeg()).json() == {"faces": []}


MPS = [
    Politician.from_sejm(MP, 10),
    Politician.from_sejm({**MP, "id": 2, "firstName": "Adam", "lastName": "Łęcki", "firstLastName": "Adam Łęcki"}, 10),
    Politician.from_sejm({**MP, "id": 3, "firstName": "Jan", "lastName": "Kowalski", "firstLastName": "Jan Kowalski"}, 10),
]


@pytest.fixture
def mps(monkeypatch):
    monkeypatch.setattr("app.services.politicians.list_politicians", lambda: MPS)


def test_search_last_name_hits_first(client, mps):
    body = client.get("/api/v1/politicians/search", params={"q": "adam"}).json()
    assert [p["full_name"] for p in body] == ["Andrzej Adamczyk", "Adam Łęcki"]
    assert set(body[0]) == {"mp_id", "full_name", "club", "active", "photo_url"}


def test_search_without_polish_characters(client, mps):
    body = client.get("/api/v1/politicians/search", params={"q": "lecki"}).json()
    assert [p["mp_id"] for p in body] == [2]


def test_search_all_words_must_match(client, mps):
    assert client.get("/api/v1/politicians/search", params={"q": "jan kow"}).json()[0]["mp_id"] == 3
    assert client.get("/api/v1/politicians/search", params={"q": "jan adam"}).json() == []


def test_search_query_too_short(client, mps):
    assert client.get("/api/v1/politicians/search", params={"q": "a"}).status_code == 422


def test_profile(client, mps):
    assert client.get("/api/v1/politicians/3").json()["full_name"] == "Jan Kowalski"
    assert client.get("/api/v1/politicians/999").status_code == 404


def test_photo_proxy(client, monkeypatch):
    monkeypatch.setattr(router, "get_photo", lambda mp_id: b"\xff\xd8jpeg" if mp_id == 1 else None)

    resp = client.get("/api/v1/politicians/1/photo")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    assert resp.content == b"\xff\xd8jpeg"
    assert client.get("/api/v1/politicians/2/photo").status_code == 404


def test_photo_url_points_to_our_backend(client, mps, monkeypatch):
    expected = "http://testserver/api/v1/politicians/3/photo"
    assert client.get("/api/v1/politicians/3").json()["photo_url"] == expected
    assert client.get("/api/v1/politicians/search", params={"q": "kowal"}).json()[0]["photo_url"] == expected

    # old face-index entries still carry the Sejm API URL
    stale = MPS[2].model_copy(update={"photo_url": "https://api.sejm.gov.pl/sejm/term10/MP/3/photo"})
    monkeypatch.setattr(router, "get_engine", lambda: FakeEngine([face()]))
    monkeypatch.setattr(router, "search_faces", lambda e: [Match(politician=stale, similarity=0.7)])
    assert post(client, jpeg()).json()["faces"][0]["politician"]["photo_url"] == expected


def test_sejm_outage_is_503(client, monkeypatch):
    def down(*args):
        raise SejmUnavailableError("down")

    monkeypatch.setattr(router, "get_photo", down)
    monkeypatch.setattr(router, "get_politician", down)
    monkeypatch.setattr(router, "search_politicians", down)

    assert client.get("/api/v1/politicians/1/photo").status_code == 503
    assert client.get("/api/v1/politicians/1").status_code == 503
    assert client.get("/api/v1/politicians/search", params={"q": "kowal"}).status_code == 503


def test_mp_list_falls_back_to_saved_copy(tmp_path, monkeypatch):
    import httpx

    from app.services import politicians as service

    monkeypatch.setenv("SEJM_CACHE_DIR", str(tmp_path))
    get_settings.cache_clear()
    monkeypatch.setattr(service, "_mps", [])

    monkeypatch.setattr(service.httpx, "get", lambda *a, **k: httpx.Response(200, json=[MP], request=httpx.Request("GET", "x")))
    assert [p.full_name for p in service.list_politicians()] == ["Andrzej Adamczyk"]

    # API down after a restart (nothing in memory): the copy on disk is used
    monkeypatch.setattr(service, "_mps", [])
    monkeypatch.setattr(service.httpx, "get", lambda *a, **k: httpx.Response(503, request=httpx.Request("GET", "x")))
    assert [p.full_name for p in service.list_politicians()] == ["Andrzej Adamczyk"]
    get_settings.cache_clear()
