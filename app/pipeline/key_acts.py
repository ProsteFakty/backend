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


def _code(eli: str, name: str, genitive: str, *abbreviations: str) -> KeyAct:
    """'Kodeks karny' / 'Prawo bankowe': cited as 'Kodeksu karnego' or 'ustawy – Kodeks karny'."""
    return KeyAct(eli, name, (genitive, f"ustawy – {name}", f"ustawy - {name}"), abbreviations)


def _act(eli: str, short_title: str, about: str, *abbreviations: str) -> KeyAct:
    """'o prawach konsumenta': cited as 'ustawy o prawach konsumenta'."""
    return KeyAct(eli, short_title, (f"ustawy {about}",), (f"ustawa {about}", f"ustawy {about}", *abbreviations))


KEY_ACTS: tuple[KeyAct, ...] = (
    # --- constitution and codes ---
    KeyAct(
        "DU/1997/483", "Konstytucja RP", ("Konstytucji Rzeczypospolitej Polskiej", "Konstytucji"),
        ("konstytucja", "konstytucji", "krp"),
    ),
    _code("DU/1997/553", "Kodeks karny", "Kodeksu karnego", "kk"),
    _code("DU/1964/93", "Kodeks cywilny", "Kodeksu cywilnego", "kc"),
    _code("DU/1974/141", "Kodeks pracy", "Kodeksu pracy", "kp"),
    _code("DU/1971/114", "Kodeks wykroczeń", "Kodeksu wykroczeń", "kw"),
    _code("DU/1964/59", "Kodeks rodzinny i opiekuńczy", "Kodeksu rodzinnego i opiekuńczego", "kro", "krio"),
    _code("DU/2000/1037", "Kodeks spółek handlowych", "Kodeksu spółek handlowych", "ksh"),
    _code("DU/1999/930", "Kodeks karny skarbowy", "Kodeksu karnego skarbowego", "kks"),
    _code("DU/1964/296", "Kodeks postępowania cywilnego", "Kodeksu postępowania cywilnego", "kpc"),
    _code("DU/1997/555", "Kodeks postępowania karnego", "Kodeksu postępowania karnego", "kpk"),
    _code("DU/1960/168", "Kodeks postępowania administracyjnego", "Kodeksu postępowania administracyjnego", "kpa"),
    _code(
        "DU/2001/1148", "Kodeks postępowania w sprawach o wykroczenia",
        "Kodeksu postępowania w sprawach o wykroczenia", "kpw", "kpsw",
    ),
    # --- taxes ---
    _act("DU/2009/11", "Ustawa o podatku akcyzowym", "o podatku akcyzowym", "akcyza", "ustawa akcyzowa", "ustawy akcyzowej", "uopa"),
    _act("DU/2004/535", "Ustawa o VAT", "o podatku od towarów i usług", "vat", "ustawa o vat", "ustawy o vat", "uptu"),
    _act("DU/1991/350", "Ustawa o PIT", "o podatku dochodowym od osób fizycznych", "pit", "ustawa o pit", "ustawy o pit", "updof"),
    _act("DU/1992/86", "Ustawa o CIT", "o podatku dochodowym od osób prawnych", "cit", "ustawa o cit", "ustawy o cit", "updop"),
    _code("DU/1997/926", "Ordynacja podatkowa", "Ordynacji podatkowej", "op", "ordynacji podatkowej"),
    _act(
        "DU/1998/930", "Ustawa o ryczałcie",
        "o zryczałtowanym podatku dochodowym od niektórych przychodów osiąganych przez osoby fizyczne",
        "ryczałt", "ustawa o ryczałcie", "ustawy o ryczałcie",
    ),
    _act("DU/2000/959", "Ustawa o podatku od czynności cywilnoprawnych", "o podatku od czynności cywilnoprawnych", "pcc"),
    _act("DU/1983/207", "Ustawa o podatku od spadków i darowizn", "o podatku od spadków i darowizn"),
    _act("DU/1991/31", "Ustawa o podatkach i opłatach lokalnych", "o podatkach i opłatach lokalnych"),
    # --- companies, startups, running a business ---
    _code("DU/2018/646", "Prawo przedsiębiorców", "Prawa przedsiębiorców"),
    _act(
        "DU/2018/647", "Ustawa o CEIDG",
        "o Centralnej Ewidencji i Informacji o Działalności Gospodarczej i Punkcie Informacji dla Przedsiębiorcy",
        "ceidg", "ustawa o ceidg", "ustawy o ceidg",
    ),
    _act("DU/1997/769", "Ustawa o Krajowym Rejestrze Sądowym", "o Krajowym Rejestrze Sądowym", "krs", "ustawa o krs", "ustawy o krs"),
    _act("DU/1994/591", "Ustawa o rachunkowości", "o rachunkowości", "uor"),
    _code("DU/2003/535", "Prawo upadłościowe", "Prawa upadłościowego"),
    _code("DU/2015/978", "Prawo restrukturyzacyjne", "Prawa restrukturyzacyjnego"),
    _act(
        "DU/2013/403", "Ustawa o opóźnieniach w transakcjach handlowych",
        "o przeciwdziałaniu nadmiernym opóźnieniom w transakcjach handlowych",
    ),
    _act(
        "DU/2008/730", "Ustawa o wspieraniu działalności innowacyjnej",
        "o niektórych formach wspierania działalności innowacyjnej",
    ),
    _act(
        "DU/2018/649", "Ustawa o przedsiębiorcach zagranicznych",
        "o zasadach uczestnictwa przedsiębiorców zagranicznych i innych osób zagranicznych w obrocie gospodarczym "
        "na terytorium Rzeczypospolitej Polskiej",
    ),
    _code("DU/2019/2019", "Prawo zamówień publicznych", "Prawa zamówień publicznych", "pzp"),
    _act("DU/1984/97", "Ustawa o fundacjach", "o fundacjach"),
    _code("DU/1989/104", "Prawo o stowarzyszeniach", "Prawa o stowarzyszeniach"),
    _act("DU/2003/873", "Ustawa o działalności pożytku publicznego", "o działalności pożytku publicznego i o wolontariacie"),
    # --- intellectual property, competition, consumers, data ---
    _act("DU/1994/83", "Prawo autorskie", "o prawie autorskim i prawach pokrewnych", "prawo autorskie", "prawa autorskiego", "pr. aut."),
    _code("DU/2001/508", "Prawo własności przemysłowej", "Prawa własności przemysłowej", "pwp"),
    _act("DU/1993/211", "Ustawa o zwalczaniu nieuczciwej konkurencji", "o zwalczaniu nieuczciwej konkurencji", "uznk"),
    _act("DU/2007/331", "Ustawa o ochronie konkurencji i konsumentów", "o ochronie konkurencji i konsumentów", "uokik"),
    _act("DU/2014/827", "Ustawa o prawach konsumenta", "o prawach konsumenta"),
    _act("DU/2018/1000", "Ustawa o ochronie danych osobowych", "o ochronie danych osobowych", "uodo"),
    _act("DU/2002/1204", "Ustawa o świadczeniu usług drogą elektroniczną", "o świadczeniu usług drogą elektroniczną", "uśude"),
    _act("DU/2016/1579", "Ustawa o usługach zaufania i identyfikacji elektronicznej", "o usługach zaufania oraz identyfikacji elektronicznej"),
    _code("DU/1984/24", "Prawo prasowe", "Prawa prasowego"),
    # --- finance ---
    _code("DU/1997/939", "Prawo bankowe", "Prawa bankowego"),
    _act("DU/2011/1175", "Ustawa o usługach płatniczych", "o usługach płatniczych"),
    _act("DU/2011/715", "Ustawa o kredycie konsumenckim", "o kredycie konsumenckim"),
    _act("DU/2005/1538", "Ustawa o obrocie instrumentami finansowymi", "o obrocie instrumentami finansowymi"),
    _act(
        "DU/2005/1539", "Ustawa o ofercie publicznej",
        "o ofercie publicznej i warunkach wprowadzania instrumentów finansowych do zorganizowanego systemu obrotu "
        "oraz o spółkach publicznych",
        "ustawa o ofercie publicznej", "ustawy o ofercie publicznej",
    ),
    _act(
        "DU/2022/1488", "Ustawa o finansowaniu społecznościowym",
        "o finansowaniu społecznościowym dla przedsięwzięć gospodarczych i pomocy kredytobiorcom",
        "crowdfunding", "ustawa o crowdfundingu",
    ),
    _act(
        "DU/2018/723", "Ustawa o przeciwdziałaniu praniu pieniędzy",
        "o przeciwdziałaniu praniu pieniędzy oraz finansowaniu terroryzmu", "aml",
    ),
    _act(
        "DU/2003/1152", "Ustawa o ubezpieczeniach obowiązkowych",
        "o ubezpieczeniach obowiązkowych, Ubezpieczeniowym Funduszu Gwarancyjnym i Polskim Biurze Ubezpieczycieli "
        "Komunikacyjnych",
    ),
    _act("DU/2009/1540", "Ustawa o grach hazardowych", "o grach hazardowych"),
    # --- work and social insurance ---
    _act("DU/1998/887", "Ustawa o systemie ubezpieczeń społecznych", "o systemie ubezpieczeń społecznych", "zus", "ustawa o zus", "ustawy o zus"),
    _act("DU/2002/1679", "Ustawa o minimalnym wynagrodzeniu", "o minimalnym wynagrodzeniu za pracę"),
    _act("DU/1991/234", "Ustawa o związkach zawodowych", "o związkach zawodowych"),
    _act("DU/2003/1608", "Ustawa o pracownikach tymczasowych", "o zatrudnianiu pracowników tymczasowych"),
    _act("DU/2018/2215", "Ustawa o PPK", "o pracowniczych planach kapitałowych", "ppk", "ustawa o ppk", "ustawy o ppk"),
    _act("DU/1998/1118", "Ustawa o emeryturach i rentach z FUS", "o emeryturach i rentach z Funduszu Ubezpieczeń Społecznych"),
    _act(
        "DU/1999/636", "Ustawa zasiłkowa",
        "o świadczeniach pieniężnych z ubezpieczenia społecznego w razie choroby i macierzyństwa",
        "ustawa zasiłkowa", "ustawy zasiłkowej",
    ),
    _act("DU/2024/928", "Ustawa o ochronie sygnalistów", "o ochronie sygnalistów"),
    # --- housing and property ---
    _act(
        "DU/2001/733", "Ustawa o ochronie praw lokatorów",
        "o ochronie praw lokatorów, mieszkaniowym zasobie gminy i o zmianie Kodeksu cywilnego",
        "ustawa o ochronie praw lokatorów", "ustawy o ochronie praw lokatorów",
    ),
    _act("DU/1994/388", "Ustawa o własności lokali", "o własności lokali"),
    _act("DU/1982/147", "Ustawa o księgach wieczystych i hipotece", "o księgach wieczystych i hipotece", "ukwh"),
    _code("DU/1994/414", "Prawo budowlane", "Prawa budowlanego"),
    _act("DU/1997/741", "Ustawa o gospodarce nieruchomościami", "o gospodarce nieruchomościami"),
    _act(
        "DU/2021/1177", "Ustawa deweloperska",
        "o ochronie praw nabywcy lokalu mieszkalnego lub domu jednorodzinnego oraz Deweloperskim Funduszu Gwarancyjnym",
        "ustawa deweloperska", "ustawy deweloperskiej",
    ),
    # --- everyday life ---
    _code("DU/1997/602", "Prawo o ruchu drogowym", "Prawa o ruchu drogowym", "prd", "pord", "kodeks drogowy", "kodeksu drogowego"),
    _act("DU/2011/151", "Ustawa o kierujących pojazdami", "o kierujących pojazdami"),
    _act("DU/1997/724", "Ustawa o ochronie zwierząt", "o ochronie zwierząt"),
    _act("DU/2004/880", "Ustawa o ochronie przyrody", "o ochronie przyrody"),
    _act(
        "DU/1996/55", "Ustawa antytytoniowa",
        "o ochronie zdrowia przed następstwami używania tytoniu i wyrobów tytoniowych",
        "ustawa antytytoniowa", "ustawy antytytoniowej", "ustawa tytoniowa",
    ),
    _act(
        "DU/1982/230", "Ustawa o wychowaniu w trzeźwości",
        "o wychowaniu w trzeźwości i przeciwdziałaniu alkoholizmowi",
        "ustawa o wychowaniu w trzeźwości", "ustawy o wychowaniu w trzeźwości",
    ),
    _act("DU/2005/1485", "Ustawa o przeciwdziałaniu narkomanii", "o przeciwdziałaniu narkomanii"),
    _act("DU/2009/417", "Ustawa o prawach pacjenta", "o prawach pacjenta i Rzeczniku Praw Pacjenta", "ustawa o prawach pacjenta", "ustawy o prawach pacjenta"),
    _act("DU/1999/549", "Ustawa o broni i amunicji", "o broni i amunicji"),
    _act("DU/2004/593", "Ustawa o pomocy społecznej", "o pomocy społecznej"),
    _act("DU/2016/195", "Ustawa o 800+", "o pomocy państwa w wychowywaniu dzieci", "800+", "800 plus", "500+"),
    _act("DU/2005/1398", "Ustawa o kosztach sądowych w sprawach cywilnych", "o kosztach sądowych w sprawach cywilnych", "uksc"),
    _code("DU/2018/1668", "Prawo o szkolnictwie wyższym i nauce", "Prawa o szkolnictwie wyższym i nauce"),
)
