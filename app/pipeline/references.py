"""Article references found in legal text by rules (no LLM).

"art. 143 § 1 lub w art. 144 lub 145" -> 143, 144, 145 (same act)
"art. 46 ust. 2 albo 3 ustawy z dnia 26 października 1982 r. o wychowaniu w trzeźwości ... (Dz. U. ...)"
    -> 46 of ustawa_o_wychowaniu_w_trzezwosci_..._1982_10_26
"art. 115 § 11 Kodeksu karnego" -> 115 of the penal code (alias of a key act)

References to EU law ("art. 10 rozporządzenia Parlamentu Europejskiego..."), to amending acts and to acts
whose title cannot be read are skipped rather than guessed.
"""

import re
from dataclasses import dataclass
from datetime import date

from app.pipeline.act_keys import make_act_key, make_article_slug, normalize_article_number

_NUM = r"\d+[a-z]*(?:\s?\[[0-9a-z]+\])?"
_QUALIFIER = r"(?:§|ust\.|pkt|lit\.|zd\.|zdanie|tiret)"
_SEPARATOR = r"(?:,|i|lub|oraz|albo|a także|–|-)"
_ART = re.compile(rf"\bart\.\s*(?={_NUM})", re.IGNORECASE)
# Spaces only, never a line break: the next line of an article is a new point ("5b) ..."), not a reference.
_TOKEN = re.compile(
    rf"[ ]*(?:(?P<qual>{_QUALIFIER})[ ]*(?P<qnum>{_NUM}|[a-z])"
    rf"|(?P<sep>{_SEPARATOR})[ ]*(?:w[ ]+)?(?:art\.[ ]*)?(?P<num>{_NUM})|(?P<first>{_NUM}))"
)

_MONTHS = {
    m: i
    for i, m in enumerate(
        ["stycznia", "lutego", "marca", "kwietnia", "maja", "czerwca", "lipca", "sierpnia", "września",
         "października", "listopada", "grudnia"],
        start=1,
    )
}
# "ustawy z dnia 26 października 1982 r. o wychowaniu ... (Dz. U." / "ustawy z dnia 29 sierpnia 1997 r. – Ordynacja
# podatkowa." - the name ends at the journal reference or at the end of the clause.
_DATED_ACT = re.compile(
    r"^\s*(?P<type>ustawy|rozporządzenia|kodeksu)\s+z\s+dnia\s+(?P<day>\d{1,2})\s+(?P<month>[a-ząćęłńóśźż]+)\s+"
    r"(?P<year>\d{4})\s*r\.\s*(?:–\s*)?(?P<name>[^()]{3,250}?)\s*(?:\(Dz\.|[.,;:]|$)",
    re.IGNORECASE,
)
# The act is named right after the reference but is not one we can resolve. "tej ustawy" / "tego rozporządzenia"
# point back to an act named earlier in the sentence, which is not necessarily this one.
_FOREIGN = re.compile(
    r"^\s*(rozporządzenia|dyrektywy|traktatu|umowy|konwencji|decyzji|unijnego|tej\s+ustawy|tego\s+rozporządzenia|"
    r"ustawy\s+zmienianej|ustawy\s+uchylanej|ustawy,\s+o\s+której|ustawy\s+wymienionej|powołanej\s+ustawy)",
    re.IGNORECASE,
)
# Case-sensitive: "kodeksu" is this code, "Kodeksu karnego" is another one (matched by aliases first).
_SELF = re.compile(r"^\s*(niniejszej\s+ustawy|niniejszego\s+kodeksu|tego\s+kodeksu|kodeksu\b)")


@dataclass(frozen=True)
class ActRef:
    act_key: str
    aliases: tuple[str, ...]  # lower-case


def _article_numbers(text: str, start: int) -> tuple[list[str], int]:
    """Article numbers of the reference starting at `start` (right after "art."), and where it ends."""
    numbers: list[str] = []
    pos, in_qualifier = start, False
    while match := _TOKEN.match(text, pos):
        if match["first"] and not numbers:
            numbers.append(match["first"])
        elif match["qual"]:
            in_qualifier = True
        elif match["num"]:
            explicit_art = "art." in match[0]
            if explicit_art or not in_qualifier:
                if match["sep"] in ("–", "-") and numbers and numbers[-1].isdigit() and match["num"].isdigit():
                    numbers += [str(n) for n in range(int(numbers[-1]) + 1, int(match["num"]) + 1)][:50]
                else:
                    numbers.append(match["num"])
                in_qualifier = False
        else:
            break
        pos = match.end()
    return [normalize_article_number(n.replace(" ", "")) for n in numbers], pos


def _target_act(tail: str, own_key: str, acts: list[ActRef]) -> str | None:
    """Act key the reference points to, from the text right after it; None when it cannot be told."""
    lowered = tail.lstrip().lower()
    for act in acts:
        if any(lowered.startswith(alias) for alias in act.aliases):
            return act.act_key
    if _SELF.match(tail):
        return own_key
    if match := _DATED_ACT.match(tail):
        month = _MONTHS.get(match["month"].lower())
        if month is None:
            return None
        act_type = "ustawa" if match["type"].lower() in ("ustawy", "kodeksu") else "rozporządzenie"
        name = match["name"].strip(" ,–")
        return make_act_key(act_type, name, date(int(match["year"]), month, int(match["day"])))
    if _FOREIGN.match(tail) or re.match(r"^\s*(ustawy|ustawą|ustawie|kodeksu|ordynacji|konstytucji)\b", tail, re.I):
        return None  # some other act we cannot identify
    return own_key  # bare "art. 5" means this act


def extract_references(text: str, own_key: str, acts: list[ActRef], own_number: str) -> list[dict]:
    """[{"slug", "same_act", "text"}] in order of appearance, without duplicates or self-references."""
    found: dict[str, dict] = {}
    end = 0
    for art in _ART.finditer(text):
        if art.start() < end:
            continue  # the "art." of "art. 165 § 2 i art. 200" already read with the reference before it
        numbers, end = _article_numbers(text, art.end())
        if not numbers:
            continue
        act_key = _target_act(text[end : end + 300], own_key, acts)
        if act_key is None:
            continue
        mention = " ".join(text[art.start() : end].split())
        for number in numbers:
            if act_key == own_key and number == own_number:
                continue
            slug = make_article_slug(act_key, number)
            found.setdefault(slug, {"slug": slug, "same_act": act_key == own_key, "text": mention})
    return list(found.values())
