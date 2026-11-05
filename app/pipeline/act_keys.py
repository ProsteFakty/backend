"""Slugs used to find articles in the DB: '<act type>_<act name>_<YYYY>_<MM>_<DD>_art_<number>'.

Example: ustawa_o_podatku_akcyzowym_2008_12_06_art_99b
Lowercase, Polish letters replaced with ASCII, every other non-alphanumeric character becomes '_'.
"""

import re
from datetime import date

_POLISH_TO_ASCII = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")
_SUPERSCRIPTS = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")
_ARTICLE_PREFIX = re.compile(r"^\s*(art\.?|artykuł|§)\s*", re.IGNORECASE)
_LEADING_ACT_TYPE = re.compile(r"^(ustaw\w*|rozporządzeni\w*|obwieszczeni\w*)\s+", re.IGNORECASE)


def slugify(text: str) -> str:
    """'– Kodeks pracy' -> 'kodeks_pracy', 'ROZPORZĄDZENIE MINISTRA' -> 'rozporzadzenie_ministra'."""
    ascii_text = text.translate(_POLISH_TO_ASCII).lower()
    return "_".join(re.findall(r"[a-z0-9]+", ascii_text))


def normalize_article_number(raw: str) -> str:
    """'Art. 178a' -> '178a', 'art. 26¹' / '26^1' / '26(1)' -> '26^1'."""
    number = _ARTICLE_PREFIX.sub("", raw).strip().lower().replace(" ", "")
    if match := re.fullmatch(r"(\d+[a-z]*)([⁰¹²³⁴⁵⁶⁷⁸⁹]+)", number):
        return f"{match[1]}^{match[2].translate(_SUPERSCRIPTS)}"
    if match := re.fullmatch(r"(\d+[a-z]*)\((\d+)\)", number):
        return f"{match[1]}^{match[2]}"
    return number


def make_act_key(act_type: str, name: str, act_date: date | None) -> str:
    """'ustawa', 'o podatku akcyzowym', 2008-12-06 -> 'ustawa_o_podatku_akcyzowym_2008_12_06'.

    Without a known date the date part is left out; see app.db.articles.find_article for how that is matched.
    """
    name = _LEADING_ACT_TYPE.sub("", name.strip())  # "ustawy o ..." -> "o ..." (type comes from act_type)
    key = f"{slugify(act_type)}_{slugify(name)}"
    return f"{key}_{act_date:%Y_%m_%d}" if act_date else key


def make_article_slug(act_key: str, article_number: str) -> str:
    """'ustawa_o_podatku_akcyzowym_2008_12_06', 'Art. 99b' -> '..._2008_12_06_art_99b' ('26^1' -> 'art_26_1')."""
    return f"{act_key}_art_{slugify(normalize_article_number(article_number))}"


def has_date(act_key: str) -> bool:
    return re.search(r"_\d{4}_\d{2}_\d{2}$", act_key) is not None


def parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None
