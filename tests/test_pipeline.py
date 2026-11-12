from app.pipeline.act_keys import normalize_article_number
from app.pipeline.act_parser import parse_articles
from app.pipeline.eli import act_key_of, act_name
from app.pipeline.pdf_text import _page_text
from app.pipeline.references import ActRef, extract_references

KK = "ustawa_kodeks_karny_1997_06_06"
ACTS = [ActRef(KK, ("kodeksu karnego",)), ActRef("ustawa_kodeks_pracy_1974_06_26", ("kodeksu pracy",))]


def refs(text: str, own: str = "1") -> list[str]:
    return [r["slug"].removeprefix(KK + "_art_") for r in extract_references(text, KK, ACTS, own)]


# --- pdf_text ---


def test_page_text_drops_footnotes_and_headers_keeps_superscripts():
    fragments = [
        (10.0, "Dziennik Ustaw – 37 – Poz. 1342"), (10.0, "\n"), (10.0, "2026-10-20"), (10.0, "\n"),
        (10.0, "Kodeks karny"), (6.5, "1"), (6.5, ")"), (10.0, "\n"),
        (10.0, "Art."), (10.0, " "), (10.0, "22"), (6.5, "["), (6.5, "1a"), (6.5, "]"), (10.0, ". Zgoda"), (10.0, "\n"),
        (6.0, "1)"), (9.0, " Niniejsza ustawa wdraża dyrektywę."),
    ]
    assert _page_text(fragments, 9.5) == "Kodeks karny\nArt. 22[1a]. Zgoda"


# --- act_parser ---

ACT_TEXT = """\
Marszałek Sejmu: W. Czarzasty
USTAWA
z dnia 6 czerwca 1997 r.
CZĘŚĆ OGÓLNA
Rozdział I
Zasady odpowiedzialności karnej
Art. 1. § 1. Odpowiedzialności karnej podlega ten tylko, kto popełnia czyn zabroniony pod groźbą kary przez
ustawę obowiązującą w czasie jego popełnienia.
§ 2. Nie stanowi przestępstwa czyn zabroniony, którego społeczna szkodliwość jest znikoma.
Art. 2. (uchylony)
Art. 3–5. (uchylone)
Rozdział II
Formy popełnienia przestępstwa
Art. 6. Kto zabija człowieka:
1) ze szczególnym okrucieństwem,
2) w związku z wzięciem zakład-
nika,
podlega karze. Przepis stosuje się w brzmieniu:
Art. 1. Treść cytowanego przepisu.
Art. 6[1]. Nowy przepis.
Załącznik nr 1 do ustawy z dnia 6 czerwca 1997 r.
Art. 7. Tabela w załączniku.
"""


def test_parse_articles():
    articles = {a.number: a.text for a in parse_articles(ACT_TEXT)}
    assert list(articles) == ["1", "2", "6", "6^1"]
    assert articles["1"] == (
        "§ 1. Odpowiedzialności karnej podlega ten tylko, kto popełnia czyn zabroniony pod groźbą kary przez "
        "ustawę obowiązującą w czasie jego popełnienia.\n"
        "§ 2. Nie stanowi przestępstwa czyn zabroniony, którego społeczna szkodliwość jest znikoma."
    )
    assert articles["2"] == "(uchylony)"  # the repealed range and the chapter heading are not part of it
    assert articles["6"].startswith("Kto zabija człowieka:\n1) ze szczególnym okrucieństwem,\n2) w związku z wzięciem zakładnika,")
    assert "Art. 1. Treść cytowanego przepisu." in articles["6"]  # quoted, numbering went back


def test_normalize_bracket_superscript():
    assert normalize_article_number("22[1a]") == "22^1a"
    assert normalize_article_number("Art. 26[1]") == "26^1"


# --- references ---


def test_references_lists_ranges_and_qualifiers():
    assert refs("o których mowa w art. 135–138, 143 i 144, czas pracy") == ["135", "136", "137", "138", "143", "144"]
    assert refs("określonego w art. 143 § 1 lub w art. 144 lub 145, który") == ["143", "144", "145"]
    assert refs("wymienione w art. 22[1]§ 1 i 3, z wyjątkiem") == ["22_1"]  # "i 3" are paragraphs


def test_references_skip_self_eu_law_and_next_line():
    assert refs("w art. 1 § 2", own="1") == []
    assert refs("o których mowa w art. 10 rozporządzenia Parlamentu Europejskiego i Rady (UE) 2016/679") == []
    assert refs("zgodnie z art. 188[1],\n5b) inne") == ["188_1"]


def test_references_to_other_acts():
    [alias] = extract_references("art. 115 § 11 Kodeksu karnego stosuje się", "ustawa_kodeks_pracy_1974_06_26", ACTS, "1")
    assert alias == {"slug": f"{KK}_art_115", "same_act": False, "text": "art. 115 § 11"}

    text = (
        "w rozumieniu art. 46 ust. 2 albo 3 ustawy z dnia 26 października 1982 r. o wychowaniu w trzeźwości "
        "i przeciwdziałaniu alkoholizmowi (Dz. U. z 2023 r. poz. 2151)"
    )
    [dated] = extract_references(text, KK, ACTS, "1")
    assert dated["slug"] == "ustawa_o_wychowaniu_w_trzezwosci_i_przeciwdzialaniu_alkoholizmowi_1982_10_26_art_46"
    assert refs("na podstawie art. 5 ustawy o czymś innym") == []  # unknown act: skipped, not guessed
    assert refs("ustawy o wymianie informacji, z uwzględnieniem art. 34 ust. 2 tej ustawy.") == []
    assert refs("o których mowa w art. 166 i art. 182 unijnego kodeksu celnego") == []

    text = "z wyłączeniem stosowania art. 165 § 2 i art. 200 ustawy z dnia 29 sierpnia 1997 r. – Ordynacja podatkowa."
    slugs = [r["slug"] for r in extract_references(text, KK, ACTS, "1")]
    assert slugs == ["ustawa_ordynacja_podatkowa_1997_08_29_art_165", "ustawa_ordynacja_podatkowa_1997_08_29_art_200"]


# --- eli ---


def test_act_name_and_key():
    assert act_name("Ustawa z dnia 6 czerwca 1997 r. - Kodeks karny.") == "Kodeks karny"
    assert act_name("Ustawa z dnia 26 czerwca 1974 r. Kodeks pracy.") == "Kodeks pracy"
    assert act_name("Konstytucja Rzeczypospolitej Polskiej z dnia 2 kwietnia 1997 r.") == "Rzeczypospolitej Polskiej"
    act = {"type": "Ustawa", "title": "Ustawa z dnia 6 grudnia 2008 r. o podatku akcyzowym", "announcementDate": "2008-12-06"}
    assert act_key_of(act) == "ustawa_o_podatku_akcyzowym_2008_12_06"
    constitution = {"type": "Ustawa", "title": "Konstytucja Rzeczypospolitej Polskiej z dnia 2 kwietnia 1997 r.", "announcementDate": "1997-04-02"}
    assert act_key_of(constitution) == "konstytucja_rzeczypospolitej_polskiej_1997_04_02"
