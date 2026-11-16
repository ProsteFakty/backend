"""Parts of an article a reference points at: "art. 115 § 11" -> the text of § 11 of art. 115.

Article text comes from act_parser, where every paragraph (§), ustęp ("2.") and point ("3)") starts a line.
"""

import re

from app.pipeline.act_keys import normalize_article_number

# "§ 11", "ust. 2" right after the article number in a mention: "art. 115 § 11", "art. 30 ust. 1 pkt 2"
_UNIT_AFTER = r"\s*(?:(?P<para>§)\s*(?P<pnum>\d+[a-z]*(?:\[[0-9a-z]+\])?)|ust\.\s*(?P<unum>\d+[a-z]*(?:\[[0-9a-z]+\])?))"


def referenced_unit(mention: str, article_number: str) -> str | None:
    """'§ 11' or 'ust. 2' when `mention` points at one unit of the article, else None.

    >>> referenced_unit("art. 143 § 1 lub w art. 144 lub 145", "143")
    '§ 1'
    >>> referenced_unit("art. 143 § 1 lub w art. 144 lub 145", "144")
    """
    for match in re.finditer(r"art\.\s*(\d+[a-z]*(?:\s?\[[0-9a-z]+\])?)", mention, re.IGNORECASE):
        if normalize_article_number(match[1].replace(" ", "")) != article_number:
            continue
        unit = re.match(_UNIT_AFTER, mention[match.end() :])
        if unit is None:
            return None
        return f"§ {unit['pnum']}" if unit["para"] else f"ust. {unit['unum']}"
    return None


def unit_text(article_text: str, unit: str) -> str | None:
    """Text of '§ 11' / 'ust. 2' (with its points), None when the article has no such unit."""
    if unit.startswith("§"):
        number = unit.removeprefix("§").strip()
        start = re.compile(rf"^§\s*{re.escape(number)}\.\s")
        next_unit = re.compile(r"^§\s*\d+[a-z]*(\[[0-9a-z]+\])?\.\s")
    else:
        number = unit.removeprefix("ust.").strip()
        start = re.compile(rf"^{re.escape(number)}\.\s")
        next_unit = re.compile(r"^\d+[a-z]*(\[[0-9a-z]+\])?\.\s")

    lines: list[str] = []
    for line in article_text.splitlines():
        if lines and next_unit.match(line):
            break
        if lines or start.match(line):
            lines.append(line)
    return "\n".join(lines) or None
