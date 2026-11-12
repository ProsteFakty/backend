"""Plain text of a Dziennik Ustaw PDF (Kancelaria Sejmu layout) without footnotes and page headers."""

import io
import re
from collections import Counter

from pypdf import PdfReader

# Lines repeated on every page: "Dziennik Ustaw – 37 – Poz. 1342", "©Kancelaria Sejmu s. 1/78", "2026-10-20".
_PAGE_HEADER = re.compile(r"^\s*(Dziennik Ustaw\s+–\s*\d+\s*–\s*Poz\.\s*\d+|©Kancelaria Sejmu\s+s\.\s*\d+/\d+|\d{4}-\d{2}-\d{2})\s*$")
# Superscripts in article numbers are printed small and in brackets: "Art. 22[1a]." - keep those.
_SUPERSCRIPT = re.compile(r"^\[[0-9a-z]+\]$")


def extract_text(pdf: bytes) -> tuple[str, int]:
    """(text, page count). Text smaller than the body font is dropped (footnotes and their markers), except
    bracketed superscripts. Font sizes are measured per document, so the body size does not need to be known.
    """
    reader = PdfReader(io.BytesIO(pdf))
    pages = []
    for page in reader.pages:
        fragments: list[tuple[float, str]] = []
        page.extract_text(visitor_text=lambda text, cm, tm, font, size: fragments.append((size * tm[0], text)))
        pages.append(fragments)

    sizes = Counter(round(size, 1) for page in pages for size, text in page if text.strip())
    body_size = sizes.most_common(1)[0][0] if sizes else 0
    text = "\n".join(_page_text(page, body_size - 0.5) for page in pages)
    return text, len(reader.pages)


def _page_text(fragments: list[tuple[float, str]], min_size: float) -> str:
    out: list[str] = []
    small: list[str] = []  # consecutive small fragments; kept only if they form one "[...]" superscript

    def flush() -> None:
        if _SUPERSCRIPT.match("".join(small).strip()):
            out.append("".join(small).strip())
        small.clear()

    for size, text in fragments:
        if not text.strip():
            if "\n" in text:
                flush()
                out.append("\n")
            else:
                (small if small else out).append(text)
            continue
        if size < min_size:
            small.append(text)
            continue
        flush()
        out.append(text)
    flush()

    lines = "".join(out).splitlines()
    return "\n".join(line for line in lines if line.strip() and not _PAGE_HEADER.match(line))
