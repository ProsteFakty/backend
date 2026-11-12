"""Plain-language summaries of loaded articles (scripts.ingest_acts) by Gemini, checked against the article text.

    docker compose exec backend python -m scripts.summarize_articles                 # all missing summaries
    docker compose exec backend python -m scripts.summarize_articles DU/1997/553 --limit 20

Only articles without a summary are sent (ingest_acts clears the summary when the wording changes), so the
script can be stopped and rerun. A summary with numbers or article numbers not found in the article is not
saved; such articles are listed at the end and stay without a summary.
"""

import argparse
import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from app.db.models import Act, Article, Layer, SourceDocument
from app.db.session import get_session_factory
from app.pipeline.act_parser import is_repealed
from app.utils.gemini_client import get_gemini_client
from app.utils.gemini_client.tasks import UngroundedSummaryError, summarize_article

logger = logging.getLogger("summarize_articles")

CONCURRENCY = 8
BATCH = 50


async def mark_summarized_acts() -> None:
    """Source documents of acts with every (non-repealed) article summarized move to the silver layer."""
    async with get_session_factory()() as session:
        for act in await session.scalars(select(Act).options(joinedload(Act.articles))):
            if any(a.summary is None and not is_repealed(a.text) for a in act.articles):
                continue
            documents = await session.scalars(
                select(SourceDocument).where(SourceDocument.act_key == act.act_key, SourceDocument.layer == Layer.BRONZE)
            )
            for document in documents:
                document.layer = Layer.SILVER
                document.silver_at = datetime.now(UTC)
        await session.commit()


async def main(elis: list[str], limit: int | None) -> None:
    gemini = get_gemini_client()  # fails fast without GEMINI_API_KEY
    query = select(Article).join(Article.act).options(joinedload(Article.act)).where(Article.summary.is_(None))
    if elis:
        query = query.where(Act.eli.in_(elis))
    query = query.order_by(Act.id, Article.position)

    async with get_session_factory()() as session:
        pending = [a for a in (await session.scalars(query)).all() if not is_repealed(a.text)][:limit]
        total = await session.scalar(select(func.count(Article.id)))
        print(f"{len(pending)} articles to summarize ({total} in the database)", flush=True)

        semaphore = asyncio.Semaphore(CONCURRENCY)
        failed: list[str] = []

        async def summarize(article: Article) -> str:
            async with semaphore:
                title = article.act.short_title or article.act.title
                return await summarize_article(gemini, title, article.number, article.text)

        for start in range(0, len(pending), BATCH):
            batch = pending[start : start + BATCH]
            results = await asyncio.gather(*(summarize(a) for a in batch), return_exceptions=True)
            for article, result in zip(batch, results, strict=True):
                if isinstance(result, UngroundedSummaryError):
                    failed.append(f"{article.slug}: {result}")
                elif isinstance(result, BaseException):
                    logger.warning("%s failed: %s", article.slug, result)
                    failed.append(f"{article.slug}: {type(result).__name__}")
                else:
                    article.summary = result
            await session.commit()
            print(f"{min(start + BATCH, len(pending))}/{len(pending)}", flush=True)

    await mark_summarized_acts()
    print(f"Done, {len(failed)} without a summary" + "".join(f"\n  {f}" for f in failed))


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("eli", nargs="*", help="Only these key acts, e.g. DU/1997/553")
    parser.add_argument("--limit", type=int, help="At most this many articles (e.g. to check quality first)")
    args = parser.parse_args()
    asyncio.run(main(args.eli, args.limit))
