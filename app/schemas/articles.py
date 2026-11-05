from datetime import date

from pydantic import BaseModel, Field


class ArticleSearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500, description="User's question or description, in Polish")


class ArticleSearchHit(BaseModel):
    slug: str = Field(description="Article id, e.g. 'ustawa_o_podatku_akcyzowym_2008_12_06_art_99b'")
    act_key: str
    act_title: str
    article_number: str
    score: float = Field(description="Cosine similarity, 0-1; only hits above the threshold are returned")
    summary_preview: str = Field(description="Beginning of the plain-language summary")


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
