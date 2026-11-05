from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.session import get_session
from app.schemas.sittings import Page, SittingDetail, SittingListItem
from app.services import sittings as service
from app.services.sejm_client import SejmClient, get_sejm_client

router = APIRouter(prefix="/api/v1/sittings", tags=["sittings"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SejmDep = Annotated[SejmClient, Depends(get_sejm_client)]

SEJM_UNAVAILABLE = "Sejm API is unavailable and this data is not stored yet"


@router.get("", summary="Paged list of Sejm sittings, newest first")
async def list_sittings(
    session: SessionDep,
    sejm: SejmDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 3,
) -> Page[SittingListItem]:
    try:
        return await service.list_sittings(session, sejm, get_settings().sejm_term, page, page_size)
    except httpx.HTTPError as exc:
        raise HTTPException(502, SEJM_UNAVAILABLE) from exc


@router.get("/{number}", summary="One sitting: bills, results, summaries, club votes, turnout")
async def get_sitting(number: int, session: SessionDep, sejm: SejmDep) -> SittingDetail:
    try:
        sitting = await service.get_sitting(session, sejm, get_settings().sejm_term, number)
    except httpx.HTTPError as exc:
        raise HTTPException(502, SEJM_UNAVAILABLE) from exc
    if sitting is None:
        raise HTTPException(404, "Sitting not found")
    return sitting
