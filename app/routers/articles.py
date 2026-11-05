import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.session import get_session
from app.schemas.articles import ArticleDetail, ArticleSearchHit, ArticleSearchRequest
from app.services import articles as service

router = APIRouter(prefix="/api/v1/articles", tags=["articles"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.post("/search", summary="Articles best matching a user's phrase (top 3, score >= threshold)")
async def search_articles(body: ArticleSearchRequest) -> list[ArticleSearchHit]:
    settings = get_settings()
    try:
        results = await asyncio.to_thread(
            service.search_articles, body.query, k=settings.search_top_k, min_score=settings.search_min_score
        )
    except (ResponseHandlingException, UnexpectedResponse) as exc:
        raise HTTPException(503, "Search index (Qdrant) is unavailable") from exc
    return [hit for hit, _ in results]


@router.get("/{slug}", summary="Full article: act, original text, summary and references")
async def get_article(slug: str, session: SessionDep) -> ArticleDetail:
    article = await service.get_article(session, slug)
    if article is None:
        raise HTTPException(404, f"Article '{slug}' not found")
    return article
