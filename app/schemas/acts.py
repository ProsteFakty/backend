from datetime import date

from pydantic import BaseModel, Field


class ActListItem(BaseModel):
    act_key: str = Field(description="Act id, e.g. 'ustawa_kodeks_karny_1997_06_06'; prefix of its article slugs")
    short_title: str | None = Field(description="e.g. 'Kodeks karny'")
    title: str = Field(description="Full official title")
    act_type: str | None
    act_date: date | None
    eli: str | None = Field(description="Base act in the Sejm ELI API, e.g. 'DU/1997/553'")
    legal_status_date: date | None = Field(description="The loaded text includes changes up to this day")
    article_count: int
    summarized_count: int = Field(description="Articles with a plain-language summary")


class ActArticleItem(BaseModel):
    slug: str = Field(description="GET /api/v1/articles/{slug}")
    number: str
    repealed: bool = Field(description="'(uchylony)': the number is kept, the content is gone")
    summary_preview: str | None


class ActDetail(ActListItem):
    articles: list[ActArticleItem] = Field(description="In the order of the act")
