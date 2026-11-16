import asyncio
from datetime import date
from types import SimpleNamespace

import pytest

from app.pipeline.act_parser import is_repealed
from app.pipeline.chunks import article_chunks, split_summary
from app.pipeline.grounding import ungrounded
from app.pipeline.polish import search_terms
from app.schemas.acts import ActArticleItem, ActDetail, ActListItem
from app.services import acts as acts_service
from app.services import articles as articles_service
from app.utils.gemini_client.tasks import ArticleInput, UngroundedSummaryError, summarize_article, summarize_articles

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
    """Answers each request with the next list of (id, title, summary)."""

    def __init__(self, *answers: list[tuple[str, str, str]]):
        self.answers = list(answers)
        self.prompts: list[str] = []

    async def generate_structured(self, prompt, schema, **kwargs):
        self.prompts.append(prompt)
        return schema(items=[{"id": id_, "title": title, "summary": summary} for id_, title, summary in self.answers.pop(0)])


def test_summary_grounded_first_time():
    gemini = FakeGemini([("A1", "Kara za zabójstwo.", " Za zabójstwo grozi więzienie na co najmniej 10 lat. ")])
    result = asyncio.run(summarize_article(gemini, "Kodeks karny", "148", ART_148))
    assert (result.title, result.summary) == ("Kara za zabójstwo", "Za zabójstwo grozi więzienie na co najmniej 10 lat.")
    assert len(gemini.prompts) == 1


def test_summary_retried_with_problems_pointed_out():
    gemini = FakeGemini([("A1", "Kara 8 lat", "Grozi 10 lat.")], [("A1", "Kara za zabójstwo", "Grozi co najmniej 10 lat.")])
    assert asyncio.run(summarize_article(gemini, "Kodeks karny", "148", ART_148)).title == "Kara za zabójstwo"
    assert "liczba 8" in gemini.prompts[1]  # a number made up in the title counts too


def test_summary_rejected_when_still_ungrounded():
    gemini = FakeGemini([("A1", "Kara", "Grozi 8 lat.")], [("A1", "Kara", "Grozi 12 lat.")])
    with pytest.raises(UngroundedSummaryError, match="liczba 12"):
        asyncio.run(summarize_article(gemini, "Kodeks karny", "148", ART_148))


def test_several_articles_in_one_request_are_checked_separately():
    articles = [ArticleInput("Kodeks karny", "148", ART_148), ArticleInput("Kodeks karny", "1", "Karze podlega ten, kto popełnia czyn.")]
    gemini = FakeGemini(
        # A2 borrows "10" from the other article; "[A1]" with brackets is still A1
        [("[A1]", "Kara za zabójstwo", "Grozi co najmniej 10 lat."), ("A2", "Kto podlega karze", "Grozi 10 lat.")],
        [],  # the retry returns nothing for A2
    )
    first, second = asyncio.run(summarize_articles(gemini, articles))
    assert first.title == "Kara za zabójstwo"
    assert isinstance(second, UngroundedSummaryError) and "liczba 10" in str(second)
    assert "[A1]" not in gemini.prompts[1] and "[A2] Ustawa: Kodeks karny, art. 1" in gemini.prompts[1]  # only the failed one


def test_make_batches_by_count_and_size():
    from scripts.summarize_articles import make_batches

    texts = ["a" * 10, "b" * 10, "c" * 50, "d" * 10]
    batches = make_batches([SimpleNamespace(text=t) for t in texts], size=2, max_chars=40)
    assert [[a.text[0] for a in batch] for batch in batches] == [["a", "b"], ["c"], ["d"]]


# --- chunks ---


def count_words(text: str) -> int:
    return len(text.split())


def test_split_summary_keeps_sentences_together():
    assert split_summary("Jedno dwa. Trzy cztery. Pięć.", count_words, max_tokens=4) == ["Jedno dwa. Trzy cztery.", "Pięć."]
    assert split_summary("Krótko.", count_words) == ["Krótko."]


def test_article_chunks_ids_are_stable():
    kwargs = dict(
        slug="ustawa_kodeks_karny_1997_06_06_art_148", act_key="ustawa_kodeks_karny_1997_06_06",
        act_title="Kodeks karny", article_number="148", title="Kara za zabójstwo", summary="Za zabójstwo grozi więzienie.",
        source_sha256="abc", count_tokens=count_words,
    )
    [(point_id, text, meta)] = article_chunks(**kwargs)
    assert text == "Kodeks karny\nArt. 148 - Kara za zabójstwo\nZa zabójstwo grozi więzienie."
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
    assert articles_service.key_act_for("ustawy o ochronie zwierząt").eli == "DU/1997/724"
    assert articles_service.key_act_for("ksh").eli == "DU/2000/1037"
    assert articles_service.key_act_for("ustawy o lasach") is None


# --- words of a query ---


def test_search_terms_stem_polish_words():
    assert search_terms("Trzymanie psa na łańcuchu") == ["trzym:*", "psa:*", "lancuch:*"]
    assert search_terms("papierosy") == search_terms("papierosów") == ["papieros:*"]
    assert search_terms("stawka VAT 23") == ["stawk:*", "vat:*", "23"]  # numbers match exactly
    assert search_terms("co to jest") == []  # only stopwords


# --- search merging ---


def test_fuse_puts_named_articles_first_then_those_found_both_ways():
    ranked = articles_service.fuse(reference=["a148"], semantic=["s1", "both", "a148"], text=["t1", "both"])
    assert [r.slug for r in ranked] == ["a148", "both", "s1", "t1"]
    assert ranked[0].match == ["reference", "semantic"]
    assert ranked[1].match == ["semantic", "text"]


class FakeSession:
    def __init__(self, articles):
        self.articles = articles

    async def scalars(self, statement):
        return self.articles


def article(slug: str, title: str | None = "Tytuł", summary: str | None = "Streszczenie.") -> SimpleNamespace:
    act = SimpleNamespace(act_key="ustawa_kodeks_karny_1997_06_06", short_title="Kodeks karny", title="Ustawa")
    return SimpleNamespace(
        slug=slug, act=act, number=slug.rsplit("_", 1)[1], title=title, summary=summary, text="Tekst przepisu."
    )


def test_search_merges_sources_and_pages(monkeypatch):
    async def referenced(session, query, act_key):
        return ["kk_art_148"] if query.startswith("art.") else []

    async def text(session, query, limit, act_key):
        return ["kk_art_148", "kk_art_150", "kk_art_removed"]

    monkeypatch.setattr(articles_service, "find_referenced_articles", referenced)
    monkeypatch.setattr(articles_service, "search_text", text)
    monkeypatch.setattr(articles_service, "search_semantic", lambda *a, **k: [("kk_art_149", 0.8), ("kk_art_150", 0.6)])
    session = FakeSession([article("kk_art_148"), article("kk_art_149"), article("kk_art_150", None, None)])

    page = asyncio.run(articles_service.search(session, "zabójstwo"))
    assert [(h.slug, h.match) for h in page.items] == [
        ("kk_art_150", ["semantic", "text"]),  # found both ways: first
        ("kk_art_149", ["semantic"]),
        ("kk_art_148", ["text"]),
    ]
    assert page.total == 3  # the article that is no longer in the database is not counted
    assert (page.items[0].title, page.items[0].summary, page.items[0].text_preview) == (None, None, "Tekst przepisu.")

    second = asyncio.run(articles_service.search(session, "zabójstwo", limit=2, offset=2))
    assert [h.slug for h in second.items] == ["kk_art_148"] and second.total == 3

    # a query naming an article returns that article only
    named = asyncio.run(articles_service.search(FakeSession([article("kk_art_148")]), "art. 148 kk"))
    assert [(h.slug, h.match, h.score) for h in named.items] == [("kk_art_148", ["reference"], 1.0)]


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
        item = ActArticleItem(slug=f"{ACT.act_key}_art_1", number="1", repealed=False, title=None, summary=None)
        return ActDetail(**ACT.model_dump(), articles=[item])

    monkeypatch.setattr(acts_service, "list_acts", fake_list)
    monkeypatch.setattr(acts_service, "get_act", fake_get)

    assert api.get("/api/v1/acts").json()[0]["legal_status_date"] == "2026-09-27"
    assert api.get(f"/api/v1/acts/{ACT.act_key}").json()["articles"][0]["number"] == "1"
    assert api.get("/api/v1/acts/missing").status_code == 404
