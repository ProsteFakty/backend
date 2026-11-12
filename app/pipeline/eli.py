"""Acts and their texts from the Sejm ELI API (https://api.sejm.gov.pl/eli)."""

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx

from app.pipeline.act_keys import make_act_key, parse_iso_date

ELI_API = "https://api.sejm.gov.pl/eli/acts"
CONSOLIDATED_NOTICES = "Inf. o tekście jednolitym"
IN_FORCE = "obowiązujący"

# "Ustawa z dnia 6 czerwca 1997 r. - Kodeks karny." / "Konstytucja Rzeczypospolitej Polskiej z dnia 2 kwietnia 1997 r."
_TITLE = re.compile(r"^(?P<type>\S+)\s*(?P<pre>.*?)\s*z dnia .*?\d{4}\s*r\.\s*[-–]?\s*(?P<post>.*?)\.?\s*$")


@dataclass
class ActSource:
    """Where the current wording of an act comes from."""

    act: dict[str, Any]  # base act metadata
    text_eli: str  # consolidated text notice (or the base act itself)
    text_kind: str  # "T": consolidated text (tekst jednolity), "U": unified text of the base act
    file_name: str
    legal_status_date: date | None  # the text includes changes up to this day

    @property
    def url(self) -> str:
        return f"{ELI_API}/{self.text_eli}/text/{self.text_kind}/{self.file_name}"


def act_name(title: str) -> str:
    """'Ustawa z dnia 6 czerwca 1997 r. - Kodeks karny.' -> 'Kodeks karny'."""
    match = _TITLE.match(title)
    if not match:
        return title
    return (match["post"] or match["pre"]).strip(" -–.")


def act_key_of(act: dict[str, Any]) -> str:
    # The type is read from the title: the API calls the Constitution "Ustawa".
    match = _TITLE.match(act["title"])
    act_type = match["type"] if match else act["type"]
    return make_act_key(act_type, act_name(act["title"]), parse_iso_date(act.get("announcementDate")))


async def get_act(client: httpx.AsyncClient, eli: str) -> dict[str, Any]:
    return (await client.get(f"{ELI_API}/{eli}")).raise_for_status().json()


async def find_source(client: httpx.AsyncClient, eli: str) -> ActSource:
    """The newest consolidated text in force (official, PDF only); without one (e.g. the Constitution),
    the unified text of the base act kept up to date by the Sejm."""
    act = await get_act(client, eli)
    references = (await client.get(f"{ELI_API}/{eli}/references")).raise_for_status().json()
    notices = [ref["act"] for ref in references.get(CONSOLIDATED_NOTICES, []) if ref["act"].get("status") == IN_FORCE]
    for notice in sorted(notices, key=lambda n: (n["year"], n["pos"]), reverse=True):
        meta = await get_act(client, notice["ELI"])
        if text := next((t for t in meta.get("texts", []) if t["type"] == "T"), None):
            return ActSource(act, notice["ELI"], "T", text["fileName"], parse_iso_date(meta.get("legalStatusDate")))

    if text := next((t for t in act.get("texts", []) if t["type"] == "U"), None):
        return ActSource(act, eli, "U", text["fileName"], parse_iso_date(act.get("changeDate", "")[:10]))
    raise LookupError(f"{eli}: no consolidated or unified text")


async def download(client: httpx.AsyncClient, source: ActSource) -> bytes:
    response = await client.get(source.url)
    response.raise_for_status()
    return response.content
