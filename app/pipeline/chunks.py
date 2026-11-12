"""Article summaries cut into the parts embedded in Qdrant (collection act_articles, see ActArticleChunk)."""

import re
import uuid
from collections.abc import Callable

from app.vectorstore.collections import ActArticleChunk

# bge-m3 takes 8192 tokens, but a shorter part keeps one topic per vector, which matches questions better.
MAX_PART_TOKENS = 400
_SENTENCE_END = re.compile(r"(?<=[.!?;])\s+")


def embedded_text(act_title: str, article_number: str, part: str) -> str:
    return f"{act_title}\nArt. {article_number}\n{part}"


def split_summary(summary: str, count_tokens: Callable[[str], int], max_tokens: int = MAX_PART_TOKENS) -> list[str]:
    """Whole sentences per part; a single sentence longer than the limit stays one part."""
    parts: list[str] = []
    for sentence in _SENTENCE_END.split(summary.strip()):
        if parts and count_tokens(f"{parts[-1]} {sentence}") <= max_tokens:
            parts[-1] = f"{parts[-1]} {sentence}"
        else:
            parts.append(sentence)
    return parts


def article_chunks(
    *,
    slug: str,
    act_key: str,
    act_title: str,
    article_number: str,
    summary: str,
    source_sha256: str,
    count_tokens: Callable[[str], int],
) -> list[tuple[str, str, ActArticleChunk]]:
    """[(point id, text to embed, metadata)]. Ids are derived from slug and part, so reindexing overwrites."""
    parts = split_summary(summary, count_tokens)
    return [
        (
            str(uuid.uuid5(uuid.NAMESPACE_URL, f"{slug}#{index}")),
            embedded_text(act_title, article_number, part),
            ActArticleChunk(
                slug=slug,
                act_key=act_key,
                act_title=act_title,
                article_number=article_number,
                part=index,
                part_count=len(parts),
                summary=summary,
                source_sha256=source_sha256,
            ),
        )
        for index, part in enumerate(parts)
    ]
