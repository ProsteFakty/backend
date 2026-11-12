"""Pull individual MP votes of all final votings of the current term, so no request waits for it.

Run once after deploy (and any time later; already synced sittings are skipped):
    python -m scripts.backfill_mp_votes
"""

import asyncio
import logging

from app.config import get_settings
from app.db.models import Base
from app.db.session import get_engine, get_session_factory
from app.services.mp_votes import ensure_mp_votes
from app.services.sejm_client import get_sejm_client


async def main() -> None:
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with get_session_factory()() as session:
        await ensure_mp_votes(session, get_sejm_client(), get_settings().sejm_term)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
