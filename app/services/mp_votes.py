"""How individual MPs voted on bills as a whole, served from Postgres (spec: docs/specs/mp-votes.md).

Individual votes are stored only for final electronic votings. They come from two places:
- the regular sitting sync (`sittings._build_voting`), for votings stored from now on
- the backfill below: for every sitting that started, the final votings are pulled once (re-pulled every
  LIVE_TTL while the sitting is in progress), including votings stored before MP votes were kept

A cold backfill of a whole term takes minutes, so requests only sync inline when a couple of sittings
are stale (a live sitting); otherwise the backfill runs in the background (also started on app startup)
and requests serve what is stored.
"""

import asyncio
import logging
import math
from collections.abc import Iterable
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import and_, exists, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Bill, MpVote, Sitting, SittingMpVotesSync, Voting, VotingClubResult
from app.schemas.mps import MpVoteOut
from app.db.session import get_session_factory
from app.schemas.sittings import Page
from app.services import sittings
from app.services.sejm_client import get_sejm_client
from app.services.sejm_client import SejmClient
from app.services.sittings import BILL, build_mp_votes, is_final, print_number, vote_value

__all__ = ["BackfillInProgress", "ensure_mp_votes", "final_candidates", "list_mp_votes", "start_backfill", "vote_value"]

logger = logging.getLogger(__name__)

INLINE_SITTINGS = 2  # at most this many stale sittings are synced inside a request
BATCH_SITTINGS = 10  # sittings fetched concurrently and committed together by the backfill
_BACKFILL = -1  # key of the backfill lock in sittings._locks, next to 0 = the sitting list
_background: dict[int, asyncio.Task[None]] = {}


class BackfillInProgress(Exception):
    """The MP has no stored votes yet, but the term is still being backfilled."""


def final_candidates(listing: Iterable[dict[str, Any]]) -> list[int]:
    """Numbers of final electronic votings in a sitting's voting listing (it has topic and description)."""
    return [
        v["votingNumber"]
        for v in listing
        if v["kind"] == "ELECTRONIC" and is_final(v.get("topic"), v.get("description"))
    ]


async def list_mp_votes(
    session: AsyncSession,
    sejm: SejmClient,
    term: int,
    mp_id: int,
    vote: str | None,
    page: int,
    page_size: int,
) -> Page[MpVoteOut] | None:
    """Final votes on bills of one MP, newest first; None when the MP does not exist in the term.

    Raises BackfillInProgress when nothing is stored for the MP yet and the background backfill runs.
    """
    await _refresh(session, sejm, term)

    of_mp = (
        select(MpVote.id)
        .join(MpVote.voting)
        .join(Voting.sitting)
        .join(Voting.bill)
        .where(MpVote.mp_id == mp_id, Sitting.term == term, Voting.kind == "ELECTRONIC", Bill.document_type == BILL)
    )
    filtered = of_mp.where(MpVote.vote == vote) if vote else of_mp
    total = await session.scalar(select(func.count()).select_from(filtered.subquery())) or 0
    if not total and not await session.scalar(select(exists(of_mp))):
        if backfill_running(term):
            raise BackfillInProgress
        if await sejm.mp(term, mp_id) is None:
            return None

    rows = await session.execute(
        select(MpVote, Voting, Sitting.number, Bill, VotingClubResult)
        .join(MpVote.voting)
        .join(Voting.sitting)
        .join(Voting.bill)
        .outerjoin(
            VotingClubResult,
            and_(VotingClubResult.voting_id == Voting.id, VotingClubResult.club == MpVote.club),
        )
        .where(MpVote.id.in_(filtered))
        .order_by(Voting.held_at.desc(), Sitting.number.desc(), Voting.number.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    rows = rows.all()
    # Same lazy Gemini summaries as the sitting detail, only for the bills this page returns.
    await sittings._fill_summaries(session, {bill for _, _, _, bill, _ in rows})
    items = [
        MpVoteOut(
            sitting=sitting_number,
            voting_number=voting.number,
            held_at=voting.held_at,
            print_number=bill.print_number,
            title=bill.title,
            summary=bill.summary,
            source_url=bill.source_url,
            vote=mp_vote.vote,
            club=mp_vote.club,
            club_position=club.position if club else None,
        )
        for mp_vote, voting, sitting_number, bill, club in rows
    ]
    return Page(items=items, page=page, page_size=page_size, total=total, pages=math.ceil(total / page_size))


def backfill_running(term: int) -> bool:
    task = _background.get(term)
    return task is not None and not task.done()


def start_backfill(term: int) -> None:
    """Run ensure_mp_votes for the term in the background with its own session, unless it already runs."""
    if not backfill_running(term):
        _background[term] = asyncio.create_task(_backfill_in_background(term))


async def _backfill_in_background(term: int) -> None:
    try:
        async with get_session_factory()() as session:
            await ensure_mp_votes(session, get_sejm_client(), term)
    except Exception:
        logger.exception("Background backfill of MP votes for term %s failed", term)


async def _refresh(session: AsyncSession, sejm: SejmClient, term: int) -> None:
    if backfill_running(term):
        return
    stale = await _stale_sittings(session, sejm, term)
    if len(stale) > INLINE_SITTINGS:
        start_backfill(term)
    elif stale:
        await ensure_mp_votes(session, sejm, term)


async def _stale_sittings(session: AsyncSession, sejm: SejmClient, term: int) -> list[Sitting]:
    async with sittings._locks[(term, 0)]:
        await sittings._ensure_sittings(session, sejm, term)
    rows = await session.execute(
        select(Sitting, SittingMpVotesSync.synced_at)
        .outerjoin(SittingMpVotesSync, SittingMpVotesSync.sitting_id == Sitting.id)
        .where(Sitting.term == term)
        .order_by(Sitting.number)
    )
    return [sitting for sitting, synced_at in rows if sittings.is_stale(sitting, synced_at)]


async def ensure_mp_votes(session: AsyncSession, sejm: SejmClient, term: int) -> None:
    """Backfill final votings with MP votes for every stale sitting of the term.

    Commits per batch of sittings, so progress survives a failure. When the Sejm API fails and some
    sittings are already backfilled, they are served as they are; otherwise the error propagates (502).
    """
    async with sittings._locks[(term, _BACKFILL)]:
        stale = await _stale_sittings(session, sejm, term)
        logger.info("Backfilling MP votes of %d sittings of term %s", len(stale), term)
        for i in range(0, len(stale), BATCH_SITTINGS):
            try:
                await _backfill_batch(session, sejm, term, stale[i : i + BATCH_SITTINGS])
            except httpx.HTTPError:
                await session.rollback()
                if not await session.scalar(select(exists(select(SittingMpVotesSync.sitting_id)))):
                    raise
                logger.warning("Sejm API unavailable, serving stored MP votes of term %s", term)
                return


async def _backfill_batch(session: AsyncSession, sejm: SejmClient, term: int, batch: list[Sitting]) -> None:
    semaphore = asyncio.Semaphore(sittings.SEJM_CONCURRENCY)

    async def limited(coro):
        async with semaphore:
            return await coro

    async with AsyncExitStack() as stack:
        # Same per-sitting locks as the sitting detail sync, so both never insert the same voting.
        for sitting in batch:
            await stack.enter_async_context(sittings._locks[(term, sitting.number)])

        listings = await asyncio.gather(*(limited(sejm.votings(term, s.number)) for s in batch))
        candidates = {s.id: set(final_candidates(listing)) for s, listing in zip(batch, listings, strict=True)}
        stored = {
            (v.sitting_id, v.number): v
            for v in await session.scalars(
                select(Voting)
                .where(Voting.sitting_id.in_(candidates), Voting.is_final)
                .options(selectinload(Voting.mp_votes))
            )
        }
        todo = [
            (s, n)
            for s in batch
            for n in sorted(candidates[s.id])
            if (s.id, n) not in stored or not stored[(s.id, n)].mp_votes
        ]
        details = await asyncio.gather(*(limited(sejm.voting(term, s.number, n)) for s, n in todo))

        new = [d for (s, n), d in zip(todo, details, strict=True) if (s.id, n) not in stored]
        titles = {p: sittings._AGENDA_POINT.sub("", d["title"]) for d in new if (p := print_number(d["title"]))}
        bills = await sittings._ensure_bills(session, sejm, term, titles)
        for (sitting, number), raw in zip(todo, details, strict=True):
            if voting := stored.get((sitting.id, number)):
                voting.mp_votes = build_mp_votes(raw)
            else:
                session.add(sittings._build_voting(sitting, raw, bills.get(print_number(raw["title"]))))

        now = datetime.now(UTC)
        await session.execute(
            insert(SittingMpVotesSync)
            .values([{"sitting_id": s.id, "synced_at": now} for s in batch])
            .on_conflict_do_update(index_elements=[SittingMpVotesSync.sitting_id], set_={"synced_at": now})
        )
        await session.commit()
