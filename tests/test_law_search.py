import asyncio
from datetime import date
from types import SimpleNamespace

import pytest

from app.pipeline.act_parser import is_repealed
from app.pipeline.chunks import article_chunks, split_summary
from app.pipeline.grounding import ungrounded
from app.schemas.acts import ActArticleItem, ActDetail, ActListItem
from app.services import acts as acts_service
from app.services import articles as articles_service
from app.utils.gemini_client.tasks import UngroundedSummaryError, summarize_article
from app.utils.gemini_client.tasks.article_summary import ArticleSummary

ART_148 = "§ 1. Kto zabija człowieka, podlega karze pozbawienia wolności na czas nie krótszy od lat 10."


# --- grounding ---


def test_ungrounded_numbers_and_articles():
    assert ungrounded("Za zabójstwo grozi co najmniej 10 lat więzienia.", ART_148) == []
    assert ungrounded("Grozi od 8 do 10 lat, jak w art. 149.", ART_148) == ["liczba 8", "art. 149"]
    # "5 000" and "5000", "0,5" and "0.5" are the same number
    assert ungrounded("grzywna 5000 zł, stawka 0.5", "grzywna 5 000 złotych, stawka 0,5") == []


def test_is_repealed():
    assert is_repealed("(uchylony)")
    assert is_repealed(" (uchylona) ")
    assert not is_repealed("(uchylony) przepis § 2 stosuje się")


# --- summary task ---


class FakeGemini:
    def __init__(self, *answers: str):
        self.answers = list(answers)
        self.prompts: list[str] = []

    async def generate_structured(self, prompt, schema, **kwargs):
        self.prompts.append(prompt)
        return ArticleSummary(summary=self.answers.pop(0))


def test_summary_grounded_first_time():
    gemini = FakeGemini("Za zabójstwo grozi więzienie na co najmniej 10 lat.")
    assert asyncio.run(summarize_article(gemini, "Kodeks karny", "148", ART_148)).endswith("10 lat.")
    assert len(gemini.prompts) == 1


def test_summary_retried_with_problems_pointed_out():
    gemini = FakeGemini("Grozi 8 lat.", "Grozi co najmniej 10 lat.")
    assert asyncio.run(summarize_article(gemini, "Kodeks karny", "148", ART_148)) == "Grozi co najmniej 10 lat."
    assert "liczba 8" in gemini.prompts[1]


def test_summary_rejected_when_still_ungrounded():
    gemini = FakeGemini("Grozi 8 lat.", "Grozi 12 lat.")
    with pytest.raises(UngroundedSummaryError, match="liczba 12"):
        asyncio.run(summarize_article(gemini, "Kodeks karny", "148", ART_148))


# --- chunks ---


def count_words(text: str) -> int:
    return len(text.split())


def test_split_summary_keeps_sentences_together():
    assert split_summary("Jedno dwa. Trzy cztery. Pięć.", count_words, max_tokens=4) == ["Jedno dwa. Trzy cztery.", "Pięć."]
    assert split_summary("Krótko.", count_words) == ["Krótko."]


def test_article_chunks_ids_are_stable():
    kwargs = dict(
        slug="ustawa_kodeks_karny_1997_06_06_art_148", act_key="ustawa_kodeks_karny_1997_06_06",
        act_title="Kodeks karny", article_number="148", summary="Za zabójstwo grozi więzienie.",
        source_sha256="abc", count_tokens=count_words,
    )
    [(point_id, text, meta)] = article_chunks(**kwargs)
    assert text == "Kodeks karny\nArt. 148\nZa zabójstwo grozi więzienie."
    assert (meta.part, meta.part_count, meta.slug) == (0, 1, kwargs["slug"])
    assert article_chunks(**kwargs)[0][0] == point_id


# --- article query ---


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("art. 148 kk", ("148", "kk")),
        ("Art. 22[1] § 1 Kodeksu pracy", ("22^1", "Kodeksu pracy")),
        ("artykuł 30 ust. 1 pkt 2 ustawy o PIT", ("30", "ustawy o PIT")),
        ("art 148", ("148", "")),
        ("akcyza na papierosy", None),
    ],
)
def test_parse_article_query(query, expected):
    assert articles_service.parse_article_query(query) == expected


def test_key_act_for():
    assert articles_service.key_act_for("k.k.").eli == "DU/1997/553"
    assert articles_service.key_act_for("Kodeksu pracy").eli == "DU/1974/141"
    assert articles_service.key_act_for("ustawy o VAT").eli == "DU/2004/535"
    assert articles_service.key_act_for("ustawy o ochronie zwierząt") is None


# --- search merging ---


def article(slug: str, summary: str | None = "Streszczenie.") -> SimpleNamespace:
    act = SimpleNamespace(act_key="ustawa_kodeks_karny_1997_06_06", short_title="Kodeks karny", title="Ustawa")
    return SimpleNamespace(slug=slug, act=act, number=slug.rsplit("_", 1)[1], summary=summary, text="Tekst przepisu.")


def test_search_orders_reference_semantic_text_without_duplicates(monkeypatch):
    async def referenced(session, query, act_key):
        return [article("kk_art_148")]

    async def text(session, query, limit, act_key):
        return [(article("kk_art_148"), 0.5), (article("kk_art_150", summary=None), 0.3)]

    semantic_hit = articles_service._hit(article("kk_art_149"), "semantic", 0.8)
    monkeypatch.setattr(articles_service, "find_referenced_articles", referenced)
    monkeypatch.setattr(articles_service, "search_text", text)
    monkeypatch.setattr(articles_service, "search_articles", lambda *a, **k: [(semantic_hit, None)])

    hits = asyncio.run(articles_service.search(None, "art. 148 kk", limit=5, min_score=0.4))
    assert [(h.slug, h.match) for h in hits] == [
        ("kk_art_148", "reference"), ("kk_art_149", "semantic"), ("kk_art_150", "text"),
    ]
    assert hits[2].summary_preview == "Tekst przepisu."  # no summary yet: preview of the text
    assert len(asyncio.run(articles_service.search(None, "art. 148 kk", limit=2, min_score=0.4))) == 2


# --- acts endpoints ---

ACT = ActListItem(
    act_key="ustawa_kodeks_karny_1997_06_06", short_title="Kodeks karny", title="Ustawa z dnia 6 czerwca 1997 r. - Kodeks karny.",
    act_type="ustawa", act_date=date(1997, 6, 6), eli="DU/1997/553", legal_status_date=date(2026, 9, 27),
    article_count=461, summarized_count=0,
)


def test_list_and_get_act(api, monkeypatch):
    async def fake_list(session):
        return [ACT]

    async def fake_get(session, act_key):
        if act_key != ACT.act_key:
            return None
        item = ActArticleItem(slug=f"{ACT.act_key}_art_1", number="1", repealed=False, summary_preview=None)
        return ActDetail(**ACT.model_dump(), articles=[item])

    monkeypatch.setattr(acts_service, "list_acts", fake_list)
    monkeypatch.setattr(acts_service, "get_act", fake_get)

    assert api.get("/api/v1/acts").json()[0]["legal_status_date"] == "2026-09-27"
    assert api.get(f"/api/v1/acts/{ACT.act_key}").json()["articles"][0]["number"] == "1"
    assert api.get("/api/v1/acts/missing").status_code == 404
