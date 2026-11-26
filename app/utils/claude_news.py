"""Press coverage of one MP from the last weeks, via Claude with the web search tool.

Returns what the model wrote plus every search result Claude saw, unvalidated: app/services/mp_news.py decides
which items and sources are kept.
"""

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime

from anthropic import AsyncAnthropic
from anthropic.types import Usage

from app.utils.claude_client import ClaudeError

MAX_CONTINUATIONS = 3  # `pause_turn` resumes of a long server-side search loop

SYSTEM_PROMPT = """\
Streszczasz doniesienia medialne o jednym polskim pośle lub posłance z ostatnich {days} dni \
dla aplikacji, która tłumaczy politykę prostym językiem.

Zasady:
- Użyj wyszukiwarki: wywiady, wypowiedzi, kontrowersje, afery, viralowe momenty, także portale plotkarskie.
- Tylko wydarzenia z ostatnich {days} dni i tylko dotyczące TEJ osoby (uważaj na osoby o tym samym nazwisku).
- Relacjonuj, nie oceniaj: przypisuj twierdzenia źródłom ("według Pudelka…", "jak podał Onet…").
- Plotek nie podawaj jako faktów. Nie spekuluj o zdrowiu, rodzinie ani życiu intymnym, \
chyba że osoba sama to upubliczniła.
- Bez obraźliwych słów, bez wykrzykników, bez clickbaitu.
- Najwyżej {max_items} wydarzeń, najgłośniejsze najpierw. Nic istotnego - pusta lista.
- W "source_urls" podawaj wyłącznie adresy dokładnie tak, jak zwróciła je wyszukiwarka.

Na końcu odpowiedz WYŁĄCZNIE obiektem JSON, bez komentarza:
{{"items": [{{
  "headline": "do 80 znaków",
  "summary": "1-2 zdania, do 300 znaków, zawiera nazwisko osoby",
  "date": "RRRR-MM-DD, data pierwszej publikacji",
  "category": "statement | controversy | legislation | personal | other",
  "source_urls": ["1-3 adresy artykułów"]
}}]}}
"""

UsageHook = Callable[[Usage], Awaitable[None]]


@dataclass
class SearchResult:
    url: str
    title: str | None


@dataclass
class ClaudeNews:
    items: list[dict]
    results: list[SearchResult] = field(default_factory=list)


def parse_items(text: str) -> list[dict]:
    """The `items` list from the model's answer; tolerates ```json fences and text around the object."""
    text = re.sub(r"```(?:json)?", "", text)
    decoder = json.JSONDecoder()
    found = None
    for start in (m.start() for m in re.finditer(r"\{", text)):
        try:
            data, _ = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            found = data  # keep the last one: the final answer comes after any drafts
    if found is None:
        raise ClaudeError("No JSON object with `items` in the answer")
    return [item for item in found["items"] if isinstance(item, dict)]


async def search_mp_news(
    client: AsyncAnthropic,
    *,
    model: str,
    effort: str,
    full_name: str,
    club: str | None,
    district: str | None,
    today: datetime,
    since: datetime,
    max_items: int,
    max_searches: int,
    blocked_domains: list[str],
    on_usage: UsageHook,
) -> ClaudeNews:
    """`on_usage` is awaited after every API response (also when a later step fails), for cost tracking."""
    prompt = (
        f"Osoba: {full_name}, posł(anka) na Sejm RP"
        + (f", klub: {club}" if club else "")
        + (f", okręg: {district}" if district else "")
        + f".\nDzisiaj: {today:%Y-%m-%d}. Okres: od {since:%Y-%m-%d}."
    )
    # Basic web search on purpose: every result Claude sees arrives as a web_search_tool_result block,
    # which is what source URLs are checked against.
    tool: dict = {
        "type": "web_search_20250305",
        "name": "web_search",
        "max_uses": max_searches,
        "user_location": {"type": "approximate", "country": "PL", "timezone": "Europe/Warsaw"},
    }
    if blocked_domains:
        tool["blocked_domains"] = blocked_domains
    system = SYSTEM_PROMPT.format(days=(today - since).days, max_items=max_items)
    messages: list[dict] = [{"role": "user", "content": prompt}]
    results: list[SearchResult] = []

    for _ in range(MAX_CONTINUATIONS + 1):
        response = await client.messages.create(
            model=model,
            max_tokens=8000,
            system=system,
            messages=messages,
            tools=[tool],
            output_config={"effort": effort},
        )
        await on_usage(response.usage)
        for block in response.content:
            if block.type == "web_search_tool_result" and isinstance(block.content, list):  # error = an object
                results += [SearchResult(url=r.url, title=r.title) for r in block.content]
        if response.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": response.content})
            continue
        if response.stop_reason in ("refusal", "max_tokens"):
            raise ClaudeError(f"Answer stopped: {response.stop_reason}")
        text = "".join(block.text for block in response.content if block.type == "text")
        return ClaudeNews(items=parse_items(text), results=results)
    raise ClaudeError(f"Search still paused after {MAX_CONTINUATIONS} continuations")
