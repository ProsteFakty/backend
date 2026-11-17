"""Checks that LLM text about a legal provision says nothing the provision does not.

Cheap, deterministic and strict on what matters most to a reader: numbers (rates, amounts, deadlines,
penalties) and article numbers must come from the source text, not from the model.
"""

import re

# "5 000", "5000", "0,5", "1.5", "26[1]" - a number as written in Polish legal text.
_NUMBER = re.compile(r"\d+(?:[  ]\d{3})*(?:[,.]\d+)?")
_ARTICLE = re.compile(r"\bart\.\s*(\d+[a-z]*(?:\s?\[[0-9a-z]+\]|\^[0-9a-z]+)?)", re.IGNORECASE)


def _numbers(text: str) -> set[str]:
    text = _ARTICLE.sub(" ", text)  # "art. 149" is checked as an article, not as the number 149
    return {re.sub(r"[  ]", "", n).replace(".", ",") for n in _NUMBER.findall(text)}


def _articles(text: str) -> set[str]:
    return {re.sub(r"[\s\[\]^]", "", a).lower() for a in _ARTICLE.findall(text)}


_QUOTE_MARKS = "„”“\"'’‘«»"
_DASHES = "–—‑-"


def _normalize_quote(text: str) -> tuple[str, list[int]]:
    """Text with case, spacing, quote and dash styles evened out, and for every character of the result its
    index in `text` - so a match in the normalized text can be reported as a position in the original."""
    chars: list[str] = []
    positions: list[int] = []
    for index, char in enumerate(text):
        if char.isspace():
            if chars and chars[-1] != " ":
                chars.append(" ")
                positions.append(index)
            continue
        chars.append('"' if char in _QUOTE_MARKS else "-" if char in _DASHES else char.lower()[:1])
        positions.append(index)
    if chars and chars[-1] == " ":
        chars.pop()
        positions.pop()
    return "".join(chars), positions


def find_quote(quote: str, source: str) -> list[tuple[int, int]] | None:
    """Where `quote` appears word for word in `source` (case, spacing, quote and dash styles aside):
    [(start, end)] with source[start:end] being the quoted words; None when it does not appear.

    An ellipsis ("...", "…") may stand for an omitted part; the pieces must then appear in order and each
    gets its own span.
    """
    pieces = [p.strip(' "') for p in re.split(r"\.\.\.|…", _normalize_quote(quote)[0])]
    pieces = [p for p in pieces if p]
    if not pieces:
        return None
    haystack, positions = _normalize_quote(source)
    spans, cursor = [], 0
    for piece in pieces:
        start = haystack.find(piece, cursor)
        if start < 0:
            return None
        cursor = start + len(piece)
        spans.append((positions[start], positions[cursor - 1] + 1))
    return spans


def quote_found(quote: str, source: str) -> bool:
    return find_quote(quote, source) is not None


def ungrounded(generated: str, source: str) -> list[str]:
    """What `generated` states that `source` does not contain: numbers and article numbers. Empty = grounded."""
    problems = [f"liczba {n}" for n in sorted(_numbers(generated) - _numbers(source))]
    problems += [f"art. {a}" for a in sorted(_articles(generated) - _articles(source))]
    return problems
