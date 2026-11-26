"""Plain-language explanation of an article together with the provisions it refers to.

The model sees only texts from our database: the article, the articles it refers to and the articles those
refer to (depth 2), within a size budget. Every point it returns is backed by verbatim quotes that the backend
checks (app.utils.gemini_client.tasks.article_explanation). Results are cached until any of the texts changes.
"""

import asyncio
import hashlib
import weakref
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from google.genai import errors
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.config import get_settings
from app.db.articles import find_article_by_slug
from app.db.models import Article
from app.db.models import ArticleExplanation as ExplanationRow
from app.pipeline.grounding import find_quote
from app.pipeline.units import referenced_unit, unit_text
from app.schemas.articles import (
    ArticleExplanation,
    ExplanationCitation,
    ExplanationPoint,
    ExplanationSource,
    MissingReference,
    TextSpan,
)
from app.utils.gemini_client import GeminiError, get_gemini_client
from app.utils.gemini_client.tasks import Source, explain_article

MAX_DEPTH = 2
MAX_REFERENCED = 12  # articles besides the explained one
MAX_CONTEXT_CHARS = 40_000
# A longer referenced article is cut to the paragraph the reference names ("art. 115 § 11"), or to its beginning.
LONG_ARTICLE_CHARS = 3_000

# One generation per article at a time: a second request waits and then reads the cached result.
# (Per process - enough for the single uvicorn worker we run.)
_generating: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()


@dataclass
class ContextEntry:
    article: Article
    depth: int
    referenced_from: Article | None
    mention: str | None  # how the referring article cites it: "art. 115 § 11"
    unit: str | None  # "§ 11" when only that part is used, "fragment" when cut to the beginning
    text: str  # what the model sees

    @property
    def act_title(self) -> str:
        return self.article.act.short_title or self.article.act.title

    @property
    def label(self) -> str:
        unit = f" {self.unit}" if self.unit and self.unit != "fragment" else ""
        return f"{self.act_title}, art. {self.article.number}{unit}"

    def spans(self, quote: str) -> list[TextSpan]:
        """Where a verified quote is in the full text of the article (the model may have seen only a part)."""
        cut = self.unit is not None and self.unit != "fragment"
        base = max(self.article.text.find(self.text), 0) if cut else 0
        return [TextSpan(start=base + start, end=base + end) for start, end in find_quote(quote, self.text) or []]


def _referenced_text(article: Article, mention: str) -> tuple[str | None, str]:
    if len(article.text) <= LONG_ARTICLE_CHARS:
        return None, article.text
    if (unit := referenced_unit(mention, article.number)) and (text := unit_text(article.text, unit)):
        return unit, text
    return "fragment", article.text[:LONG_ARTICLE_CHARS].rsplit(" ", 1)[0] + " (…)"


async def _load_articles(session: AsyncSession, slugs: set[str]) -> dict[str, Article]:
    if not slugs:
        return {}
    statement = select(Article).options(joinedload(Article.act)).where(Article.slug.in_(slugs))
    return {a.slug: a for a in await session.scalars(statement)}


async def gather_context(session: AsyncSession, article: Article) -> tuple[list[ContextEntry], list[MissingReference]]:
    """The article first, then what it refers to, breadth-first. Missing = direct references not in the database."""
    entries = [ContextEntry(article, 0, None, None, None, article.text)]
    seen, missing = {article.slug}, []
    size = len(article.text)
    level: list[tuple[dict[str, Any], ContextEntry]] = [(ref, entries[0]) for ref in article.references]

    for depth in range(1, MAX_DEPTH + 1):
        found = await _load_articles(session, {ref["slug"] for ref, _ in level} - seen)
        next_level = []
        for ref, parent in level:
            if ref["slug"] in seen:
                continue
            target = found.get(ref["slug"])
            if target is None:
                if depth == 1:
                    missing.append(MissingReference(slug=ref["slug"], text=ref.get("text", "")))
                continue
            unit, text = _referenced_text(target, ref.get("text", ""))
            if len(entries) - 1 >= MAX_REFERENCED or size + len(text) > MAX_CONTEXT_CHARS:
                return entries, missing
            seen.add(target.slug)
            size += len(text)
            entry = ContextEntry(target, depth, parent.article, ref.get("text"), unit, text)
            entries.append(entry)
            # from a cut article follow only what its used part refers to
            next_level += [(r, entry) for r in target.references if unit is None or r.get("text", "") in text]
        level = next_level
    return entries, missing


def _sources(entries: list[ContextEntry]) -> list[Source]:
    sources = []
    for index, entry in enumerate(entries, start=1):
        if entry.referenced_from is None:
            role = "wyjaśniany artykuł"
        else:
            parent = entry.referenced_from
            role = f"art. {parent.number} {parent.act.short_title or ''} odsyła tu słowami: {entry.mention}".replace("  ", " ")
        sources.append(Source(id=f"Z{index}", label=entry.label, role=role, text=entry.text))
    return sources


def _context_hash(entries: list[ContextEntry], model: str) -> str:
    digest = hashlib.sha256(model.encode())
    for entry in entries:
        digest.update(f"\x00{entry.article.slug}\x00{entry.unit}\x00{entry.text}".encode())
    return digest.hexdigest()


async def _cached_or_generated(
    session: AsyncSession,
    article: Article,
    entries: list[ContextEntry],
    missing: list[MissingReference],
    context_hash: str,
    model: str,
) -> tuple[ExplanationRow, bool]:
    """(row, whether it was already up to date)."""
    row = await session.scalar(select(ExplanationRow).where(ExplanationRow.article_id == article.id))
    if row is not None and row.context_sha256 == context_hash:
        return row, True
    sources = _sources(entries)
    slug_of = {s.id: e.article.slug for s, e in zip(sources, entries, strict=True)}
    try:
        # Without a limit a stalled model would keep the app's request (and the per-article lock) open for minutes.
        async with asyncio.timeout(get_settings().explanation_timeout_seconds):
            points = await explain_article(get_gemini_client(), sources, [m.text for m in missing])
    except TimeoutError as exc:
        raise GeminiError(f"no answer from {model} in {get_settings().explanation_timeout_seconds:.0f} s") from exc
    except errors.APIError as exc:  # still refused after the retries: quota used up, overloaded, wrong model name
        raise GeminiError(f"{model} answered {exc.code}") from exc
    stored = [
        {"text": p.text, "citations": [{"slug": slug_of[c.source], "quote": c.quote} for c in p.citations]}
        for p in points
    ]
    if row is None:
        row = ExplanationRow(article_id=article.id)
        session.add(row)
    row.context_sha256, row.model, row.points = context_hash, model, stored
    row.created_at = datetime.now(UTC)
    await session.commit()
    return row, False


async def get_explanation(session: AsyncSession, slug: str) -> ArticleExplanation | None:
    """None if there is no such article. Gemini is asked only when there is no up-to-date cached explanation
    (GeminiError without a key, UngroundedExplanationError when nothing could be verified)."""
    article = await find_article_by_slug(session, slug)
    if article is None:
        return None
    entries, missing = await gather_context(session, article)
    by_slug = {e.article.slug: e for e in entries}
    model = get_settings().gemini_model
    context_hash = _context_hash(entries, model)

    lock = _generating.setdefault(slug, asyncio.Lock())
    async with lock:
        row, cached = await _cached_or_generated(session, article, entries, missing, context_hash, model)

    def citation(c: dict[str, str]) -> ExplanationCitation:
        entry = by_slug[c["slug"]]
        return ExplanationCitation(
            slug=c["slug"],
            act_title=entry.act_title,
            article_number=entry.article.number,
            unit=entry.unit,
            quote=c["quote"],
            spans=entry.spans(c["quote"]),
        )

    return ArticleExplanation(
        slug=article.slug,
        act_title=entries[0].act_title,
        article_number=article.number,
        title=article.title,
        points=[ExplanationPoint(text=p["text"], citations=[citation(c) for c in p["citations"]]) for p in row.points],
        sources=[
            ExplanationSource(
                slug=e.article.slug,
                act_title=e.act_title,
                article_number=e.article.number,
                title=e.article.title,
                unit=e.unit,
                depth=e.depth,
                referenced_from=e.referenced_from.slug if e.referenced_from else None,
                text=e.text,
            )
            for e in entries
        ],
        missing_references=missing,
        model=row.model,
        generated_at=row.created_at,
        cached=cached,
    )
