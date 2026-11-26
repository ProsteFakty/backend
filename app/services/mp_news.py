"""'Ostatnio w mediach' on the MP profile: press coverage from the last 30 days (docs/specs/mp-media-news.md).

Claude (Haiku + web search) writes the items; everything it says is checked here before it is stored:
each item needs a date in the window, the MP's last name, and at least one source URL that the web search
actually returned. Spending is capped per month (app/utils/claude_client.py).
"""

import asyncio
import datetime as dt
import logging
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import urlparse, urlunparse

import httpx
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import MpNewsCache
from app.db.session import get_session_factory
from app.services import politicians
from app.services.mp_profile import club_name
from app.services.politicians import Politician
from app.utils.claude_client import ensure_budget, get_claude_client, record_usage
from app.utils.claude_news import ClaudeNews, search_mp_news

logger = logging.getLogger(__name__)

WINDOW_DAYS = 30
MAX_ITEMS = 5
MAX_SOURCES = 3
HEADLINE_MAX = 80
SUMMARY_MAX = 300
DISCLAIMER = "Podsumowanie automatyczne na podstawie publikacji medialnych. Sprawdź źródła."

Category = Literal["statement", "controversy", "legislation", "personal", "other"]
CATEGORIES: set[str] = {"statement", "controversy", "legislation", "personal", "other"}


class NewsSource(BaseModel):
    url: str = Field(description="Article link, as returned by the web search.")
    domain: str = Field(description="Site without `www.`, e.g. `tvn24.pl`. FE shows it as the link label.")
    title: str | None = Field(None, description="Article title from the search result.")


class NewsItem(BaseModel):
    headline: str = Field(description=f"Up to {HEADLINE_MAX} chars, Polish.")
    summary: str = Field(
        description=f"1-2 sentences, up to {SUMMARY_MAX} chars, Polish; claims attributed to the source."
    )
    date: dt.date = Field(description="First publication, within the last 30 days.")
    category: Category = Field(description="For an icon / chip.")
    sources: list[NewsSource] = Field(description=f"1-{MAX_SOURCES} sources.")


class MpNews(BaseModel):
    mp_id: int
    status: Literal["ok", "empty", "unavailable"] = Field(
        description="`ok`: `items` has news. `empty`: nothing relevant found (FE hides the section). "
        "`unavailable`: never fetched successfully yet (FE hides the section)."
    )
    generated_at: datetime | None = Field(description="When the served result was produced; null if `unavailable`.")
    items: list[NewsItem] = Field(description=f"0-{MAX_ITEMS} items, newest first.")
    disclaimer: str = Field(DISCLAIMER, description="Show small under the section.")


# --- validation (pure) ---


def _norm(text: str) -> str:
    return " ".join(text.replace("\\", "").split()).casefold()


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0].rstrip(",;:-") + "…"


def _name_stems(last_name: str) -> list[str]:
    """Polish last names decline (Kowalczyk -> Kowalczyka, Kowalska -> Kowalskiej): match without the ending."""
    parts = [p for p in last_name.replace("-", " ").split() if p]
    return [(p[:-1] if len(p) >= 6 else p).casefold() for p in parts]


def _bare(domain: str | None) -> str:
    domain = (domain or "").strip().casefold()
    return domain.removeprefix("www.")


def _host(url: str) -> str:
    return _bare(urlparse(url).hostname)


def _blocked(host: str, blocked: Iterable[str]) -> bool:
    return any(host == d or host.endswith("." + d) for d in map(_bare, blocked))


def _parse_date(value: object) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _url_key(url: str) -> str:
    """Same article despite cosmetic differences: no fragment, no trailing slash, host case-insensitive."""
    parts = urlparse(url.strip())
    return urlunparse(parts._replace(netloc=parts.netloc.casefold(), fragment="", path=parts.path.rstrip("/")))


def build_items(
    news: ClaudeNews, *, last_name: str, today: dt.date, blocked_domains: Iterable[str] = ()
) -> list[NewsItem]:
    since = today - timedelta(days=WINDOW_DAYS)
    stems = _name_stems(last_name)
    found = {}
    for result in news.results:
        found.setdefault(_url_key(result.url), result)

    items: list[NewsItem] = []
    used: set[str] = set()
    for raw in news.items:  # model order = most discussed first, so earlier items keep shared sources
        headline = _clip(str(raw.get("headline") or ""), HEADLINE_MAX)
        summary = _clip(str(raw.get("summary") or ""), SUMMARY_MAX)
        day = _parse_date(raw.get("date"))
        if not headline or not summary or day is None or not since <= day <= today:
            continue
        if not any(stem in _norm(f"{headline} {summary}") for stem in stems):
            continue  # namesake or off-topic
        sources: list[NewsSource] = []
        urls = raw.get("source_urls") or []
        for url in urls if isinstance(urls, list) else []:
            result = found.get(_url_key(str(url)))  # only what the search returned, never a URL the model made up
            if result is None or result.url in used or _blocked(_host(result.url), blocked_domains):
                continue
            used.add(result.url)
            sources.append(NewsSource(url=result.url, domain=_host(result.url), title=result.title))
            if len(sources) == MAX_SOURCES:
                break
        if not sources:
            continue
        category = str(raw.get("category") or "").strip().casefold()
        items.append(
            NewsItem(
                headline=headline,
                summary=summary,
                date=day,
                category=category if category in CATEGORIES else "other",
                sources=sources,
            )
        )
    items.sort(key=lambda i: i.date, reverse=True)
    return items[:MAX_ITEMS]


# --- fetching ---


def news_subject(mp_id: int) -> tuple[Politician, str | None] | None:
    """The MP and the club name used in the search prompt; None if no such MP. Sync (Sejm API, cached)."""
    mp = politicians.get_politician(mp_id)
    if mp is None:
        return None
    official = None
    try:
        official = next((c.name for c in politicians.list_clubs() if c.id == mp.club), None)
    except httpx.HTTPError:
        logger.warning("Sejm clubs unavailable", exc_info=True)
    return mp, club_name(mp.club, official)


async def fetch_news(session: AsyncSession, mp: Politician, club: str | None, now: datetime) -> list[NewsItem]:
    settings = get_settings()
    await ensure_budget(session)

    async def on_usage(usage) -> None:
        await record_usage(session, "mp_news", settings.mp_news_model, usage)

    news = await search_mp_news(
        get_claude_client(),
        model=settings.mp_news_model,
        effort=settings.mp_news_effort,
        full_name=mp.full_name,
        club=club,
        district=mp.district,
        today=now,
        since=now - timedelta(days=WINDOW_DAYS),
        max_items=MAX_ITEMS,
        max_searches=settings.mp_news_max_searches,
        blocked_domains=settings.mp_news_blocked_domains,
        on_usage=on_usage,
    )
    return build_items(
        news, last_name=mp.last_name, today=now.date(), blocked_domains=settings.mp_news_blocked_domains
    )


# --- storage and freshness ---

_locks: defaultdict[tuple[int, int], asyncio.Lock] = defaultdict(asyncio.Lock)
_refreshing: set[tuple[int, int]] = set()
_tasks: set[asyncio.Task] = set()  # keeps background refreshes referenced until they finish


def _now() -> datetime:
    return datetime.now(UTC)


async def _load(session: AsyncSession, term: int, mp_id: int) -> MpNewsCache | None:
    return await session.get(MpNewsCache, (term, mp_id), populate_existing=True)


async def _store(session: AsyncSession, term: int, mp_id: int, **values) -> MpNewsCache:
    for attempt in range(2):
        row = await _load(session, term, mp_id)
        if row is None:
            row = MpNewsCache(term=term, mp_id=mp_id, status="unavailable", items=[], blocked=False)
            session.add(row)
        for key, value in values.items():
            setattr(row, key, value)
        try:
            await session.commit()
            return row
        except IntegrityError:  # another worker inserted the same MP meanwhile: update its row instead
            await session.rollback()
            if attempt:
                raise
    raise AssertionError("unreachable")


def _is_stale(row: MpNewsCache, now: datetime) -> bool:
    if row.blocked:
        return False
    if row.generated_at is None:
        return True
    return now - row.generated_at > timedelta(hours=get_settings().mp_news_ttl_hours)


def _backing_off(row: MpNewsCache | None, now: datetime) -> bool:
    if row is None or row.error_at is None:
        return False
    return now - row.error_at < timedelta(minutes=get_settings().mp_news_retry_minutes)


def _can_fetch(row: MpNewsCache | None, now: datetime) -> bool:
    return bool(get_settings().anthropic_api_key) and not _backing_off(row, now) and (row is None or _is_stale(row, now))


async def _refresh(session: AsyncSession, mp: Politician, club: str | None) -> MpNewsCache:
    now = _now()
    try:
        items = await asyncio.wait_for(
            fetch_news(session, mp, club, now), timeout=get_settings().mp_news_timeout_seconds
        )
    except Exception as exc:  # provider trouble or budget used up: keep what is stored, retry after the backoff
        logger.warning("MP news fetch failed for %s (term %s)", mp.mp_id, mp.term, exc_info=True)
        return await _store(session, mp.term, mp.mp_id, error_at=now, error=f"{type(exc).__name__}: {exc}"[:2000])
    logger.info(
        "MP news stored for %s (term %s): %s",
        mp.mp_id,
        mp.term,
        [(i.headline, [s.url for s in i.sources]) for i in items] or "no items",
    )
    return await _store(
        session,
        mp.term,
        mp.mp_id,
        status="ok" if items else "empty",
        items=[i.model_dump(mode="json") for i in items],
        generated_at=now,
        error_at=None,
        error=None,
    )


async def _refresh_locked(session: AsyncSession, mp: Politician, club: str | None) -> MpNewsCache | None:
    """Refresh unless another request did it while this one waited for the lock."""
    async with _locks[(mp.term, mp.mp_id)]:
        row = await _load(session, mp.term, mp.mp_id)
        if not _can_fetch(row, _now()):
            return row
        return await _refresh(session, mp, club)


def _open_session() -> AsyncSession:
    return get_session_factory()()


async def _refresh_in_background(mp: Politician, club: str | None) -> None:
    key = (mp.term, mp.mp_id)
    try:
        async with _open_session() as session:
            await _refresh_locked(session, mp, club)
    except Exception:
        logger.exception("Background MP news refresh failed for %s", mp.mp_id)
    finally:
        _refreshing.discard(key)


def _start_background_refresh(mp: Politician, club: str | None) -> None:
    key = (mp.term, mp.mp_id)
    if key in _refreshing:
        return
    _refreshing.add(key)
    task = asyncio.create_task(_refresh_in_background(mp, club))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def to_api(mp_id: int, row: MpNewsCache | None) -> MpNews:
    if row is None or row.generated_at is None:
        return MpNews(mp_id=mp_id, status="unavailable", generated_at=None, items=[])
    items = [NewsItem.model_validate(i) for i in row.items or []]
    return MpNews(
        mp_id=mp_id,
        status="ok" if items else "empty",
        generated_at=row.generated_at,
        items=items,
    )


async def get_mp_news(session: AsyncSession, mp: Politician, club: str | None) -> MpNews:
    """Stored result if fresh. Stale: stored result now, refresh in the background. Never fetched: fetch now."""
    now = _now()
    row = await _load(session, mp.term, mp.mp_id)
    if _can_fetch(row, now):
        if row is None or row.generated_at is None:
            row = await _refresh_locked(session, mp, club)
        else:
            _start_background_refresh(mp, club)
    return to_api(mp.mp_id, row)
