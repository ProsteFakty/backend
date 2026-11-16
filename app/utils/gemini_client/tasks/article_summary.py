from dataclasses import dataclass

from pydantic import BaseModel, Field

from app.pipeline.grounding import ungrounded
from app.utils.gemini_client.client import GeminiClient

SYSTEM_INSTRUCTION = """\
Tłumaczysz przepisy polskiego prawa na prosty język dla osób, które nie znają prawa.
Dostajesz artykuły ustaw, każdy z identyfikatorem, np. [A1]. Do każdego piszesz tytuł i krótkie streszczenie,
które pokażą się na liście wyników wyszukiwarki. Każdy artykuł opisujesz osobno, tylko na podstawie jego treści.

Tytuł:
- Mówi, czego dotyczy artykuł, słowami, jakich użyłby zwykły człowiek szukający tego przepisu,
  np. "Stawka akcyzy na płyn do e-papierosów", "Zakaz konkurencji po ustaniu zatrudnienia", "Kara za zabójstwo".
- Od 3 do 9 słów, bez numeru artykułu, bez nazwy ustawy, bez kropki na końcu.

Streszczenie:
- 1-2 zdania, najwyżej 3 dla długiego artykułu. Najpierw to, co najważniejsze dla zwykłego człowieka.
- Pisz po polsku, prostym językiem, bez żargonu. Zwracaj się do czytelnika bezosobowo.

Zasady dla obu:
- Opieraj się wyłącznie na treści danego artykułu. Nie dopowiadaj skutków, wyjątków ani kontekstu, których
  w nim nie ma, i nie przenoś niczego z innych artykułów.
- Każdą liczbę (kwotę, stawkę, termin, wymiar kary) zapisz dokładnie tak, jak w artykule: cyframi, jeśli
  artykuł używa cyfr, słowami, jeśli używa słów. Nie przeliczaj jednostek i nie zaokrąglaj.
- Jeśli artykuł odsyła do innego przepisu (np. "art. 115 § 2"), napisz, że odsyła, ale nie zgaduj, co tam jest.
- Nie podawaj numerów artykułów, których nie ma w treści.
- Bądź neutralny: nie oceniaj przepisu.
- Zwróć wynik dla każdego identyfikatora, który dostałeś.
"""


@dataclass(frozen=True)
class ArticleInput:
    act_title: str
    number: str
    text: str


class ArticleSummary(BaseModel):
    title: str = Field(description="Czego dotyczy artykuł, 3-9 słów, bez numeru artykułu i bez kropki")
    summary: str = Field(description="Streszczenie artykułu prostym językiem, 1-3 zdania")


class _Item(ArticleSummary):
    id: str = Field(description="Identyfikator artykułu, np. A1")


class _Batch(BaseModel):
    items: list[_Item]


class UngroundedSummaryError(ValueError):
    pass


def _problems(result: ArticleSummary | None, text: str) -> list[str]:
    if result is None:
        return ["brak wyniku"]
    problems = ungrounded(f"{result.title}\n{result.summary}", text)
    if not result.title:
        problems.append("brak tytułu")
    return problems


async def _ask(
    client: GeminiClient, articles: dict[str, ArticleInput], problems: dict[str, list[str]], model: str | None
) -> dict[str, ArticleSummary]:
    parts = [f"[{id_}] Ustawa: {a.act_title}, art. {a.number}\n{a.text}" for id_, a in articles.items()]
    prompt = "\n\n---\n\n".join(parts)
    if problems:
        notes = "\n".join(f"[{id_}]: {', '.join(found)}" for id_, found in problems.items())
        prompt += (
            "\n\n---\n\nPoprzednie tytuły i streszczenia zawierały elementy, których nie ma w treści artykułu:\n"
            f"{notes}\nNapisz je od nowa, używając tylko tego, co jest w treści danego artykułu."
        )
    result = await client.generate_structured(
        prompt, _Batch, system_instruction=SYSTEM_INSTRUCTION, temperature=0, model=model
    )
    return {
        item.id.strip("[] "): ArticleSummary(title=" ".join(item.title.split()).rstrip("."), summary=item.summary.strip())
        for item in result.items
    }


async def summarize_articles(
    client: GeminiClient, articles: list[ArticleInput], model: str | None = None
) -> list[ArticleSummary | UngroundedSummaryError]:
    """Title and plain-language summary of each article, in one request (one more for those that failed).

    A result is accepted only if all its numbers and article numbers appear in that article. Articles whose
    result is still not grounded after the retry get an UngroundedSummaryError in their place.
    """
    by_id = {f"A{index}": article for index, article in enumerate(articles, start=1)}
    results = await _ask(client, by_id, {}, model)
    problems = {id_: found for id_, a in by_id.items() if (found := _problems(results.get(id_), a.text))}
    if problems:
        retried = await _ask(client, {id_: by_id[id_] for id_ in problems}, problems, model)
        results.update(retried)

    out: list[ArticleSummary | UngroundedSummaryError] = []
    for id_, article in by_id.items():
        found = _problems(results.get(id_), article.text)
        out.append(UngroundedSummaryError(f"Art. {article.number}: {', '.join(found)}") if found else results[id_])
    return out


async def summarize_article(client: GeminiClient, act_title: str, number: str, text: str) -> ArticleSummary:
    """One article; UngroundedSummaryError if the result is not grounded even after the retry."""
    [result] = await summarize_articles(client, [ArticleInput(act_title, number, text)])
    if isinstance(result, UngroundedSummaryError):
        raise result
    return result
