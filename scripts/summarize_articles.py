"""Titles and plain-language summaries of loaded articles (scripts.ingest_acts) by Gemini, checked against the text.

    docker compose exec backend python -m scripts.summarize_articles                 # all missing summaries
    docker compose exec backend python -m scripts.summarize_articles DU/1997/553 --limit 20

    # free tier (no billing): a model with a usable daily quota, several articles per request, below its RPM
    docker compose exec backend python -m scripts.summarize_articles --model gemini-3.5-flash-lite --rpm 12 --concurrency 2

Each article gets a title ("Stawka akcyzy na płyn do e-papierosów") and a 1-3 sentence summary. Several articles
go in one request (--batch-size), which is what makes the free tier's requests-per-day quota last.

Only articles missing a title or summary are sent (ingest_acts clears both when the wording changes) and
progress is saved after every request, so the script can be stopped and rerun; it stops by itself when the
quota runs out. A result with numbers or article numbers not found in the article is not saved; such articles
are listed at the end and stay without a summary. Run scripts.index_articles afterwards.
"""

import argparse
import asyncio
import logging
import time
from datetime import UTC, datetime

from google.genai import errors
from sqlalchemy import func, or_, select
from sqlalchemy.orm import joinedload

from app.db.models import Act, Article, Layer, SourceDocument
from app.db.session import get_session_factory
from app.pipeline.act_parser import is_repealed
from app.utils.gemini_client import get_gemini_client
from app.utils.gemini_client.tasks import ArticleInput, UngroundedSummaryError, summarize_articles

logger = logging.getLogger("summarize_articles")

CONCURRENCY = 8
BATCH_SIZE = 10
# A request is closed early when its articles add up to this much text; a longer article goes alone.
BATCH_CHARS = 24_000


def make_batches(articles: list[Article], size: int, max_chars: int = BATCH_CHARS) -> list[list[Article]]:
    batches: list[list[Article]] = []
    chars = 0
    for article in articles:
        if not batches or len(batches[-1]) >= size or chars + len(article.text) > max_chars:
            batches.append([])
            chars = 0
        batches[-1].append(article)
        chars += len(article.text)
    return batches


class Pace:
    """At most `rpm` request starts per minute (no limit when None)."""

    def __init__(self, rpm: float | None) -> None:
        self.interval = 60 / rpm if rpm else 0.0
        self.next_start = 0.0
        self.lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self.lock:
            delay = self.next_start - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self.next_start = time.monotonic() + self.interval


async def mark_summarized_acts() -> None:
    """Source documents of acts with every (non-repealed) article summarized move to the silver layer."""
    async with get_session_factory()() as session:
        for act in (await session.scalars(select(Act).options(joinedload(Act.articles)))).unique():
            if any(a.summary is None and not is_repealed(a.text) for a in act.articles):
                continue
            documents = await session.scalars(
                select(SourceDocument).where(SourceDocument.act_key == act.act_key, SourceDocument.layer == Layer.BRONZE)
            )
            for document in documents:
                document.layer = Layer.SILVER
                document.silver_at = datetime.now(UTC)
        await session.commit()


async def main(
    elis: list[str], limit: int | None, concurrency: int, batch_size: int, rpm: float | None, model: str | None
) -> None:
    gemini = get_gemini_client()  # fails fast without GEMINI_API_KEY
    query = (
        select(Article)
        .join(Article.act)
        .options(joinedload(Article.act))
        .where(or_(Article.summary.is_(None), Article.title.is_(None)))
    )
    if elis:
        query = query.where(Act.eli.in_(elis))
    query = query.order_by(Act.id, Article.position)

    async with get_session_factory()() as session:
        pending = [a for a in (await session.scalars(query)).all() if not is_repealed(a.text)][:limit]
        total = await session.scalar(select(func.count(Article.id)))
        await session.commit()  # no open transaction while waiting for Gemini
        batches = make_batches(pending, batch_size)
        print(
            f"{len(pending)} articles to summarize ({total} in the database) in {len(batches)} requests, "
            f"model {model or gemini.model}",
            flush=True,
        )

        semaphore = asyncio.Semaphore(concurrency)
        pace = Pace(rpm)
        save = asyncio.Lock()
        failed: list[str] = []
        done = 0
        out_of_quota = False

        async def run(batch: list[Article]) -> None:
            nonlocal done, out_of_quota
            async with semaphore:
                if out_of_quota:
                    return
                await pace.wait()
                inputs = [ArticleInput(a.act.short_title or a.act.title, a.number, a.text) for a in batch]
                try:
                    results = await summarize_articles(gemini, inputs, model=model)
                except errors.ClientError as exc:
                    if exc.code == 429:  # still refused after all the retries: the quota is used up
                        out_of_quota = True
                        return
                    results = [exc] * len(batch)
                except Exception as exc:  # one bad request must not stop the run
                    results = [exc] * len(batch)

            async with save:
                for article, result in zip(batch, results, strict=True):
                    if isinstance(result, UngroundedSummaryError):
                        failed.append(f"{article.slug}: {result}")
                    elif isinstance(result, BaseException):
                        logger.warning("%s failed: %s", article.slug, result)
                        failed.append(f"{article.slug}: {type(result).__name__}")
                    else:
                        article.title, article.summary = result.title, result.summary
                await session.commit()
                done += len(batch)
                print(f"{done}/{len(pending)}", flush=True)

        await asyncio.gather(*(run(batch) for batch in batches))

    await mark_summarized_acts()
    if out_of_quota:
        print("Gemini quota exhausted - stopped. What was done is saved; rerun later (or with another --model).")
    print(f"Done: {done - len(failed)} summarized, {len(failed)} without a summary" + "".join(f"\n  {f}" for f in failed))


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("eli", nargs="*", help="Only these key acts, e.g. DU/1997/553")
    parser.add_argument("--limit", type=int, help="At most this many articles (e.g. to check quality first)")
    parser.add_argument("--concurrency", type=int, default=CONCURRENCY, help="Parallel Gemini requests")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE, help="Articles per request")
    parser.add_argument("--rpm", type=float, help="At most this many requests per minute (free tier: below its RPM)")
    parser.add_argument("--model", help="Gemini model for this run instead of GEMINI_MODEL")
    args = parser.parse_args()
    asyncio.run(main(args.eli, args.limit, args.concurrency, args.batch_size, args.rpm, args.model))
