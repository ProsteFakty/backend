import asyncio
from contextlib import nullcontext
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from anthropic.types import ServerToolUsage, Usage

from app.config import get_settings
from app.db.models import MpNewsCache
from app.services import mp_news as service
from app.services.mp_news import MpNews, NewsItem, NewsSource, build_items
from app.services.politicians import Politician
from app.utils import claude_client
from app.utils.claude_client import BudgetExceeded, ClaudeError, cost_usd
from app.utils.claude_news import ClaudeNews, SearchResult, parse_items, search_mp_news

TODAY = date(2026, 11, 26)
NOW = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)


def _item(**overrides) -> dict:
    return {
        "headline": "Kowalska krytykuje projekt PCC w TVN24",
        "summary": "Według TVN24 posłanka Kowalska skrytykowała projekt zmian w podatku PCC.",
        "date": "2026-11-21",
        "category": "statement",
        "source_urls": ["https://tvn24.pl/a"],
        **overrides,
    }


def _results(*urls: str) -> list[SearchResult]:
    return [SearchResult(url=u, title=f"Tytuł {u}") for u in urls]


def _build(news: ClaudeNews, **kwargs) -> list[NewsItem]:
    return build_items(news, last_name="Kowalska", today=TODAY, **kwargs)


# --- validation ---


def test_valid_item_keeps_search_result_url_and_title():
    news = ClaudeNews(items=[_item(source_urls=["https://TVN24.pl/a/#comments"])], results=_results("https://tvn24.pl/a"))

    [item] = _build(news)

    assert item.headline == "Kowalska krytykuje projekt PCC w TVN24"
    assert item.date == date(2026, 11, 21)
    assert item.category == "statement"
    assert item.sources == [NewsSource(url="https://tvn24.pl/a", domain="tvn24.pl", title="Tytuł https://tvn24.pl/a")]


@pytest.mark.parametrize(
    "overrides",
    [
        {"date": "2026-10-20"},  # older than 30 days
        {"date": "2026-11-27"},  # future
        {"date": None},
        {"date": "wczoraj"},
        {"headline": "Spór o PCC", "summary": "Nowak skrytykował projekt."},  # not about this MP
        {"headline": ""},
        {"source_urls": ["https://made-up.pl/x"]},  # not returned by the search
        {"source_urls": []},
        {"source_urls": "https://tvn24.pl/a"},  # not a list
    ],
)
def test_invalid_items_are_dropped(overrides):
    assert _build(ClaudeNews(items=[_item(**overrides)], results=_results("https://tvn24.pl/a"))) == []


def test_declined_last_name_matches():
    news = ClaudeNews(
        items=[_item(headline="Wywiad z posłanką", summary="Pudelek pisze o wywiadzie posłanki Kowalskiej.")],
        results=_results("https://tvn24.pl/a"),
    )

    assert len(_build(news)) == 1


def test_blocked_domains_are_dropped():
    news = ClaudeNews(items=[_item(source_urls=["https://spam.example.com/x"])], results=_results("https://spam.example.com/x"))

    assert _build(news, blocked_domains=["example.com"]) == []


def test_sources_dedupe_sort_cap_and_unknown_category():
    days = ["2026-11-01", "2026-11-25", "2026-11-10", "2026-11-20", "2026-11-05", "2026-11-15"]
    items = [_item(date=d, source_urls=["https://tvn24.pl/a", f"https://site{i}.pl/n"]) for i, d in enumerate(days)]
    items[1]["category"] = "Scandal"
    news = ClaudeNews(items=items, results=_results("https://tvn24.pl/a", *(f"https://site{i}.pl/n" for i in range(6))))

    result = _build(news)

    assert [i.date.isoformat() for i in result] == ["2026-11-25", "2026-11-20", "2026-11-15", "2026-11-10", "2026-11-05"]
    assert [s.domain for s in result[-1].sources] == ["site4.pl"]  # tvn24.pl went to the first item only
    assert result[0].category == "other"


def test_long_headline_is_clipped_at_a_word():
    news = ClaudeNews(items=[_item(headline="Kowalska " + "słowo " * 30)], results=_results("https://tvn24.pl/a"))

    [item] = _build(news)

    assert len(item.headline) <= service.HEADLINE_MAX
    assert item.headline.endswith("słowo…")


# --- Claude answer parsing and the search loop ---


@pytest.mark.parametrize(
    "text",
    [
        '{"items": [{"headline": "a"}]}',
        'Oto wynik:\n```json\n{"items": [{"headline": "a"}]}\n```',
        'Szukam {nie json}. {"items": [{"headline": "a"}, "śmieć"]}',
        '{"items": [{"headline": "szkic"}]} Poprawiam: {"items": [{"headline": "a"}]}',
    ],
)
def test_parse_items(text):
    assert parse_items(text) == [{"headline": "a"}]


def test_parse_items_without_json_raises():
    with pytest.raises(ClaudeError):
        parse_items("Nic nie znalazłem.")


def _usage(searches: int = 0, inp: int = 1000, out: int = 100) -> Usage:
    return Usage(input_tokens=inp, output_tokens=out, server_tool_use=ServerToolUsage(web_search_requests=searches, web_fetch_requests=0))


def _search_block(*urls):
    return SimpleNamespace(type="web_search_tool_result", content=[SimpleNamespace(url=u, title=u) for u in urls])


def _text(t):
    return SimpleNamespace(type="text", text=t)


def _run_search(responses):
    sent, usages = [], []

    class FakeMessages:
        async def create(self, **kwargs):
            sent.append(kwargs)
            return responses.pop(0)

    async def on_usage(u):
        usages.append(u)

    result = asyncio.run(
        search_mp_news(
            SimpleNamespace(messages=FakeMessages()), model="claude-haiku-5-5", effort="low",
            full_name="Anna Kowalska", club="KO", district="Kraków", today=NOW, since=NOW - timedelta(days=30),
            max_items=5, max_searches=3, blocked_domains=["spam.pl"], on_usage=on_usage,
        )
    )
    return result, sent, usages


def test_search_collects_results_across_pause_turn():
    paused = SimpleNamespace(stop_reason="pause_turn", usage=_usage(2),
                             content=[_text("Szukam."), _search_block("https://a.pl/1")])
    final = SimpleNamespace(
        stop_reason="end_turn", usage=_usage(1),
        content=[_search_block("https://b.pl/2"), SimpleNamespace(type="web_search_tool_result",
                 content=SimpleNamespace(error_code="max_uses_exceeded")), _text('{"items": '), _text("[]}")],
    )

    result, sent, usages = _run_search([paused, final])

    assert result.items == []
    assert [r.url for r in result.results] == ["https://a.pl/1", "https://b.pl/2"]
    assert len(usages) == 2
    assert sent[1]["messages"][-1] == {"role": "assistant", "content": paused.content}
    tool = sent[0]["tools"][0]
    assert tool["type"] == "web_search_20250305" and tool["max_uses"] == 3 and tool["blocked_domains"] == ["spam.pl"]
    assert sent[0]["model"] == "claude-haiku-5-5" and sent[0]["output_config"] == {"effort": "low"}
    assert "Anna Kowalska" in sent[0]["messages"][0]["content"]


def test_search_refusal_raises_but_usage_is_recorded():
    with pytest.raises(ClaudeError):
        _run_search([SimpleNamespace(stop_reason="refusal", usage=_usage(), content=[])])


# --- cost and budget ---


def test_cost_tokens_and_searches():
    assert cost_usd("claude-haiku-5-5", _usage(searches=3, inp=20_000, out=1_000)) == pytest.approx(
        20_000 * 0.10 / 1e6 + 1_000 * 0.50 / 1e6 + 3 * 0.01
    )
    # prompts over 100K tokens use the long-context price
    assert cost_usd("claude-haiku-5-5", _usage(inp=200_000, out=0)) == pytest.approx(200_000 * 0.50 / 1e6)


def test_cost_unknown_model_refuses():
    with pytest.raises(ClaudeError):
        cost_usd("claude-unknown", _usage())


def test_budget_exceeded_blocks_the_call(monkeypatch):
    async def spent(session, now=None):
        return 5.0

    monkeypatch.setattr(claude_client, "spent_this_month", spent)
    monkeypatch.setattr(service, "search_mp_news", lambda *a, **k: pytest.fail("must not call Claude"))

    with pytest.raises(BudgetExceeded):
        asyncio.run(service.fetch_news(object(), MP, "KO", NOW))


# --- freshness ---

MP = Politician(
    mp_id=7, term=10, first_name="Anna", last_name="Kowalska", full_name="Anna Kowalska", active=True,
    photo_url="/p", profile_url="https://sejm",
)
ITEM = NewsItem(
    headline="h", summary="s", date=date(2026, 11, 21), category="other",
    sources=[NewsSource(url="https://tvn24.pl/a", domain="tvn24.pl")],
)


class Store:
    def __init__(self):
        self.rows: dict[tuple[int, int], MpNewsCache] = {}

    def put(self, **values) -> MpNewsCache:
        row = MpNewsCache(term=MP.term, mp_id=MP.mp_id, status="unavailable", items=[],
                          generated_at=None, error_at=None, error=None, blocked=False)
        for key, value in values.items():
            setattr(row, key, value)
        self.rows[(MP.term, MP.mp_id)] = row
        return row

    async def load(self, session, term, mp_id):
        return self.rows.get((term, mp_id))

    async def store(self, session, term, mp_id, **values):
        row = self.rows.get((term, mp_id)) or self.put()
        for key, value in values.items():
            setattr(row, key, value)
        return row


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    get_settings.cache_clear()
    s = Store()
    monkeypatch.setattr(service, "_load", s.load)
    monkeypatch.setattr(service, "_store", s.store)
    monkeypatch.setattr(service, "_now", lambda: NOW)
    monkeypatch.setattr(service, "_open_session", lambda: nullcontext(object()))
    service._refreshing.clear()
    service._locks.clear()
    yield s
    get_settings.cache_clear()


@pytest.fixture
def fetches(monkeypatch):
    calls = []

    async def fake_fetch(session, mp, club, now):
        calls.append(club)
        await asyncio.sleep(0.01)
        return [ITEM]

    monkeypatch.setattr(service, "fetch_news", fake_fetch)
    return calls


def _get(*, concurrent: int = 1) -> list[MpNews]:
    async def run():
        results = await asyncio.gather(*(service.get_mp_news(object(), MP, "KO") for _ in range(concurrent)))
        await asyncio.gather(*list(service._tasks))  # let background refreshes finish
        return results

    return asyncio.run(run())


def test_first_view_fetches_once_and_stores(store, fetches):
    a, b = _get(concurrent=2)

    assert fetches == ["KO"]
    assert a == b
    assert a.status == "ok" and a.items == [ITEM] and a.generated_at == NOW
    assert store.rows[(10, 7)].status == "ok"


def test_fresh_row_is_served_without_a_call(store, fetches):
    store.put(status="ok", items=[ITEM.model_dump(mode="json")], generated_at=NOW - timedelta(hours=1))

    [result] = _get()

    assert fetches == []
    assert result.items == [ITEM]


def test_stale_row_is_served_and_refreshed_once_in_background(store, fetches):
    old = NOW - timedelta(hours=30)
    store.put(status="empty", items=[], generated_at=old)

    a, b = _get(concurrent=2)

    assert a.status == "empty" and a.generated_at == old  # served what was stored
    assert fetches == ["KO"]
    assert store.rows[(10, 7)].generated_at == NOW


def test_failure_keeps_old_row_and_backs_off(store, monkeypatch):
    old = NOW - timedelta(hours=30)
    store.put(status="ok", items=[ITEM.model_dump(mode="json")], generated_at=old)
    calls = []

    async def failing(session, mp, club, now):
        calls.append(1)
        raise ClaudeError("boom")

    monkeypatch.setattr(service, "fetch_news", failing)

    [result] = _get()
    assert result.items == [ITEM]
    row = store.rows[(10, 7)]
    assert row.generated_at == old and row.error_at == NOW and "boom" in row.error

    _get()  # within the backoff: no new call
    assert calls == [1]


def test_first_view_failure_is_unavailable_not_an_error(store, monkeypatch):
    async def failing(session, mp, club, now):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(service, "fetch_news", failing)

    [result] = _get()

    assert result.status == "unavailable" and result.items == [] and result.generated_at is None


def test_no_api_key_means_no_call(store, fetches, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    get_settings.cache_clear()

    [result] = _get()

    assert result.status == "unavailable"
    assert fetches == []


def test_blocked_row_is_never_refreshed(store, fetches):
    store.put(status="empty", items=[], generated_at=NOW - timedelta(days=400), blocked=True)

    [result] = _get()

    assert result.status == "empty"
    assert fetches == []


# --- endpoint ---


def test_news_endpoint(api, monkeypatch):
    monkeypatch.setattr(service, "news_subject", lambda mp_id: (MP, "Koalicja Obywatelska"))
    seen = {}

    async def fake_get(session, mp, club):
        seen.update(mp_id=mp.mp_id, club=club)
        return MpNews(mp_id=mp.mp_id, status="ok", generated_at=NOW, items=[ITEM])

    monkeypatch.setattr(service, "get_mp_news", fake_get)

    resp = api.get("/api/v1/politicians/7/news")

    assert resp.status_code == 200
    assert resp.json() == {
        "mp_id": 7,
        "status": "ok",
        "generated_at": "2026-11-26T12:00:00Z",
        "items": [
            {
                "headline": "h",
                "summary": "s",
                "date": "2026-11-21",
                "category": "other",
                "sources": [{"url": "https://tvn24.pl/a", "domain": "tvn24.pl", "title": None}],
            }
        ],
        "disclaimer": service.DISCLAIMER,
    }
    assert seen == {"mp_id": 7, "club": "Koalicja Obywatelska"}


def test_news_endpoint_unknown_mp(api, monkeypatch):
    monkeypatch.setattr(service, "news_subject", lambda mp_id: None)

    assert api.get("/api/v1/politicians/999/news").status_code == 404


def test_news_endpoint_sejm_down(api, monkeypatch):
    def down(mp_id):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(service, "news_subject", down)

    assert api.get("/api/v1/politicians/7/news").status_code == 502
