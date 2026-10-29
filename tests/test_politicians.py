import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app
from app.routers import politicians as router
from app.services.faces import Face
from app.services.politicians import Match, Politician

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
    return client.post("/api/politicians/recognize", files={"photo": ("p.jpg", data, "image/jpeg")})


def face(box=(10, 20, 30, 40)) -> Face:
    return Face(box=box, score=0.9, embedding=np.ones(128) / np.sqrt(128))


def test_from_sejm_builds_links():
    p = Politician.from_sejm(MP, 10)
    assert p.photo_url == "https://api.sejm.gov.pl/sejm/term10/MP/1/photo"
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
