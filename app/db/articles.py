import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.db.models import Act, Article
from app.pipeline.act_keys import has_date, make_article_slug, normalize_article_number

__all__ = ["find_article", "find_article_by_slug", "normalize_article_number"]


async def find_article_by_slug(session: AsyncSession, slug: str) -> Article | None:
    query = select(Article).options(joinedload(Article.act)).where(Article.slug == slug)
    return (await session.execute(query)).scalar_one_or_none()


async def find_article(session: AsyncSession, act_key: str, article_number: str) -> Article | None:
    """Look an article up by its slug, built from the act key and article number.

    When the reference did not say the act's date (act_key without date), any date matches and the
    newest act wins - still matched on the slug column only.
    """
    if has_date(act_key):
        return await find_article_by_slug(session, make_article_slug(act_key, article_number))

    article_part = make_article_slug("", article_number).removeprefix("_")  # "art_99b"
    pattern = rf"^{re.escape(act_key)}_\d{{4}}_\d{{2}}_\d{{2}}_{re.escape(article_part)}$"
    query = (
        select(Article)
        .join(Article.act)
        .options(joinedload(Article.act))
        .where(Article.slug.regexp_match(pattern))
        .order_by(Act.act_date.desc().nulls_last())
        .limit(1)
    )
    return (await session.execute(query)).scalar_one_or_none()
