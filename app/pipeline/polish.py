"""Just enough Polish morphology for full-text search: "papierosy" must find "papierosów".

Postgres ships no Polish dictionary, so query words are cut to a rough stem and matched as prefixes
("papieros:*") against articles.search_vector. Crude on purpose: it only has to produce candidates, the ranking
and the semantic search do the rest. Stem alternations ("pies"/"psa", "podatek"/"podatku") are not handled.
"""

import re

_POLISH_TO_ASCII = str.maketrans("ąćęłńóśźż", "acelnoszz")

# After folding ("ą" -> "a", "ów" -> "ow"). The longest ending that leaves at least MIN_STEM letters is cut.
_ENDINGS = sorted(
    (
        "owanie owania owaniu owaniem aniach eniach osciach osciami osci oscia anie ania aniu aniem enie enia eniu "
        "eniem owego owemu owych owymi owym owej owa owe owy owi ami ach ego emu ymi imi ych ich iem ow om em ym im "
        "ej ie ia iu a e i o u y"
    ).split(),
    key=len,
    reverse=True,
)
MIN_STEM = 4

STOPWORDS = frozenset(
    (
        "a aby albo ale art bez by byc co czy dla do gdy gdzie i ich ile jak jaka jaki jakie jest jego jej kiedy kto "
        "ktora ktore ktory lub ma mam mi moge mozna na nad nie niz o od oraz po pod przed przez przy sa sie ta tak "
        "te tego ten to tym u w we wobec z za ze "
        # in nearly every article, so they only add noise
        "przepis przepisy przepisow ustawa ustawy artykul"
    ).split()
)


def fold(text: str) -> str:
    """'Łańcuch' -> 'lancuch'."""
    return text.lower().translate(_POLISH_TO_ASCII)


def stem(word: str) -> str:
    """Folded word without its ending: 'papierosow' -> 'papieros', 'umowie' -> 'umow'; short words stay whole."""
    for ending in _ENDINGS:
        if word.endswith(ending) and len(word) - len(ending) >= MIN_STEM:
            return word[: -len(ending)]
    return word


def search_terms(query: str) -> list[str]:
    """tsquery terms for the words of `query` that carry meaning: 'Trzymanie psa na łańcuchu' ->
    ['trzym:*', 'psa:*', 'lancuch:*']. Numbers must match exactly ('500', not '5000')."""
    terms: list[str] = []
    for word in re.findall(r"[a-z0-9]+", fold(query)):
        if word in STOPWORDS or (len(word) < 3 and not word.isdigit()):
            continue
        term = word if word.isdigit() else f"{stem(word)}:*"
        if term not in terms:
            terms.append(term)
    return terms
