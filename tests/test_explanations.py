import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.pipeline.grounding import find_quote, quote_found
from app.pipeline.units import referenced_unit, unit_text
from app.services import explanations
from app.utils.gemini_client.tasks import Source, UngroundedExplanationError, explain_article
from app.utils.gemini_client.tasks.article_explanation import Citation, Explanation, Point, check_points

ART_115 = "§ 1. Czynem zabronionym jest zachowanie.\n§ 11. Osobą najbliższą jest małżonek, wstępny, zstępny.\n§ 12. Inny."
ART_148 = "§ 1. Kto zabija człowieka, podlega karze pozbawienia wolności na czas nie krótszy od lat 10."


# --- units ---


def test_referenced_unit():
    assert referenced_unit("art. 115 § 11", "115") == "§ 11"
    assert referenced_unit("art. 143 § 1 lub w art. 144 lub 145", "143") == "§ 1"
    assert referenced_unit("art. 143 § 1 lub w art. 144 lub 145", "144") is None
    assert referenced_unit("art. 30 ust. 1 pkt 2", "30") == "ust. 1"


def test_unit_text():
    assert unit_text(ART_115, "§ 11") == "§ 11. Osobą najbliższą jest małżonek, wstępny, zstępny."
    assert unit_text("1. Pierwszy.\n1) punkt\n2. Drugi.", "ust. 1") == "1. Pierwszy.\n1) punkt"
    assert unit_text(ART_115, "§ 99") is None


# --- quotes ---


def test_quote_found():
    assert quote_found("kto zabija  człowieka", ART_148)
    assert quote_found("„Kto zabija człowieka” … nie krótszy od lat 10", ART_148)
    assert not quote_found("kto zabija zwierzę", ART_148)
    assert not quote_found("nie krótszy od lat 10 ... kto zabija", ART_148)  # pieces out of order
    assert not quote_found("…", ART_148)


def test_find_quote_gives_positions_in_the_original_text():
    text = "§ 1. Kto zabija\n  człowieka – podlega karze."
    [(start, end)] = find_quote("kto zabija człowieka - podlega", text)
    assert text[start:end] == "Kto zabija\n  człowieka – podlega"
    assert [text[s:e] for s, e in find_quote("Kto zabija ... karze", text)] == ["Kto zabija", "karze"]
    assert find_quote("kto zabija zwierzę", text) is None


# --- explanation task ---

SOURCES = [
    Source("Z1", "Kodeks karny, art. 148", "wyjaśniany artykuł", ART_148),
    Source("Z2", "Kodeks karny, art. 115 § 11", "odesłanie", "§ 11. Osobą najbliższą jest małżonek."),
]
GOOD = Point(text="Za zabójstwo grozi co najmniej 10 lat więzienia.", citations=[Citation(source="Z1", quote="nie krótszy od lat 10")])
WRONG_QUOTE = Point(text="Osobą najbliższą jest rodzeństwo.", citations=[Citation(source="Z2", quote="jest rodzeństwo")])
WRONG_NUMBER = Point(text="Grozi 15 lat.", citations=[Citation(source="Z1", quote="Kto zabija człowieka")])


def test_check_points():
    valid, problems = check_points([GOOD, WRONG_QUOTE, WRONG_NUMBER, Point(text="Bez cytatu.", citations=[])], {s.id: s for s in SOURCES})
    assert valid == [GOOD]
    assert [p.split(":")[0] for p in problems] == ["Punkt 2", "Punkt 3", "Punkt 4"]
    assert "liczba 15" in problems[1]


class FakeGemini:
    model = "fake"

    def __init__(self, *answers: list[Point]):
        self.answers = list(answers)
        self.prompts: list[str] = []

    async def generate_structured(self, prompt, schema, **kwargs):
        self.prompts.append(prompt)
        return Explanation(points=self.answers.pop(0))


def test_explain_retries_and_keeps_only_verified_points():
    gemini = FakeGemini([WRONG_QUOTE], [GOOD, WRONG_NUMBER])
    assert asyncio.run(explain_article(gemini, SOURCES, [])) == [GOOD]
    assert "[Z2] Kodeks karny, art. 115 § 11" in gemini.prompts[0]
    assert "Punkt 1" in gemini.prompts[1]


def test_explain_fails_when_nothing_verified():
    with pytest.raises(UngroundedExplanationError):
        asyncio.run(explain_article(FakeGemini([WRONG_QUOTE], [WRONG_NUMBER]), SOURCES, []))


# --- context and service ---

KK = SimpleNamespace(short_title="Kodeks karny", title="Ustawa - Kodeks karny")


def article(number: str, text: str, refs: list[tuple[str, str]] = (), id_: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        id=id_, slug=f"kk_art_{number}", number=number, title=None, text=text, act=KK,
        references=[{"slug": f"kk_art_{n}", "same_act": True, "text": t} for n, t in refs],
    )


DB = {
    "kk_art_148": article("148", ART_148, [("115", "art. 115 § 11"), ("999", "art. 999")]),
    "kk_art_115": article("115", ART_115 + " " + "x" * 3000, [("1", "art. 1")]),
    "kk_art_1": article("1", "§ 1. Odpowiedzialności karnej podlega ten tylko."),
}


@pytest.fixture
def fake_db(monkeypatch):
    async def load(session, slugs):
        return {s: DB[s] for s in slugs if s in DB}

    monkeypatch.setattr(explanations, "_load_articles", load)


def test_gather_context_cuts_long_articles_to_the_referenced_paragraph(fake_db):
    entries, missing = asyncio.run(explanations.gather_context(None, DB["kk_art_148"]))
    assert [(e.article.number, e.depth, e.unit) for e in entries] == [("148", 0, None), ("115", 1, "§ 11")]
    assert entries[1].text == "§ 11. Osobą najbliższą jest małżonek, wstępny, zstępny."
    # art. 115 refers to art. 1 only outside § 11, so art. 1 is not followed
    assert [m.slug for m in missing] == ["kk_art_999"]


class FakeSession:
    def __init__(self, row=None):
        self.row, self.added, self.commits = row, [], 0

    async def scalar(self, statement):
        return self.row

    def add(self, row):
        self.added.append(row)

    async def commit(self):
        self.commits += 1


def test_get_explanation_generates_then_serves_from_cache(fake_db, monkeypatch):
    async def find(session, slug):
        return DB.get(slug)

    family = Point(text="Osobą najbliższą jest m.in. małżonek.", citations=[Citation(source="Z2", quote="małżonek, wstępny")])
    gemini = FakeGemini([family, GOOD])
    monkeypatch.setattr(explanations, "find_article_by_slug", find)
    monkeypatch.setattr(explanations, "get_gemini_client", lambda: gemini)

    session = FakeSession()
    first = asyncio.run(explanations.get_explanation(session, "kk_art_148"))
    assert not first.cached and session.commits == 1
    # source ids mapped back to article slugs
    assert [p.citations[0].slug for p in first.points] == ["kk_art_115", "kk_art_148"]
    assert first.points[0].citations[0].unit == "§ 11"
    # the model saw only § 11, the position is given in the whole art. 115
    [span] = first.points[0].citations[0].spans
    assert DB["kk_art_115"].text[span.start : span.end] == "małżonek, wstępny"
    assert first.sources[1].unit == "§ 11"
    assert first.missing_references[0].text == "art. 999"

    row = session.added[0]
    second = asyncio.run(explanations.get_explanation(FakeSession(row), "kk_art_148"))
    assert second.cached and second.points == first.points
    assert gemini.answers == []  # Gemini was not asked again

    row.context_sha256 = "stale"  # a text changed since
    gemini.answers = [[GOOD]]
    assert not asyncio.run(explanations.get_explanation(FakeSession(row), "kk_art_148")).cached
    assert asyncio.run(explanations.get_explanation(FakeSession(), "missing")) is None


def test_explanation_endpoint_errors(api, monkeypatch):
    from app.utils.gemini_client import GeminiError

    async def fake(session, slug):
        if slug == "no_key":
            raise GeminiError("GEMINI_API_KEY is not set")
        if slug == "ungrounded":
            raise UngroundedExplanationError("nothing verified")
        return None

    monkeypatch.setattr(explanations, "get_explanation", fake)
    assert api.get("/api/v1/articles/no_key/explanation").status_code == 503
    assert api.get("/api/v1/articles/ungrounded/explanation").status_code == 502
    assert api.get("/api/v1/articles/missing/explanation").status_code == 404
