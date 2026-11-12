"""Acts loaded into the law search. ELI ids are those of the base acts, not of their consolidated texts."""

from dataclasses import dataclass


@dataclass(frozen=True)
class KeyAct:
    eli: str  # base act, e.g. "DU/1997/553"; its newest consolidated text is found through the ELI references
    short_title: str
    # How other acts cite this one (genitive, as it follows "art. N"); matched case-insensitively.
    aliases: tuple[str, ...] = ()
    # What users type in a search after "art. N": "art. 148 kk", "art. 30 ustawy o pit".
    abbreviations: tuple[str, ...] = ()


KEY_ACTS: tuple[KeyAct, ...] = (
    KeyAct(
        "DU/1997/483", "Konstytucja RP", ("Konstytucji Rzeczypospolitej Polskiej", "Konstytucji"),
        ("konstytucja", "konstytucji", "krp"),
    ),
    KeyAct("DU/1997/553", "Kodeks karny", ("Kodeksu karnego", "ustawy – Kodeks karny"), ("kk",)),
    KeyAct("DU/1964/93", "Kodeks cywilny", ("Kodeksu cywilnego", "ustawy – Kodeks cywilny"), ("kc",)),
    KeyAct("DU/1974/141", "Kodeks pracy", ("Kodeksu pracy", "ustawy – Kodeks pracy"), ("kp",)),
    KeyAct("DU/1971/114", "Kodeks wykroczeń", ("Kodeksu wykroczeń", "ustawy – Kodeks wykroczeń"), ("kw",)),
    KeyAct(
        "DU/2009/11", "Ustawa o podatku akcyzowym", ("ustawy o podatku akcyzowym",),
        ("akcyza", "ustawa akcyzowa", "ustawy akcyzowej", "uopa"),
    ),
    KeyAct(
        "DU/2004/535", "Ustawa o VAT", ("ustawy o podatku od towarów i usług",),
        ("vat", "ustawa o vat", "ustawy o vat", "uptu"),
    ),
    KeyAct(
        "DU/1991/350", "Ustawa o PIT", ("ustawy o podatku dochodowym od osób fizycznych",),
        ("pit", "ustawa o pit", "ustawy o pit", "updof"),
    ),
    KeyAct(
        "DU/1992/86", "Ustawa o CIT", ("ustawy o podatku dochodowym od osób prawnych",),
        ("cit", "ustawa o cit", "ustawy o cit", "updop"),
    ),
    KeyAct(
        "DU/1997/926", "Ordynacja podatkowa", ("Ordynacji podatkowej", "ustawy – Ordynacja podatkowa"),
        ("op", "ordynacji podatkowej"),
    ),
    KeyAct(
        "DU/1997/602", "Prawo o ruchu drogowym", ("ustawy – Prawo o ruchu drogowym", "Prawa o ruchu drogowym"),
        ("prd", "pord", "kodeks drogowy", "kodeksu drogowego"),
    ),
)
