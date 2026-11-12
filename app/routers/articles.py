from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.session import get_session
from app.schemas.articles import ArticleDetail, ArticleSearchHit, ArticleSearchRequest
from app.services import articles as service

router = APIRouter(prefix="/api/v1/articles", tags=["articles"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.post("/search", summary="Articles matching a question, a phrase from the law or 'art. 148 kk'")
async def search_articles(body: ArticleSearchRequest, session: SessionDep) -> list[ArticleSearchHit]:
    """Order: the article named in the query (`match: reference`), articles similar in meaning (`semantic`,
    cosine >= SEARCH_MIN_SCORE), articles containing the query words (`text`). Each article appears once."""
    settings = get_settings()
    try:
        return await service.search(
            session,
            body.query,
            limit=body.limit or settings.search_top_k,
            min_score=settings.search_min_score,
            act_key=body.act_key,
        )
    except service.SearchUnavailableError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.get("/{slug}", summary="Full article: act, original text, summary and references")
async def get_article(slug: str, session: SessionDep) -> ArticleDetail:
    article = await service.get_article(session, slug)
    if article is None:
        raise HTTPException(404, f"Article '{slug}' not found")
    return article
