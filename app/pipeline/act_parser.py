"""Split the plain text of an act (see pdf_text) into articles."""

import re
from dataclasses import dataclass

from app.pipeline.act_keys import normalize_article_number

# "Art. 148. § 1. Kto zabija...", "Art. 22[1a]. Zgoda...", "Art. 9. (uchylony)"
_ARTICLE_START = re.compile(r"^Art\.\s*(\d+[a-z]*(?:\[[0-9a-z]+\])?)\.\s*(.*)$")
# Repealed ranges have no text of their own: "Art. 10–12. (uchylone)".
_ARTICLE_RANGE = re.compile(r"^Art\.\s*\d+[a-z]*(?:\[[0-9a-z]+\])?\s*[–-]\s*\d+[a-z]*(?:\[[0-9a-z]+\])?\.")
# A whole line that is a structural heading: "Rozdział XIX", "DZIAŁ IIa", "CZĘŚĆ OGÓLNA", "Oddział 2".
_HEADING = re.compile(
    r"^(CZĘŚĆ|Część|KSIĘGA|Księga|TYTUŁ|Tytuł|DZIAŁ|Dział|Rozdział|ROZDZIAŁ|Oddział)\s+"
    r"([IVXLC]+|\d+|[A-ZĄĆĘŁŃÓŚŹŻ]+)[a-z0-9\[\]]*\s*$"
)
# Annexes (tables of rates, forms) follow the last article; they are not articles.
_ANNEX = re.compile(r"^(ZAŁĄCZNIK|Załącznik|Załączniki)(\s+nr\s+\d+[a-z]*)?(\s+do\s+ustawy.*)?\s*$")
# A new line inside an article starts a paragraph, point, letter or indent; anything else is a wrapped line.
_BLOCK_START = re.compile(r"^(§\s*\d+[a-z]*(\[[0-9a-z]+\])?\.|\d+[a-z]*(\[[0-9a-z]+\])?[.)]\s|[a-z]{1,2}\)\s|–\s)")


_REPEALED = re.compile(r"^\(\s*(uchylony|uchylona|uchylone|pominięty|pominięta|utracił moc|utraciła moc)\s*\)\.?$")


def is_repealed(text: str) -> bool:
    """'(uchylony)' and the like: the article number stays in the act, its content is gone."""
    return _REPEALED.match(text.strip()) is not None


@dataclass
class ParsedArticle:
    number: str  # normalized: "148", "22^1a"
    text: str


def _base_number(number: str) -> int:
    return int(re.match(r"\d+", number)[0])


def _join_lines(lines: list[str]) -> str:
    """Unwrap PDF lines: keep line breaks only before §, ust., pkt, lit. and indents; undo hyphenation."""
    out = ""
    for line in (line.strip() for line in lines):
        if not line:
            continue
        if not out:
            out = line
        elif _BLOCK_START.match(line):
            out += "\n" + line
        elif out.endswith("-") and line[0].islower():
            out = out[:-1] + line  # "szcze-" + "gólności"
        else:
            out += " " + line
    out = re.sub(r"[ \t]{2,}", " ", out)
    return re.sub(r" ([,;.])(?= |\n|$)", r"\1", out)  # "w ustawie , nie": a removed footnote mark leaves a gap


# Unified texts of the Sejm Chancellery show a pending amendment as both wordings side by side:
#   [Art. 5. the wording in force, about to be replaced]  <Art. 5. the wording that will replace it>
# "[1a]" is not such a bracket but a superscript, as in "Art. 22[1a]".
_SUPERSCRIPT = r"\[[0-9a-z]{1,4}\]"
_FUTURE_WORDING = re.compile(r"<[^<>]*>")
_OUTGOING_WORDING = re.compile(rf"\[(?!{_SUPERSCRIPT[2:]})((?:[^\[\]]|{_SUPERSCRIPT})*)\]")


def current_wording(text: str) -> str:
    """The text as in force on its legal status date: wording that is yet to enter into force is dropped,
    wording that is about to be replaced stays (without the brackets)."""
    return _OUTGOING_WORDING.sub(lambda match: match[1], _FUTURE_WORDING.sub("", text))


def parse_articles(text: str) -> list[ParsedArticle]:
    """Articles in order. Text before "Art. 1." (announcement, preamble) and annexes after the act are skipped.

    An "Art. N." line counts as a new article only if N was not seen yet and does not go back in numbering,
    so a quoted provision inside an article stays part of that article.
    """
    articles: list[tuple[str, list[str]]] = []
    in_heading = False
    for line in current_wording(text).splitlines():
        line = line.strip()
        if match := _ARTICLE_START.match(line):
            number = normalize_article_number(match[1])
            seen = {n for n, _ in articles}
            if (not articles and number == "1") or (
                articles and number not in seen and _base_number(number) >= _base_number(articles[-1][0])
            ):
                articles.append((number, [match[2]]))
                in_heading = False
                continue
        if not articles:
            continue
        if _ANNEX.match(line):
            break
        if _ARTICLE_RANGE.match(line):
            in_heading = True  # skip like a heading until the next article
            continue
        if _HEADING.match(line):
            in_heading = True
            continue
        if not in_heading:
            articles[-1][1].append(line)
    return [ParsedArticle(number, _join_lines(lines)) for number, lines in articles]
