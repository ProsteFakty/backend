"""Articles read model: semantic search in Qdrant (act_articles), full article data from Postgres."""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.articles import find_article_by_slug
from app.db.models import Article
from app.schemas.articles import ActInfo, ArticleDetail, ArticleReference, ArticleSearchHit
from app.vectorstore.collections import ActArticleChunk

logger = logging.getLogger(__name__)

PREVIEW_CHARS = 200


def summary_preview(summary: str, limit: int = PREVIEW_CHARS) -> str:
    """First `limit` characters, cut at a word boundary; markdown bold markers dropped."""
    text = " ".join(summary.replace("**", "").split())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def search_articles(
    query: str, k: int = 3, min_score: float = 0.0, act_key: str | None = None
) -> list[tuple[ArticleSearchHit, ActArticleChunk]]:
    """Best `k` articles scoring at least `min_score`. An article split into parts counts once (its best part).

    Blocking (embeds the query on the CPU) - call it via asyncio.to_thread from async code.
    """
    from app.vectorstore import get_vector_store  # lazy: pulls in torch + langchain

    store = get_vector_store(ActArticleChunk)
    query_filter = ActArticleChunk.field_filter(act_key=act_key) if act_key else None
    results = store.similarity_search_with_score(query, k=k * 4, filter=query_filter)

    hits: dict[str, tuple[ArticleSearchHit, ActArticleChunk]] = {}
    for doc, score in results:  # sorted best-first
        if score < min_score:
            break
        chunk = ActArticleChunk.model_validate(doc.metadata)
        if chunk.slug in hits:
            continue
        hit = ArticleSearchHit(
            slug=chunk.slug,
            act_key=chunk.act_key,
            act_title=chunk.act_title,
            article_number=chunk.article_number,
            score=round(score, 4),
            summary_preview=summary_preview(chunk.summary),
        )
        hits[chunk.slug] = (hit, chunk)
        if len(hits) == k:
            break
    return list(hits.values())


async def get_article(session: AsyncSession, slug: str) -> ArticleDetail | None:
    article = await find_article_by_slug(session, slug)
    if article is None:
        return None

    referenced = [ref["slug"] for ref in article.references]
    available = set(
        (await session.scalars(select(Article.slug).where(Article.slug.in_(referenced)))).all() if referenced else []
    )
    act = article.act
    return ArticleDetail(
        slug=article.slug,
        number=article.number,
        position=article.position,
        act=ActInfo(act_key=act.act_key, title=act.title, act_type=act.act_type, act_date=act.act_date),
        text=article.text,
        summary=article.summary,
        references=[
            ArticleReference(slug=ref["slug"], same_act=ref["same_act"], available=ref["slug"] in available)
            for ref in article.references
        ],
    )


def warm_up() -> None:
    """Load the embedding model ahead of the first search (importing torch + the model takes ~10-40 s)."""
    try:
        from app.vectorstore.embeddings import get_embeddings  # lazy: pulls in torch + langchain

        get_embeddings().embed_query("rozgrzewka")
        logger.info("embedding model loaded")
    except Exception:
        logger.exception("embedding model warm-up failed; the first search will load it")
