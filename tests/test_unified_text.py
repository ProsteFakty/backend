from app.pipeline.act_parser import current_wording, parse_articles

# A unified text with a pending amendment: [wording in force] <wording that will replace it>
TEXT = "\n".join(
    [
        "Art. 1. Pierwszy.",
        "[Art. 2. Stare brzmienie,",
        "wciąż obowiązujące, art. 22[1a].]",
        "<Art. 2. § 1. Nowe brzmienie,",
        "które dopiero wejdzie w życie.>",
        "<Art. 2a. Całkiem nowy artykuł.>",
        "Art. 3. Trzeci, zob. art. 22[1] § 1.",
    ]
)


def test_pending_amendment_keeps_the_wording_in_force():
    current = current_wording(TEXT)
    assert "<" not in current and "Nowe brzmienie" not in current and "nowy artykuł" not in current

    articles = {a.number: a.text for a in parse_articles(TEXT)}
    assert list(articles) == ["1", "2", "3"]
    assert articles["2"] == "Stare brzmienie, wciąż obowiązujące, art. 22[1a]."
    assert articles["3"] == "Trzeci, zob. art. 22[1] § 1."  # superscripts are left alone
