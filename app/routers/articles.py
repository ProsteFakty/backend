from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.schemas.articles import ArticleDetail, ArticleExplanation, ArticleSearchRequest, ArticleSearchResponse
from app.services import articles as service
from app.services import explanations
from app.utils.gemini_client import GeminiError
from app.utils.gemini_client.tasks import UngroundedExplanationError

router = APIRouter(prefix="/api/v1/articles", tags=["articles"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.post("/search", summary="Articles matching a topic, a question, a phrase from the law or 'art. 148 kk'")
async def search_articles(body: ArticleSearchRequest, session: SessionDep) -> ArticleSearchResponse:
    """One ranking, best first: the article named in the query, then articles found by meaning (`semantic`)
    and by words (`text`) - an article found both ways ranks higher. Page with `limit` and `offset`."""
    try:
        return await service.search(session, body.query, limit=body.limit, offset=body.offset, act_key=body.act_key)
    except service.SearchUnavailableError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.get("/{slug}", summary="Full article: act, original text, summary and references")
async def get_article(slug: str, session: SessionDep) -> ArticleDetail:
    article = await service.get_article(session, slug)
    if article is None:
        raise HTTPException(404, f"Article '{slug}' not found")
    return article


@router.get(
    "/{slug}/explanation",
    summary="Plain-language explanation of the article with the provisions it refers to",
    responses={
        404: {"description": "No such article"},
        502: {"description": "The model could not produce an explanation backed by quotes from the law"},
        503: {"description": "Gemini is unavailable (or GEMINI_API_KEY is not set)"},
    },
)
async def explain_article(slug: str, session: SessionDep) -> ArticleExplanation:
    """Built only from texts in our database: the article, the articles it refers to and the ones those refer to.
    Every point carries verbatim quotes that the backend found in the cited articles; points it could not
    verify are dropped. Generating takes several seconds; afterwards it is cached until any of the texts changes.
    """
    try:
        explanation = await explanations.get_explanation(session, slug)
    except GeminiError as exc:
        raise HTTPException(503, f"Gemini is unavailable: {exc}") from exc
    except UngroundedExplanationError as exc:
        raise HTTPException(502, "Could not produce an explanation backed by the law text") from exc
    if explanation is None:
        raise HTTPException(404, f"Article '{slug}' not found")
    return explanation
