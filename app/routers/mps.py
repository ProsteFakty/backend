from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.session import get_session
from app.schemas.mps import MpVoteOut, VoteValue
from app.schemas.sittings import Page
from app.services import mp_votes as service
from app.services.sejm_client import SejmClient, get_sejm_client

router = APIRouter(prefix="/api/v1/mps", tags=["mps"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SejmDep = Annotated[SejmClient, Depends(get_sejm_client)]


@router.get(
    "/{mp_id}/votes",
    summary="How an MP voted on bills as a whole, newest first",
    responses={
        404: {"description": "No MP with this id in the current term"},
        502: {"description": "Sejm API unavailable and nothing stored yet"},
        503: {"description": "Votes of the term are still being loaded; retry after `Retry-After` seconds"},
    },
)
async def list_mp_votes(
    mp_id: int,
    session: SessionDep,
    sejm: SejmDep,
    vote: Annotated[VoteValue | None, Query(description="Only votes of this kind; omit for all")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[MpVoteOut]:
    try:
        votes = await service.list_mp_votes(session, sejm, get_settings().sejm_term, mp_id, vote, page, page_size)
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Sejm API is unavailable and this data is not stored yet") from exc
    except service.BackfillInProgress as exc:
        raise HTTPException(503, "MP votes are still being loaded", headers={"Retry-After": "30"}) from exc
    if votes is None:
        raise HTTPException(404, "MP not found")
    return votes
