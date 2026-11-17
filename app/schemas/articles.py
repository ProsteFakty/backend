from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class ArticleSearchRequest(BaseModel):
    query: str = Field(
        min_length=2,
        max_length=500,
        description="What the user typed, in Polish: a topic ('papierosy', 'zakaz konkurencji w umowie o pracę'), "
        "a question, a \"phrase in quotes\" from the law, or an article like 'art. 148 kk'",
    )
    limit: int = Field(10, ge=1, le=50, description="Page size")
    offset: int = Field(0, ge=0, le=200, description="How many hits to skip (paging)")
    act_key: str | None = Field(None, description="Search only this act (from GET /api/v1/acts)")


class ArticleSearchHit(BaseModel):
    """One result tile: act name, 'Art. N - title', short summary."""

    slug: str = Field(description="Article id, e.g. 'ustawa_o_podatku_akcyzowym_2008_12_06_art_99b'")
    act_key: str
    act_title: str = Field(description="Short name of the act, e.g. 'Ustawa o podatku akcyzowym'")
    article_number: str = Field(description="e.g. '99b'; '22^1' stands for 22 with superscript 1")
    title: str | None = Field(description="What the article is about; null until it has been summarized")
    summary: str | None = Field(description="1-3 sentences in plain Polish; null until it has been summarized")
    text_preview: str = Field(description="Beginning of the original wording (to show when there is no summary)")
    match: list[Literal["reference", "semantic", "text"]] = Field(
        description="Why it was found: 'reference' - the query named this article ('art. 148 kk'); "
        "'semantic' - similar in meaning; 'text' - its title, summary or wording contains the query words"
    )
    score: float = Field(description="Relevance used for ordering; comparable only within one response")


class ArticleSearchResponse(BaseModel):
    total: int = Field(description="Number of hits for the query (at most ~100 are ranked)")
    limit: int
    offset: int
    items: list[ArticleSearchHit] = Field(description="Best first")


class ActInfo(BaseModel):
    act_key: str
    short_title: str | None = Field(description="e.g. 'Kodeks karny'")
    title: str = Field(description="Full official title")
    act_type: str | None
    act_date: date | None


class ArticleReference(BaseModel):
    slug: str
    text: str = Field(description="How the article mentions it, e.g. 'art. 115 § 11' (to link in the wording)")
    same_act: bool = Field(description="Reference to another article of the same act")
    available: bool = Field(description="The referenced article is in the database (GET /articles/{slug} works)")
    act_title: str | None = Field(description="Short name of the referenced act; null when not available")
    article_number: str | None = Field(description="null when not available")
    title: str | None = Field(description="What the referenced article is about; null when unknown")


class ArticleDetail(BaseModel):
    slug: str
    number: str
    position: int = Field(description="Order of the article within its act")
    act: ActInfo
    title: str | None = Field(description="What the article is about; null until it has been summarized")
    summary: str | None = Field(description="1-3 sentences in plain Polish; null until it has been summarized")
    text: str = Field(description="Original legal text")
    repealed: bool = Field(description="'(uchylony)': the number is kept, the content is gone")
    references: list[ArticleReference]


class TextSpan(BaseModel):
    start: int
    end: int


class ExplanationCitation(BaseModel):
    slug: str = Field(description="Cited article (GET /articles/{slug})")
    act_title: str
    article_number: str
    unit: str | None = Field(description="'§ 11' / 'ust. 2' when only that part of the article was used")
    quote: str = Field(description="Verbatim fragment of the article's text; checked by the backend")
    spans: list[TextSpan] = Field(
        description="Where the quote is in the full text of the cited article (`text` of GET /articles/{slug}): "
        "text[start:end], counted in Unicode code points. Several spans when the quote skips a part with an "
        "ellipsis. Use them to highlight the source."
    )


class ExplanationPoint(BaseModel):
    text: str = Field(description="1-3 sentences in plain Polish")
    citations: list[ExplanationCitation] = Field(description="At least one; every quote was found in its article")


class ExplanationSource(BaseModel):
    """One provision the explanation was generated from."""

    slug: str
    act_title: str
    article_number: str
    title: str | None = Field(description="What the article is about; null until it has been summarized")
    unit: str | None = Field(description="'§ 11' / 'ust. 2' when only that part was used; 'fragment' when cut")
    depth: int = Field(description="0: the explained article, 1: referenced by it, 2: referenced by a referenced one")
    referenced_from: str | None = Field(description="Slug of the article that refers to this one")
    text: str = Field(description="The text given to the model")


class MissingReference(BaseModel):
    slug: str
    text: str = Field(description="How the article refers to it, e.g. 'art. 46 ust. 2 albo 3'")


class ArticleExplanation(BaseModel):
    slug: str
    act_title: str
    article_number: str
    title: str | None
    points: list[ExplanationPoint]
    sources: list[ExplanationSource] = Field(description="Everything the model saw, explained article first")
    missing_references: list[MissingReference] = Field(
        description="Provisions the article refers to that are not in our database (not used in the explanation)"
    )
    model: str
    generated_at: datetime
    cached: bool = Field(description="False when generated by this request")
