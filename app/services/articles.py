"""Articles read model: search and full article data.

Search asks three sources and merges them into one ranking:
- reference: the article the query names ("art. 148 kk") - then only that is returned,
- semantic: titles + summaries embedded in Qdrant (meaning: "trzymanie psa na łańcuchu" -> "uwięź"),
- text: title, summary and wording in Postgres full-text search (words: "papierosy" -> "papierosów").
"""

import asyncio
import logging
import re
from dataclasses import dataclass, field

from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse
from sqlalchemy import Integer, cast, func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.config import get_settings
from app.db.articles import find_article_by_slug
from app.db.models import Act, Article
from app.pipeline.act_keys import normalize_article_number, slugify
from app.pipeline.act_parser import is_repealed
from app.pipeline.key_acts import KEY_ACTS, KeyAct
from app.pipeline.polish import fold, search_terms
from app.schemas.articles import ActInfo, ArticleDetail, ArticleReference, ArticleSearchHit, ArticleSearchResponse

logger = logging.getLogger(__name__)

PREVIEW_CHARS = 200
# How many articles each source contributes to the ranking; paging stops there.
SEMANTIC_CANDIDATES = 50
TEXT_CANDIDATES = 50
# Reciprocal rank fusion: an article scores 1 / (RRF_K + its rank) in every source that found it.
RRF_K = 60

_SIMPLE = literal_column("'simple'::regconfig")
# ts_rank normalization: divide by the article's length, or the longest articles (tax definitions,
# lists of exemptions) outrank the short one that is exactly about the query.
_BY_LENGTH = 2

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


def preview(text: str, limit: int = PREVIEW_CHARS) -> str:
    """First `limit` characters, cut at a word boundary; markdown bold markers dropped."""
    text = " ".join(text.replace("**", "").split())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"


async def find_referenced_articles(session: AsyncSession, query: str, act_key: str | None) -> list[str]:
    """Slugs of the articles the query names. Without an act ('art. 148') the same number in every act matches."""
    parsed = parse_article_query(query)
    if parsed is None:
        return []
    number, hint = parsed
    statement = select(Article.slug).join(Article.act).where(Article.number == number)
    if hint:
        key_act = key_act_for(hint)
        if key_act is None:
            return []  # "art. 5 ustawy o czymś" - an act we do not have
        statement = statement.where(Act.eli == key_act.eli)
    elif act_key:
        statement = statement.where(Act.act_key == act_key)
    return list((await session.scalars(statement.order_by(Act.id))).all())


async def search_text(session: AsyncSession, query: str, limit: int, act_key: str | None) -> list[str]:
    """Slugs of articles containing the query words, best first.

    Words are matched by their stems (app.pipeline.polish); at least half of them must be present, and articles
    with more of them come first. A query with a "phrase in quotes" is matched as written instead.
    """
    vector = Article.search_vector
    if '"' in query:
        tsquery = func.websearch_to_tsquery(_SIMPLE, fold(query))
        statement = select(Article.slug).where(vector.op("@@")(tsquery)).order_by(func.ts_rank(vector, tsquery, _BY_LENGTH).desc())
    else:
        terms = search_terms(query)
        if not terms:
            return []
        any_term = func.to_tsquery(_SIMPLE, " | ".join(terms))
        matched = sum(cast(vector.op("@@")(func.to_tsquery(_SIMPLE, term)), Integer) for term in terms)
        statement = (
            select(Article.slug)
            .where(vector.op("@@")(any_term), matched >= (len(terms) + 1) // 2)
            .order_by(matched.desc(), func.ts_rank(vector, any_term, _BY_LENGTH).desc())
        )
    if act_key:
        statement = statement.join(Article.act).where(Act.act_key == act_key)
    return list((await session.scalars(statement.order_by(Article.id).limit(limit))).all())


def search_semantic(query: str, k: int, min_score: float, act_key: str | None = None) -> list[tuple[str, float]]:
    """(slug, cosine similarity) of the best `k` articles scoring at least `min_score`, best first.

    Blocking (embeds the query on the CPU) - call it via asyncio.to_thread from async code.
    """
    from app.vectorstore import ActArticleChunk, get_vector_store  # lazy: pulls in torch + langchain

    store = get_vector_store(ActArticleChunk)
    query_filter = ActArticleChunk.field_filter(act_key=act_key) if act_key else None
    # A long summary is embedded in parts; the article counts once, with its best part.
    results = store.similarity_search_with_score(query, k=k * 2, filter=query_filter)

    hits: dict[str, float] = {}
    for doc, score in results:  # sorted best-first
        if score < min_score or len(hits) == k:
            break
        hits.setdefault(doc.metadata["slug"], round(score, 4))
    return list(hits.items())


@dataclass
class Ranked:
    slug: str
    score: float = 0.0
    match: list[str] = field(default_factory=list)


def fuse(reference: list[str], semantic: list[str], text: list[str]) -> list[Ranked]:
    """One ranking out of three: named articles first, the rest by reciprocal rank fusion."""
    ranked: dict[str, Ranked] = {}
    for slug in reference:
        ranked.setdefault(slug, Ranked(slug, 1.0, ["reference"]))
    for name, slugs in (("semantic", semantic), ("text", text)):
        for rank, slug in enumerate(slugs, start=1):
            item = ranked.setdefault(slug, Ranked(slug))
            item.score += 1 / (RRF_K + rank)
            item.match.append(name)
    return sorted(ranked.values(), key=lambda item: item.score, reverse=True)  # stable: ties keep source order


async def search(
    session: AsyncSession, query: str, limit: int = 10, offset: int = 0, act_key: str | None = None
) -> ArticleSearchResponse:
    reference = await find_referenced_articles(session, query, act_key)
    text: list[str] = []
    semantic: list[tuple[str, float]] | None = []
    if not reference:  # "art. 148 kk" asks for that article, not for articles that mention 148
        text = await search_text(session, query, TEXT_CANDIDATES, act_key)
        try:
            semantic = await asyncio.to_thread(
                search_semantic, query, SEMANTIC_CANDIDATES, get_settings().search_min_score, act_key
            )
        except (ResponseHandlingException, UnexpectedResponse) as exc:
            logger.warning("Semantic search unavailable: %s", exc)
            semantic = None
        if semantic is None and not text:
            raise SearchUnavailableError("Search index (Qdrant) is unavailable")

    ranked = fuse(reference, [slug for slug, _ in semantic or []], text)
    statement = select(Article).options(joinedload(Article.act)).where(Article.slug.in_([r.slug for r in ranked]))
    articles = {a.slug: a for a in await session.scalars(statement)} if ranked else {}
    ranked = [r for r in ranked if r.slug in articles]  # the index may still hold an article removed since

    items = []
    for item in ranked[offset : offset + limit]:
        article = articles[item.slug]
        items.append(
            ArticleSearchHit(
                slug=article.slug,
                act_key=article.act.act_key,
                act_title=article.act.short_title or article.act.title,
                article_number=article.number,
                title=article.title,
                summary=article.summary,
                text_preview=preview(article.text),
                match=item.match,
                score=round(item.score, 4),
            )
        )
    return ArticleSearchResponse(total=len(ranked), limit=limit, offset=offset, items=items)


async def get_article(session: AsyncSession, slug: str) -> ArticleDetail | None:
    article = await find_article_by_slug(session, slug)
    if article is None:
        return None

    slugs = [ref["slug"] for ref in article.references]
    statement = select(Article).options(joinedload(Article.act)).where(Article.slug.in_(slugs))
    referenced = {a.slug: a for a in await session.scalars(statement)} if slugs else {}

    def reference(ref: dict) -> ArticleReference:
        target = referenced.get(ref["slug"])
        return ArticleReference(
            slug=ref["slug"],
            text=ref.get("text", ""),
            same_act=ref["same_act"],
            available=target is not None,
            act_title=(target.act.short_title or target.act.title) if target else None,
            article_number=target.number if target else None,
            title=target.title if target else None,
        )

    act = article.act
    return ArticleDetail(
        slug=article.slug,
        number=article.number,
        position=article.position,
        act=ActInfo(
            act_key=act.act_key, short_title=act.short_title, title=act.title, act_type=act.act_type, act_date=act.act_date
        ),
        title=article.title,
        summary=article.summary,
        text=article.text,
        repealed=is_repealed(article.text),
        references=[reference(ref) for ref in article.references],
    )


def warm_up() -> None:
    """Load the embedding model ahead of the first search (importing torch + the model takes ~10-40 s)."""
    try:
        from app.vectorstore.embeddings import get_embeddings  # lazy: pulls in torch + langchain

        get_embeddings().embed_query("rozgrzewka")
        logger.info("embedding model loaded")
    except Exception:
        logger.exception("embedding model warm-up failed; the first search will load it")
