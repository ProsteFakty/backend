"""Load the key acts (app.pipeline.key_acts) from the Sejm ELI API into Postgres: act, articles, references.

    docker compose exec backend python -m scripts.ingest_acts            # all key acts
    docker compose exec backend python -m scripts.ingest_acts DU/1997/553 --dry-run   # parse only, no DB

Idempotent: an act whose text file did not change is skipped (--force to redo). Articles keep their summary
when their text did not change. PDFs are cached in ACTS_DATA_DIR.
"""

import argparse
import asyncio
import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import Act, Article, Base, DocumentStatus, Layer, SourceDocument
from app.db.session import get_engine, get_session_factory
from app.pipeline.act_keys import make_article_slug, parse_iso_date
from app.pipeline.act_parser import ParsedArticle, parse_articles
from app.pipeline.eli import ActSource, act_key_of, download, find_source
from app.pipeline.key_acts import KEY_ACTS, KeyAct
from app.pipeline.pdf_text import extract_text
from app.pipeline.references import ActRef, extract_references

logger = logging.getLogger("ingest_acts")


@dataclass
class ParsedAct:
    key_act: KeyAct
    act_key: str
    source: ActSource
    pdf: bytes
    page_count: int
    articles: list[ParsedArticle]
    references: dict[str, list[dict]]  # article number -> references
    unresolved: int  # same-act references to articles that are not in the parsed text

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.pdf).hexdigest()


async def fetch_pdf(client: httpx.AsyncClient, source: ActSource) -> bytes:
    cache = Path(get_settings().acts_data_dir) / source.text_eli.replace("/", "_") / source.file_name
    if cache.exists():
        return cache.read_bytes()
    pdf = await download(client, source)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(pdf)
    return pdf


def parse(key_act: KeyAct, act_key: str, source: ActSource, pdf: bytes, act_refs: list[ActRef]) -> ParsedAct:
    text, page_count = extract_text(pdf)
    articles = parse_articles(text)
    own_slugs = {make_article_slug(act_key, a.number) for a in articles}
    references = {a.number: extract_references(a.text, act_key, act_refs, a.number) for a in articles}
    unresolved = sum(
        1 for refs in references.values() for ref in refs if ref["same_act"] and ref["slug"] not in own_slugs
    )
    return ParsedAct(key_act, act_key, source, pdf, page_count, articles, references, unresolved)


async def save(session: AsyncSession, parsed: ParsedAct) -> None:
    meta = parsed.source.act
    act = await session.scalar(select(Act).where(Act.act_key == parsed.act_key))
    if act is None:
        act = Act(act_key=parsed.act_key)
        session.add(act)
    act.eli = meta["ELI"]
    act.act_type = meta["type"].lower()
    act.act_date = parse_iso_date(meta.get("announcementDate"))
    act.title = meta["title"]
    act.short_title = parsed.key_act.short_title
    await session.flush()

    existing = {a.number: a for a in await session.scalars(select(Article).where(Article.act_id == act.id))}
    for position, parsed_article in enumerate(parsed.articles):
        article = existing.pop(parsed_article.number, None)
        if article is None:
            article = Article(act_id=act.id, number=parsed_article.number)
            session.add(article)
        elif article.text != parsed_article.text:
            article.summary = None  # summary of the old wording
        article.slug = make_article_slug(parsed.act_key, parsed_article.number)
        article.position = position
        article.text = parsed_article.text
        article.references = parsed.references[parsed_article.number]
    if existing:  # articles no longer in the act
        await session.execute(delete(Article).where(Article.id.in_([a.id for a in existing.values()])))

    document = await session.scalar(select(SourceDocument).where(SourceDocument.sha256 == parsed.sha256))
    if document is None:
        document = SourceDocument(sha256=parsed.sha256)
        session.add(document)
    document.file_name = parsed.source.file_name
    document.file_path = parsed.source.url
    document.size_bytes = len(parsed.pdf)
    document.page_count = parsed.page_count
    document.pdf_metadata = {
        "act_eli": meta["ELI"],
        "text_eli": parsed.source.text_eli,
        "text_kind": parsed.source.text_kind,
        "legal_status_date": str(parsed.source.legal_status_date or ""),
    }
    document.layer = Layer.BRONZE  # silver once the articles have summaries
    document.status = DocumentStatus.DONE
    document.error = None
    document.act_key = parsed.act_key
    document.article_count = len(parsed.articles)
    document.unresolved_references = parsed.unresolved
    await session.commit()


async def already_loaded(session: AsyncSession, sha256: str) -> bool:
    document = await session.scalar(select(SourceDocument).where(SourceDocument.sha256 == sha256))
    return document is not None and document.status == DocumentStatus.DONE


async def main(elis: list[str], dry_run: bool, force: bool) -> None:
    key_acts = [a for a in KEY_ACTS if not elis or a.eli in elis]
    if not dry_run:
        async with get_engine().begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
        sources = {a.eli: await find_source(client, a.eli) for a in KEY_ACTS}
        act_keys = {eli: act_key_of(source.act) for eli, source in sources.items()}
        act_refs = [ActRef(act_keys[a.eli], tuple(alias.lower() for alias in a.aliases)) for a in KEY_ACTS]

        for key_act in key_acts:
            source = sources[key_act.eli]
            pdf = await fetch_pdf(client, source)
            parsed = await asyncio.to_thread(parse, key_act, act_keys[key_act.eli], source, pdf, act_refs)
            numbers = [a.number for a in parsed.articles]
            reference_count = sum(len(r) for r in parsed.references.values())
            print(
                f"{key_act.short_title}: {source.text_eli} ({source.text_kind}, stan prawny {source.legal_status_date}), "
                f"{len(numbers)} articles {numbers[0]}..{numbers[-1]}, {reference_count} references, "
                f"{parsed.unresolved} unresolved",
                flush=True,
            )
            if dry_run:
                continue
            async with get_session_factory()() as session:
                if not force and await already_loaded(session, parsed.sha256):
                    print("  unchanged, skipped")
                    continue
                await save(session, parsed)
                print("  saved")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("eli", nargs="*", help="Only these key acts, e.g. DU/1997/553")
    parser.add_argument("--dry-run", action="store_true", help="Download and parse, do not touch the database")
    parser.add_argument("--force", action="store_true", help="Reload acts whose text did not change")
    args = parser.parse_args()
    asyncio.run(main(args.eli, args.dry_run, args.force))
