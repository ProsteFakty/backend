"""Admin tool for "Ostatnio w mediach" (docs/specs/mp-media-news.md), current term.

    python -m scripts.mp_news --show 123      # stored result
    python -m scripts.mp_news --refresh 123   # fetch now (calls Claude, counts toward the budget), store and show
    python -m scripts.mp_news --clear 123     # forget the stored result (refetched on the next view)
    python -m scripts.mp_news --block 123     # takedown: serve nothing for this MP until --clear
    python -m scripts.mp_news --spend         # Claude spending this month vs the budget
"""

import argparse
import asyncio
import json
import logging

from sqlalchemy import delete

from app.config import get_settings
from app.db.models import Base, MpNewsCache
from app.db.session import get_engine, get_session_factory
from app.services import mp_news
from app.utils.claude_client import spent_this_month


async def main(action: str, mp_id: int | None) -> None:
    term = get_settings().sejm_term
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with get_session_factory()() as session:
        if action == "spend":
            spent = await spent_this_month(session)
            print(f"Claude this month: ${spent:.4f} of ${get_settings().claude_monthly_budget_usd:.2f}")
            return
        if action == "clear":
            await session.execute(delete(MpNewsCache).where(MpNewsCache.term == term, MpNewsCache.mp_id == mp_id))
            await session.commit()
            print(f"Cleared MP {mp_id}")
            return
        if action == "block":
            await mp_news._store(
                session, term, mp_id, status="empty", items=[],
                generated_at=mp_news._now(), blocked=True,
            )
            print(f"Blocked MP {mp_id}")
            return
        if action == "refresh":
            subject = await asyncio.to_thread(mp_news.news_subject, mp_id)
            if subject is None:
                raise SystemExit(f"No MP {mp_id} in term {term}")
            await mp_news._refresh(session, *subject)
        row = await mp_news._load(session, term, mp_id)
        if row is not None and row.error:
            print(f"Last error ({row.error_at}): {row.error}")
        print(json.dumps(mp_news.to_api(mp_id, row).model_dump(mode="json"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    for name in ("show", "refresh", "clear", "block"):
        group.add_argument(f"--{name}", type=int, metavar="MP_ID")
    group.add_argument("--spend", action="store_true")
    args = parser.parse_args()
    action = "spend" if args.spend else next(n for n in ("show", "refresh", "clear", "block") if getattr(args, n) is not None)
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main(action, None if action == "spend" else getattr(args, action)))
