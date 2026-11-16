from datetime import date

import httpx
import pytest
from qdrant_client.http.exceptions import ResponseHandlingException

from app.schemas.articles import ActInfo, ArticleDetail, ArticleReference, ArticleSearchHit, ArticleSearchResponse
from app.schemas.sittings import Page, SittingDetail, SittingListItem
from app.services import articles as articles_service
from app.services import sittings as sittings_service
from app.services.articles import preview

SITTING = SittingListItem(
    number=30,
    title="30. Posiedzenie Sejmu RP",
    start_date=date(2026, 3, 3),
    end_date=date(2026, 3, 5),
    status="finished",
    description="Zdanie pierwsze. Zdanie drugie.",
    votings_count=12,
)

HIT = ArticleSearchHit(
    slug="ustawa_o_podatku_akcyzowym_2008_12_06_art_99b",
    act_key="ustawa_o_podatku_akcyzowym_2008_12_06",
    act_title="Ustawa o podatku akcyzowym",
    article_number="99b",
    title="Stawka akcyzy na płyn do e-papierosów",
    summary="Akcyza na płyn do papierosów elektronicznych wynosi 0,55 zł za mililitr.",
    text_preview="1. Produkcją płynu do papierosów elektronicznych…",
    match=["semantic", "text"],
    score=0.0328,
)


# --- sittings ---


def test_list_sittings_returns_page(api, monkeypatch):
    calls = {}

    async def fake_list(session, sejm, term, page, page_size):
        calls.update(term=term, page=page, page_size=page_size)
        return Page(items=[SITTING], page=page, page_size=page_size, total=1, pages=1)

    monkeypatch.setattr(sittings_service, "list_sittings", fake_list)

    resp = api.get("/api/v1/sittings", params={"page": 2, "page_size": 5})

    assert resp.status_code == 200
    body = resp.json()
    assert body["items"][0]["number"] == 30
    assert body["items"][0]["start_date"] == "2026-03-03"
    assert (body["page"], body["page_size"], body["total"], body["pages"]) == (2, 5, 1, 1)
    assert calls == {"term": 10, "page": 2, "page_size": 5}


def test_list_sittings_defaults_and_validation(api, monkeypatch):
    async def fake_list(session, sejm, term, page, page_size):
        return Page(items=[], page=page, page_size=page_size, total=0, pages=0)

    monkeypatch.setattr(sittings_service, "list_sittings", fake_list)

    assert api.get("/api/v1/sittings").json()["page_size"] == 3
    assert api.get("/api/v1/sittings", params={"page": 0}).status_code == 422
    assert api.get("/api/v1/sittings", params={"page_size": 101}).status_code == 422


def test_list_sittings_sejm_down_is_502(api, monkeypatch):
    async def fake_list(*args):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(sittings_service, "list_sittings", fake_list)

    assert api.get("/api/v1/sittings").status_code == 502


def test_get_sitting_returns_detail(api, monkeypatch):
    async def fake_get(session, sejm, term, number):
        return SittingDetail(**SITTING.model_dump(), turnout=0.9, source_url="https://sejm.gov.pl/x", bills=[])

    monkeypatch.setattr(sittings_service, "get_sitting", fake_get)

    resp = api.get("/api/v1/sittings/30")

    assert resp.status_code == 200
    assert resp.json()["turnout"] == 0.9
    assert resp.json()["bills"] == []


def test_get_sitting_not_found_is_404(api, monkeypatch):
    async def fake_get(*args):
        return None

    monkeypatch.setattr(sittings_service, "get_sitting", fake_get)

    assert api.get("/api/v1/sittings/999").status_code == 404
    assert api.get("/api/v1/sittings/abc").status_code == 422


def test_get_sitting_sejm_down_is_502(api, monkeypatch):
    async def fake_get(*args):
        raise httpx.ReadTimeout("slow")

    monkeypatch.setattr(sittings_service, "get_sitting", fake_get)

    assert api.get("/api/v1/sittings/30").status_code == 502


# --- articles ---


def test_search_articles_returns_page(api, monkeypatch):
    calls = {}

    async def fake_search(session, query, limit, offset, act_key):
        calls.update(query=query, limit=limit, offset=offset, act_key=act_key)
        return ArticleSearchResponse(total=1, limit=limit, offset=offset, items=[HIT])

    monkeypatch.setattr(articles_service, "search", fake_search)

    resp = api.post("/api/v1/articles/search", json={"query": "akcyza na e-papierosy"})

    assert resp.status_code == 200
    assert resp.json() == {"total": 1, "limit": 10, "offset": 0, "items": [HIT.model_dump()]}
    assert calls == {"query": "akcyza na e-papierosy", "limit": 10, "offset": 0, "act_key": None}

    api.post("/api/v1/articles/search", json={"query": "akcyza", "limit": 5, "offset": 10, "act_key": HIT.act_key})
    assert calls == {"query": "akcyza", "limit": 5, "offset": 10, "act_key": HIT.act_key}


def test_search_articles_validates_query(api):
    assert api.post("/api/v1/articles/search", json={"query": "a"}).status_code == 422
    assert api.post("/api/v1/articles/search", json={}).status_code == 422
    assert api.post("/api/v1/articles/search", json={"query": "akcyza", "limit": 51}).status_code == 422
    assert api.post("/api/v1/articles/search", json={"query": "akcyza", "offset": -1}).status_code == 422


def test_search_articles_qdrant_down_is_503(api, monkeypatch):
    """Without Qdrant the search still answers from Postgres; 503 only when that finds nothing either."""

    async def nothing(*args, **kwargs):
        return []

    def qdrant_down(*args, **kwargs):
        raise ResponseHandlingException(ConnectionError("down"))

    monkeypatch.setattr(articles_service, "find_referenced_articles", nothing)
    monkeypatch.setattr(articles_service, "search_text", nothing)
    monkeypatch.setattr(articles_service, "search_semantic", qdrant_down)

    assert api.post("/api/v1/articles/search", json={"query": "akcyza"}).status_code == 503


def test_get_article_returns_detail(api, monkeypatch):
    detail = ArticleDetail(
        slug=HIT.slug,
        number="99b",
        position=120,
        act=ActInfo(
            act_key=HIT.act_key, short_title=HIT.act_title, title="Ustawa z dnia 6 grudnia 2008 r. o podatku akcyzowym",
            act_type="ustawa", act_date=date(2008, 12, 6),
        ),
        title=HIT.title,
        summary="Streszczenie.",
        text="Art. 99b. ...",
        repealed=False,
        references=[
            ArticleReference(
                slug="ustawa_o_vat_2004_03_11_art_5", text="art. 5", same_act=False, available=False,
                act_title=None, article_number=None, title=None,
            )
        ],
    )

    async def fake_get(session, slug):
        return detail if slug == HIT.slug else None

    monkeypatch.setattr(articles_service, "get_article", fake_get)

    resp = api.get(f"/api/v1/articles/{HIT.slug}")
    assert resp.status_code == 200
    assert resp.json()["act"]["act_date"] == "2008-12-06"
    assert resp.json()["references"][0]["available"] is False

    assert api.get("/api/v1/articles/missing").status_code == 404


def test_preview_cuts_at_word_boundary():
    assert preview("**Krótko** o   tym.") == "Krótko o tym."
    assert preview("jeden dwa trzy cztery", limit=12) == "jeden dwa…"
