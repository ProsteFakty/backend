"""Articles read model: search (article reference, semantic in Qdrant, full text in Postgres) and full article data."""

import asyncio
import logging
import re

from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.db.articles import find_article_by_slug
from app.db.models import Act, Article
from app.pipeline.act_keys import normalize_article_number, slugify
from app.pipeline.key_acts import KEY_ACTS, KeyAct
from app.schemas.articles import ActInfo, ArticleDetail, ArticleReference, ArticleSearchHit
from app.vectorstore.collections import ActArticleChunk

logger = logging.getLogger(__name__)

PREVIEW_CHARS = 200

# "art. 148 kk", "Art. 22[1] § 1 Kodeksu pracy", "artykuł 30 ust. 1 pkt 2 ustawy o pit", "art 148"
_ARTICLE_QUERY = re.compile(
    r"^\s*art(?:\.|ykuł|ykul)?\s*(?P<number>\d+[a-z]*(?:\s?\[[0-9a-z]+\]|\^[0-9a-z]+|[⁰¹²³⁴⁵⁶⁷⁸⁹]+)?)\.?"
    r"(?:\s*(?:§|ust\.|ust\b|pkt\b|lit\.|lit\b)\s*(?:\d+[a-z]*|[a-z]\b))*\s*(?P<act>.*?)\s*$",
    re.IGNORECASE,
)


class SearchUnavailableError(RuntimeError):
    """Nothing found and the semantic index could not be asked."""


def _fold(text: str) -> str:
    """'Kodeksu karnego' -> 'kodeksukarnego', 'k.k.' -> 'kk'."""
    return slugify(text).replace("_", "")


def key_act_for(hint: str) -> KeyAct | None:
    """The key act a user means by 'kk', 'kodeks karny', 'Kodeksu karnego', 'ustawy o VAT', ..."""
    folded = _fold(hint)
    for act in KEY_ACTS:
        if folded in {_fold(name) for name in (act.short_title, *act.aliases, *act.abbreviations)}:
            return act
    return None


def parse_article_query(query: str) -> tuple[str, str] | None:
    """('148', 'kk') for 'art. 148 § 1 kk'; None when the query does not start with an article number."""
    match = _ARTICLE_QUERY.match(query)
    if not match:
        return None
    return normalize_article_number(match["number"]), match["act"].strip(" .,")


def _hit(article: Article, match: str, score: float) -> ArticleSearchHit:
    return ArticleSearchHit(
        slug=article.slug,
        act_key=article.act.act_key,
        act_title=article.act.short_title or article.act.title,
        article_number=article.number,
        score=score,
        summary_preview=summary_preview(article.summary or article.text),
        match=match,
    )


async def find_referenced_articles(session: AsyncSession, query: str, act_key: str | None) -> list[Article]:
    """Articles the query names directly. Without an act ('art. 148') the same number in every act matches."""
    parsed = parse_article_query(query)
    if parsed is None:
        return []
    number, hint = parsed
    statement = select(Article).join(Article.act).options(joinedload(Article.act)).where(Article.number == number)
    if hint:
        key_act = key_act_for(hint)
        if key_act is None:
            return []  # "art. 5 ustawy o czymś" - an act we do not have
        statement = statement.where(Act.eli == key_act.eli)
    elif act_key:
        statement = statement.where(Act.act_key == act_key)
    return list((await session.scalars(statement.order_by(Act.id))).all())


async def search_text(
    session: AsyncSession, query: str, limit: int, act_key: str | None
) -> list[tuple[Article, float]]:
    """Articles whose wording contains all words of the query (exact forms; quotes for a phrase)."""
    if not re.search(r"\w{3,}", query):
        return []
    vector = func.to_tsvector("simple", Article.text)
    tsquery = func.websearch_to_tsquery("simple", query)
    rank = func.ts_rank(vector, tsquery)
    statement = (
        select(Article, rank)
        .join(Article.act)
        .options(joinedload(Article.act))
        .where(vector.op("@@")(tsquery))
        .order_by(rank.desc())
        .limit(limit)
    )
    if act_key:
        statement = statement.where(Act.act_key == act_key)
    return [(article, float(score)) for article, score in (await session.execute(statement)).all()]


async def search(
    session: AsyncSession, query: str, limit: int, min_score: float, act_key: str | None = None
) -> list[ArticleSearchHit]:
    """Named articles first, then semantic hits, then full-text hits; each article once, at most `limit`."""
    hits: dict[str, ArticleSearchHit] = {}
    for article in await find_referenced_articles(session, query, act_key):
        hits.setdefault(article.slug, _hit(article, "reference", 1.0))

    semantic: list[tuple[ArticleSearchHit, ActArticleChunk]] | None
    try:
        semantic = await asyncio.to_thread(search_articles, query, k=limit, min_score=min_score, act_key=act_key)
    except (ResponseHandlingException, UnexpectedResponse) as exc:
        logger.warning("Semantic search unavailable: %s", exc)
        semantic = None
    for hit, _ in semantic or []:
        hits.setdefault(hit.slug, hit)

    for article, rank in await search_text(session, query, limit, act_key):
        hits.setdefault(article.slug, _hit(article, "text", round(rank, 4)))

    if not hits and semantic is None:
        raise SearchUnavailableError("Search index (Qdrant) is unavailable")
    return list(hits.values())[:limit]


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
