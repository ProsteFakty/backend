from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class ArticleSearchRequest(BaseModel):
    query: str = Field(
        min_length=2,
        max_length=500,
        description="User's question, a phrase from the law, or an article like 'art. 148 kk', in Polish",
    )
    limit: int | None = Field(None, ge=1, le=20, description="Max hits; default SEARCH_TOP_K")
    act_key: str | None = Field(None, description="Search only this act (from GET /api/v1/acts)")


class ArticleSearchHit(BaseModel):
    slug: str = Field(description="Article id, e.g. 'ustawa_o_podatku_akcyzowym_2008_12_06_art_99b'")
    act_key: str
    act_title: str
    article_number: str
    score: float = Field(
        description="'reference': 1.0; 'semantic': cosine similarity 0-1 (only above the threshold); "
        "'text': full-text rank, comparable only between text hits"
    )
    summary_preview: str = Field(description="Beginning of the plain-language summary (or of the text without one)")
    match: Literal["reference", "semantic", "text"] = Field(
        "semantic",
        description="'reference': the query named this article ('art. 148 kk'); 'semantic': similar in meaning; "
        "'text': the article's wording contains the query words",
    )


class ActInfo(BaseModel):
    act_key: str
    title: str
    act_type: str | None
    act_date: date | None


class ArticleReference(BaseModel):
    slug: str
    same_act: bool = Field(description="Reference to another article of the same act")
    available: bool = Field(description="The referenced article is in the database (GET /articles/{slug} works)")


class ArticleDetail(BaseModel):
    slug: str
    number: str
    position: int = Field(description="Order of the article within its act")
    act: ActInfo
    text: str = Field(description="Original legal text")
    summary: str | None = Field(description="Plain-language summary; null for base acts loaded without one")
    references: list[ArticleReference]
