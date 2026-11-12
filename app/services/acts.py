"""Acts loaded by scripts.ingest_acts, with their articles."""

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Act, Article, SourceDocument
from app.pipeline.act_keys import parse_iso_date
from app.pipeline.act_parser import is_repealed
from app.schemas.acts import ActArticleItem, ActDetail, ActListItem
from app.services.articles import summary_preview


async def _legal_status_dates(session: AsyncSession) -> dict[str, date | None]:
    """act_key -> legal status date of the newest loaded text."""
    documents = await session.scalars(select(SourceDocument).order_by(SourceDocument.id))
    return {d.act_key: parse_iso_date(d.pdf_metadata.get("legal_status_date")) for d in documents if d.act_key}


def _list_item(act: Act, legal_status_date: date | None) -> ActListItem:
    return ActListItem(
        act_key=act.act_key,
        short_title=act.short_title,
        title=act.title,
        act_type=act.act_type,
        act_date=act.act_date,
        eli=act.eli,
        legal_status_date=legal_status_date,
        article_count=len(act.articles),
        summarized_count=sum(1 for a in act.articles if a.summary),
    )


async def list_acts(session: AsyncSession) -> list[ActListItem]:
    acts = await session.scalars(select(Act).options(selectinload(Act.articles)).order_by(Act.id))
    dates = await _legal_status_dates(session)
    return [_list_item(act, dates.get(act.act_key)) for act in acts]


async def get_act(session: AsyncSession, act_key: str) -> ActDetail | None:
    act = await session.scalar(
        select(Act).where(Act.act_key == act_key).options(selectinload(Act.articles))
    )
    if act is None:
        return None
    dates = await _legal_status_dates(session)
    articles: list[Article] = sorted(act.articles, key=lambda a: a.position)
    return ActDetail(
        **_list_item(act, dates.get(act.act_key)).model_dump(),
        articles=[
            ActArticleItem(
                slug=a.slug,
                number=a.number,
                repealed=is_repealed(a.text),
                summary_preview=summary_preview(a.summary) if a.summary else None,
            )
            for a in articles
        ],
    )
