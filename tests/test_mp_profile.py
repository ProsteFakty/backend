"""GET /api/v1/politicians/{mp_id} profile extras (docs/specs/mp-profile.md)."""
import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app
from app.services import mp_profile, politicians
from app.services.mp_profile import Committee, VotingStats, club_name, gender
from app.services.politicians import Club, Politician


def mp(mp_id=5, first="Anna", last="Kowalczyk", club="KO", birth="1970-01-01", term=10) -> Politician:
    return Politician.from_sejm(
        {
            "id": mp_id,
            "firstName": first,
            "lastName": last,
            "firstLastName": f"{first} {last}",
            "club": club,
            "districtNum": 13,
            "districtName": "Kraków",
            "birthDate": birth,
            "active": True,
        },
        term,
    )


ANNA = mp()
JAN = mp(mp_id=6, first="Jan", last="Nowak", club="PiS")
CLUBS = [
    Club(
        id="KO",
        name="Klub Parlamentarny Koalicja Obywatelska - Platforma Obywatelska, Nowoczesna",
        member_functions={5: "wiceprzewodniczący"},
    ),
    Club(id="PiS", name="Klub Parlamentarny Prawo i Sprawiedliwość"),
]
COMMITTEES = [
    Committee(name_genitive="Komisji Zdrowia", member_functions={5: "przewodniczący", 6: "zastępca przewodniczącego"}),
    Committee(name_genitive="Komisji Etyki Poselskiej", member_functions={6: "przewodniczący"}),
]
# Anna was an MP in terms 8 (id 101), 9 (id 202) and 10 (id 5). A namesake with another birth date in term 7.
PAST_TERMS = {
    7: {("anna", "kowalczyk", "1980-05-05"): 77},
    8: {("anna", "kowalczyk", "1970-01-01"): 101},
    9: {("anna", "kowalczyk", "1970-01-01"): 202},
}
STATS = {(10, 5): VotingStats(voted=4880, votings=5232), (9, 202): VotingStats(voted=6000, votings=6100),
         (8, 101): VotingStats(voted=1000, votings=1000), (10, 6): VotingStats(voted=0, votings=0)}


@pytest.fixture
def sejm(monkeypatch):
    """Stubbed Sejm data; tests can break parts by replacing entries in `failing`."""
    failing: set[str] = set()

    def guard(name):
        if name in failing:
            raise httpx.ConnectError(name)

    def term_ids(term):
        guard(f"term{term}")
        return PAST_TERMS.get(term, {})

    def stats(term, mp_id):
        guard(f"stats{term}")
        return STATS[(term, mp_id)]

    def committees():
        guard("committees")
        return COMMITTEES

    def clubs():
        guard("clubs")
        return CLUBS

    monkeypatch.setattr(politicians, "list_politicians", lambda: [ANNA, JAN])
    monkeypatch.setattr(politicians, "list_clubs", clubs)
    monkeypatch.setattr(mp_profile, "list_committees", committees)
    monkeypatch.setattr(mp_profile, "_term_ids", term_ids)
    monkeypatch.setattr(mp_profile, "voting_stats", stats)
    return failing


@pytest.fixture
def client(sejm):
    get_settings.cache_clear()
    yield TestClient(create_app())
    get_settings.cache_clear()


def test_gender():
    assert gender("Anna") == "female"
    assert gender("Jan") == "male"
    assert gender("Bożenna") == "female"


@pytest.mark.parametrize(
    ("club_id", "official", "expected"),
    [
        ("KO", "Klub Parlamentarny Koalicja Obywatelska - Platforma Obywatelska, Nowoczesna", "Koalicja Obywatelska"),
        ("PiS", "Klub Parlamentarny Prawo i Sprawiedliwość", "Prawo i Sprawiedliwość"),
        ("PSL-TD", "Klub Parlamentarny Polskie Stronnictwo Ludowe - Trzecia Droga", "Polskie Stronnictwo Ludowe"),
        ("Razem", "Koło Poselskie Razem", "Razem"),
        ("Lewica", "Koalicyjny Klub Parlamentarny Lewicy (Nowa Lewica, PPS, Unia Pracy)", "Lewica"),
        ("niez.", "Posłowie niezrzeszeni", "Posłowie niezrzeszeni"),
        ("RozwojPlus", None, "Rozwój Plus"),  # official name unavailable -> short label
        (None, None, None),
    ],
)
def test_club_name(club_id, official, expected):
    assert club_name(club_id, official) == expected


def test_profile(client):
    body = client.get("/api/v1/politicians/5").json()
    assert body["full_name"] == "Anna Kowalczyk"
    assert body["district_number"] == 13  # Politician fields stay
    assert body["gender"] == "female"
    assert (body["club"], body["club_label"], body["club_name"]) == ("KO", "KO", "Koalicja Obywatelska")
    # Feminized, capitalized, chair before deputy, committee before club
    assert body["functions"] == ["Przewodnicząca Komisji Zdrowia", "Wiceprzewodnicząca klubu KO"]
    assert body["terms"] == [8, 9, 10]  # term-7 namesake has another birth date
    assert body["attendance_percent"] == 93.3
    assert (body["votes_cast"], body["votings_count"]) == (4880, 5232)
    assert body["votes_cast_all_terms"] == 4880 + 6000 + 1000
    assert body["office_address"] is None


def test_profile_male_keeps_titles_and_orders_chairs_first(client):
    body = client.get("/api/v1/politicians/6").json()
    assert body["gender"] == "male"
    assert body["functions"] == ["Przewodniczący Komisji Etyki Poselskiej", "Zastępca przewodniczącego Komisji Zdrowia"]
    assert body["terms"] == [10]
    assert body["attendance_percent"] is None  # no votings yet
    assert body["votes_cast"] == 0


def test_profile_without_functions(client, monkeypatch):
    monkeypatch.setattr(mp_profile, "list_committees", lambda: [])
    monkeypatch.setattr(politicians, "list_clubs", lambda: [])
    body = client.get("/api/v1/politicians/5").json()
    assert body["functions"] == []
    assert body["club_name"] == "KO"  # no official name -> label


def test_current_stats_failure(client, sejm):
    sejm.add("stats10")
    body = client.get("/api/v1/politicians/5").json()
    assert (body["attendance_percent"], body["votes_cast"], body["votings_count"]) == (None, None, None)
    assert body["votes_cast_all_terms"] is None
    assert body["terms"] == [8, 9, 10]


def test_past_term_failure_makes_all_terms_sum_unknown(client, sejm):
    sejm.add("term8")
    body = client.get("/api/v1/politicians/5").json()
    assert body["terms"] == [9, 10]
    assert body["votes_cast_all_terms"] is None
    assert body["votes_cast"] == 4880


def test_committees_and_clubs_failure(client, sejm):
    sejm.update({"committees", "clubs"})
    body = client.get("/api/v1/politicians/5").json()
    assert body["functions"] == []
    assert body["club_name"] == "KO"


def test_profile_404(client):
    assert client.get("/api/v1/politicians/999").status_code == 404


def test_profile_502_when_mp_list_unavailable(client, monkeypatch):
    def down():
        raise httpx.ConnectError("down")

    monkeypatch.setattr(politicians, "list_politicians", down)
    assert client.get("/api/v1/politicians/5").status_code == 502


def test_fetch_stats_sums_days(monkeypatch):
    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return [{"numVoted": 7, "numVotings": 8, "numMissed": 1}, {"numVoted": 5, "numVotings": 5}]

    monkeypatch.setattr(httpx, "get", lambda url, timeout: Resp())
    assert mp_profile._fetch_stats(10, 1) == VotingStats(voted=12, votings=13)


def test_fetch_term_ids_keys_by_name_and_birth_date(monkeypatch):
    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return [{"id": 7, "firstName": "Anna", "lastName": "Kowalczyk", "birthDate": "1970-01-01"}]

    monkeypatch.setattr(httpx, "get", lambda url, timeout: Resp())
    assert mp_profile._fetch_term_ids(9) == {("anna", "kowalczyk", "1970-01-01"): 7}
