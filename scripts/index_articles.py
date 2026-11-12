"""Embed article summaries (scripts.summarize_articles) into Qdrant for POST /api/v1/articles/search.

    docker compose exec backend python -m scripts.index_articles                # all acts
    docker compose exec backend python -m scripts.index_articles DU/1997/553

Each act is reindexed whole: its old points are deleted first, so removed articles disappear from search.
Articles without a summary are not indexed. The first run downloads the embedding model (~2 GB).
"""

import argparse
import asyncio
from datetime import UTC, datetime

from qdrant_client import models
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.db.models import Act, Layer, SourceDocument
from app.db.session import get_session_factory
from app.pipeline.chunks import article_chunks
from app.vectorstore import ActArticleChunk, get_vector_store
from app.vectorstore.embeddings import get_qdrant_client, get_tokenizer


def index_act(act: Act, source_sha256: str) -> int:
    """Replace the act's points in Qdrant; returns the number of points written. Blocking (embeds on the CPU)."""
    tokenizer = get_tokenizer()
    count_tokens = lambda text: len(tokenizer.encode(text, add_special_tokens=False))  # noqa: E731
    title = act.short_title or act.title
    chunks = [
        chunk
        for article in act.articles
        if article.summary
        for chunk in article_chunks(
            slug=article.slug,
            act_key=act.act_key,
            act_title=title,
            article_number=article.number,
            summary=article.summary,
            source_sha256=source_sha256,
            count_tokens=count_tokens,
        )
    ]

    store = get_vector_store(ActArticleChunk)
    get_qdrant_client().delete(
        ActArticleChunk.collection_name,
        points_selector=models.FilterSelector(filter=ActArticleChunk.field_filter(act_key=act.act_key)),
    )
    if chunks:
        ids, texts, metadata = zip(*chunks, strict=True)
        store.add_texts(list(texts), metadatas=[m.model_dump() for m in metadata], ids=list(ids), batch_size=64)
    return len(chunks)


async def main(elis: list[str]) -> None:
    async with get_session_factory()() as session:
        query = select(Act).options(joinedload(Act.articles)).order_by(Act.id)
        if elis:
            query = query.where(Act.eli.in_(elis))
        acts = (await session.scalars(query)).unique().all()

        for act in acts:
            document = await session.scalar(
                select(SourceDocument).where(SourceDocument.act_key == act.act_key).order_by(SourceDocument.id.desc())
            )
            sha = document.sha256 if document else ""
            summarized = sum(1 for a in act.articles if a.summary)
            count = await asyncio.to_thread(index_act, act, sha)
            print(f"{act.short_title}: {summarized}/{len(act.articles)} articles with a summary, {count} points", flush=True)
            if document is not None and count:
                document.chunk_count = count
                if document.layer == Layer.SILVER:
                    document.layer = Layer.GOLD
                    document.gold_at = datetime.now(UTC)
        await session.commit()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("eli", nargs="*", help="Only these key acts, e.g. DU/1997/553")
    args = parser.parse_args()
    asyncio.run(main(args.eli))
