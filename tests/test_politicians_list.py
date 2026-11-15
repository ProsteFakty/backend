"""GET /api/v1/politicians and /api/v1/politicians/clubs (docs/specs/politicians-search.md)."""
import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app
from app.services import politicians as service
from app.services.politicians import Club, Politician, polish_sort_key


def mp(mp_id, first, last, club="KO", active=True, district_num=13, district="Kraków") -> Politician:
    return Politician.from_sejm(
        {
            "id": mp_id,
            "firstName": first,
            "lastName": last,
            "firstLastName": f"{first} {last}",
            "club": club,
            "districtNum": district_num,
            "districtName": district,
            "active": active,
        },
        10,
    )


MPS = [
    mp(1, "Jan", "Maj", club="PiS"),
    mp(2, "Adam", "Łęcki", club="RozwojPlus"),
    mp(3, "Anna", "Lis"),
    mp(4, "Ewa", "Zając", club="niez."),
    mp(5, "Piotr", "Lis", active=False),
    mp(6, "Józef", "Śliwa", club=None),
    mp(7, "Barbara", "Kowalczyk", club="PiS"),
]
CLUBS = [
    Club(id="KO", name="Klub Parlamentarny Koalicja Obywatelska"),
    Club(id="PiS", name="Klub Parlamentarny Prawo i Sprawiedliwość"),
    Club(id="niez.", name="Posłowie niezrzeszeni"),
    Club(id="Lewica", name="Lewica"),  # no MPs -> omitted
]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(service, "list_politicians", lambda: MPS)
    monkeypatch.setattr(service, "list_clubs", lambda: CLUBS)
    get_settings.cache_clear()
    yield TestClient(create_app())
    get_settings.cache_clear()


def names(client, **params) -> list[str]:
    return [p["full_name"] for p in client.get("/api/v1/politicians", params=params).json()["items"]]


def test_from_sejm_maps_district_number():
    assert mp(1, "Jan", "Maj", district_num=15).district_number == 15


def test_polish_sort_key():
    words = ["Maj", "łęcki", "Lis", "Zając", "Żak", "Źrebiec", "Ćma", "Cap", "Dąb"]
    assert sorted(words, key=polish_sort_key) == ["Cap", "Ćma", "Dąb", "Lis", "łęcki", "Maj", "Zając", "Źrebiec", "Żak"]


def test_list_active_sorted_by_last_name_polish(client):
    body = client.get("/api/v1/politicians").json()
    assert [p["full_name"] for p in body["items"]] == [
        "Barbara Kowalczyk",
        "Anna Lis",
        "Adam Łęcki",
        "Jan Maj",
        "Józef Śliwa",
        "Ewa Zając",
    ]
    assert (body["total"], body["page"], body["page_size"], body["pages"]) == (6, 1, 30, 1)
    assert [p["letter"] for p in body["items"]] == ["K", "L", "Ł", "M", "Ś", "Z"]


def test_list_include_inactive(client):
    assert names(client, include_inactive=True)[1:3] == ["Anna Lis", "Piotr Lis"]


def test_list_row_shape(client):
    row = client.get("/api/v1/politicians", params={"q": "łęcki"}).json()["items"][0]
    assert row == {
        "mp_id": 2,
        "first_name": "Adam",
        "last_name": "Łęcki",
        "full_name": "Adam Łęcki",
        "letter": "Ł",
        "club": "RozwojPlus",
        "club_label": "Rozwój Plus",
        "district_number": 13,
        "district_name": "Kraków",
        "active": True,
        "photo_url": "https://api.sejm.gov.pl/sejm/term10/MP/2/photo",
    }


def test_list_no_club_has_null_label(client):
    row = client.get("/api/v1/politicians", params={"q": "sliwa"}).json()["items"][0]
    assert row["club"] is None and row["club_label"] is None


@pytest.mark.parametrize(
    ("q", "expected"),
    [
        ("lecki", ["Adam Łęcki"]),  # without Polish characters
        ("a", ["Anna Lis", "Adam Łęcki"]),  # one letter is enough, first-name prefixes too
        ("an lis", ["Anna Lis"]),  # every word must match
        ("jan lis", []),
        ("   ", ["Barbara Kowalczyk", "Anna Lis", "Adam Łęcki", "Jan Maj", "Józef Śliwa", "Ewa Zając"]),
    ],
)
def test_list_query(client, q, expected):
    assert names(client, q=q) == expected


def test_list_paging(client):
    body = client.get("/api/v1/politicians", params={"page": 2, "page_size": 4}).json()
    assert [p["full_name"] for p in body["items"]] == ["Józef Śliwa", "Ewa Zając"]
    assert (body["total"], body["page"], body["page_size"], body["pages"]) == (6, 2, 4, 2)


def test_list_page_past_end_is_empty(client):
    body = client.get("/api/v1/politicians", params={"page": 9}).json()
    assert body["items"] == [] and body["total"] == 6


def test_list_default_page_size_is_30(client, monkeypatch):
    many = [mp(i, "Jan", f"Nowak{i:03d}") for i in range(1, 46)]
    monkeypatch.setattr(service, "list_politicians", lambda: many)
    body = client.get("/api/v1/politicians").json()
    assert (len(body["items"]), body["total"], body["pages"]) == (30, 45, 2)


@pytest.mark.parametrize("params", [{"page": 0}, {"page_size": 0}, {"page_size": 101}])
def test_list_invalid_paging(client, params):
    assert client.get("/api/v1/politicians", params=params).status_code == 422


def test_list_query_too_long(client):
    assert client.get("/api/v1/politicians", params={"q": "a" * 101}).status_code == 422


def test_list_club_filter(client):
    assert names(client, club="PiS") == ["Barbara Kowalczyk", "Jan Maj"]
    assert names(client, club="PiS", q="jan") == ["Jan Maj"]
    assert names(client, club="Nope") == []


def test_clubs(client):
    body = client.get("/api/v1/politicians/clubs").json()
    assert body == [
        {"id": "PiS", "label": "PiS", "name": "Klub Parlamentarny Prawo i Sprawiedliwość", "members_count": 2},
        {"id": "KO", "label": "KO", "name": "Klub Parlamentarny Koalicja Obywatelska", "members_count": 1},
        # missing from the Sejm club list: name falls back to the label
        {"id": "RozwojPlus", "label": "Rozwój Plus", "name": "Rozwój Plus", "members_count": 1},
        {"id": "niez.", "label": "Niezrzeszeni", "name": "Posłowie niezrzeszeni", "members_count": 1},
    ]


def test_clubs_counts_match_list(client):
    for include_inactive in (False, True):
        params = {"include_inactive": include_inactive}
        for c in client.get("/api/v1/politicians/clubs", params=params).json():
            total = client.get("/api/v1/politicians", params={**params, "club": c["id"], "page_size": 1}).json()["total"]
            assert total == c["members_count"]


def test_profile_route_still_works(client):
    assert client.get("/api/v1/politicians/3").json()["full_name"] == "Anna Lis"


def test_club_label_fallback():
    assert service.club_label("Nowy_Klub") == "Nowy Klub"


@pytest.fixture
def sejm_down(monkeypatch):
    def fail():
        raise httpx.ConnectError("down")

    monkeypatch.setattr(service, "list_politicians", fail)
    monkeypatch.setattr(service, "list_clubs", fail)
    get_settings.cache_clear()
    yield TestClient(create_app())
    get_settings.cache_clear()


def test_sejm_down_without_cache_is_502(sejm_down):
    assert sejm_down.get("/api/v1/politicians").status_code == 502
    assert sejm_down.get("/api/v1/politicians/clubs").status_code == 502


def test_cache_serves_stale_value_when_refresh_fails(monkeypatch):
    calls = []

    def fetch():
        calls.append(1)
        if len(calls) > 1:
            raise httpx.ConnectError("down")
        return ["fresh"]

    cached = service._Cached(fetch)
    assert cached.get() == ["fresh"]
    monkeypatch.setattr(service, "MP_LIST_TTL_SECONDS", -1)  # force refresh
    assert cached.get() == ["fresh"]
    assert len(calls) == 2


def test_cache_raises_when_nothing_cached():
    def fetch():
        raise httpx.ConnectError("down")

    with pytest.raises(httpx.HTTPError):
        service._Cached(fetch).get()
