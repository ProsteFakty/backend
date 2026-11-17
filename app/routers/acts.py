from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.schemas.acts import ActDetail, ActListItem
from app.services import acts as service

router = APIRouter(prefix="/api/v1/acts", tags=["acts"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("", summary="Acts available in the law search")
async def list_acts(session: SessionDep) -> list[ActListItem]:
    return await service.list_acts(session)


@router.get("/{act_key}", summary="Act with the list of its articles")
async def get_act(act_key: str, session: SessionDep) -> ActDetail:
    """To search inside this act pass its `act_key` to POST /api/v1/articles/search."""
    act = await service.get_act(session, act_key)
    if act is None:
        raise HTTPException(404, f"Act '{act_key}' not found")
    return act
