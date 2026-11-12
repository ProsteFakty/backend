from datetime import datetime

import httpx
import pytest

from app.db.models import Sitting
from app.schemas.mps import MpVoteOut
from app.schemas.sittings import Page
from app.services import mp_votes as service
from app.services.mp_votes import final_candidates, vote_value
from app.services.sittings import _build_voting, is_final

VOTE = MpVoteOut(
    sitting=45,
    voting_number=112,
    held_at=datetime(2026, 10, 17, 11, 32, 5),
    print_number="1702",
    title="Rządowy projekt ustawy o zmianie ustawy o podatku od czynności cywilnoprawnych",
    summary="Zdanie pierwsze. Zdanie drugie.",
    source_url="https://www.sejm.gov.pl/sejm10.nsf/PrzebiegProc.xsp?nr=1702",
    vote="for",
    club="KO",
    club_position="for",
)


def _voting_raw(number: int, *, kind="ELECTRONIC", description="całość projektu ustawy", topic=""):
    return {
        "votingNumber": number,
        "date": "2026-10-17T11:32:05",
        "kind": kind,
        "title": "Pkt. 3 Sprawozdanie Komisji (druki nr 1702 i 1750)",
        "topic": topic,
        "description": description,
        "yes": 2,
        "no": 1,
        "abstain": 0,
        "notParticipating": 1,
        "majorityVotes": 2,
    }


# --- endpoint contract ---


def test_mp_votes_returns_page(api, monkeypatch):
    calls = {}

    async def fake_list(session, sejm, term, mp_id, vote, page, page_size):
        calls.update(term=term, mp_id=mp_id, vote=vote, page=page, page_size=page_size)
        return Page(items=[VOTE], page=page, page_size=page_size, total=1, pages=1)

    monkeypatch.setattr(service, "list_mp_votes", fake_list)

    resp = api.get("/api/v1/mps/7/votes", params={"vote": "against", "page": 2, "page_size": 5})

    assert resp.status_code == 200
    item = resp.json()["items"][0]
    assert item == {
        "sitting": 45,
        "voting_number": 112,
        "held_at": "2026-10-17T11:32:05",
        "print_number": "1702",
        "title": VOTE.title,
        "summary": "Zdanie pierwsze. Zdanie drugie.",
        "source_url": VOTE.source_url,
        "vote": "for",
        "club": "KO",
        "club_position": "for",
    }
    assert calls == {"term": 10, "mp_id": 7, "vote": "against", "page": 2, "page_size": 5}


def test_mp_votes_defaults_and_validation(api, monkeypatch):
    calls = {}

    async def fake_list(session, sejm, term, mp_id, vote, page, page_size):
        calls.update(vote=vote, page=page, page_size=page_size)
        return Page(items=[], page=page, page_size=page_size, total=0, pages=0)

    monkeypatch.setattr(service, "list_mp_votes", fake_list)

    assert api.get("/api/v1/mps/7/votes").status_code == 200
    assert calls == {"vote": None, "page": 1, "page_size": 20}
    assert api.get("/api/v1/mps/7/votes", params={"vote": "za"}).status_code == 422
    assert api.get("/api/v1/mps/7/votes", params={"page": 0}).status_code == 422
    assert api.get("/api/v1/mps/7/votes", params={"page_size": 101}).status_code == 422
    assert api.get("/api/v1/mps/abc/votes").status_code == 422


def test_mp_votes_unknown_mp_is_404(api, monkeypatch):
    async def fake_list(*args):
        return None

    monkeypatch.setattr(service, "list_mp_votes", fake_list)

    assert api.get("/api/v1/mps/999/votes").status_code == 404


def test_mp_votes_sejm_down_is_502(api, monkeypatch):
    async def fake_list(*args):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(service, "list_mp_votes", fake_list)

    assert api.get("/api/v1/mps/7/votes").status_code == 502


def test_mp_votes_backfill_in_progress_is_503(api, monkeypatch):
    async def fake_list(*args):
        raise service.BackfillInProgress

    monkeypatch.setattr(service, "list_mp_votes", fake_list)

    resp = api.get("/api/v1/mps/7/votes")

    assert resp.status_code == 503
    assert resp.headers["retry-after"] == "30"


# --- pure helpers ---


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("YES", "for"), ("NO", "against"), ("ABSTAIN", "abstained"), ("ABSENT", "absent"), ("PRESENT", "absent")],
)
def test_vote_value_maps_sejm_votes(raw, expected):
    assert vote_value(raw) == expected


def test_is_final_detects_vote_on_whole_document():
    assert is_final("głosowanie nad całością projektu.", "całość projektu ustawy")
    assert is_final(None, "całość projektu ustawy")
    assert not is_final("wniosek o odrzucenie w całości projektu.", "")
    assert not is_final("poprawka 9", "wniosek mniejszości")
    assert not is_final(None, None)


def test_final_candidates_keeps_electronic_final_votings_only():
    listing = [
        _voting_raw(1, description="całość projektu"),
        _voting_raw(2, description="poprawka 3"),
        _voting_raw(3, kind="ON_LIST", description="całość projektu"),
        _voting_raw(4, description=None, topic="głosowanie nad całością projektu"),
    ]

    assert final_candidates(listing) == [1, 4]


# --- storing individual votes ---


def _sitting() -> Sitting:
    return Sitting(term=10, number=45, title="45. Posiedzenie", dates=[], votings_count=0)


def test_build_voting_stores_mp_votes_for_final_electronic_voting():
    raw = _voting_raw(112, description="całość projektu") | {
        "votes": [
            {"MP": 1, "club": "KO", "vote": "YES"},
            {"MP": 2, "club": "KO", "vote": "NO"},
            {"MP": 3, "club": "PiS", "vote": "ABSENT"},
        ]
    }

    voting = _build_voting(_sitting(), raw, None)

    assert voting.is_final
    assert {(v.mp_id, v.club, v.vote) for v in voting.mp_votes} == {
        (1, "KO", "for"),
        (2, "KO", "against"),
        (3, "PiS", "absent"),
    }


def test_build_voting_skips_mp_votes_for_amendments():
    raw = _voting_raw(113, description="poprawka 9") | {"votes": [{"MP": 1, "club": "KO", "vote": "YES"}]}

    voting = _build_voting(_sitting(), raw, None)

    assert not voting.is_final
    assert voting.mp_votes == []
    assert [c.club for c in voting.clubs] == ["KO"]  # club aggregates are still kept for every voting
