from pydantic import BaseModel, Field

from app.pipeline.grounding import ungrounded
from app.utils.gemini_client.client import GeminiClient

SYSTEM_INSTRUCTION = """\
Tłumaczysz przepisy polskiego prawa na prosty język dla osób, które nie znają prawa.
Dostajesz jeden artykuł ustawy.

Zasady:
- Pisz po polsku, prostym językiem, bez żargonu. Zwracaj się do czytelnika bezosobowo.
- Długość: 1-2 zdania dla krótkiego artykułu, najwyżej 5 zdań dla długiego.
- Opieraj się wyłącznie na treści artykułu. Nie dopowiadaj skutków, wyjątków ani kontekstu, których w nim nie ma.
- Każdą liczbę (kwotę, stawkę, termin, wymiar kary) zapisz dokładnie tak, jak w artykule: cyframi, jeśli
  artykuł używa cyfr, słowami, jeśli używa słów. Nie przeliczaj jednostek i nie zaokrąglaj.
- Jeśli artykuł odsyła do innego przepisu (np. "art. 115 § 2"), napisz, że odsyła, ale nie zgaduj, co tam jest.
- Nie podawaj numerów artykułów, których nie ma w treści.
- Bądź neutralny: nie oceniaj przepisu.
"""


class ArticleSummary(BaseModel):
    summary: str = Field(description="Streszczenie artykułu prostym językiem, 1-5 zdań")


class UngroundedSummaryError(ValueError):
    pass


async def summarize_article(client: GeminiClient, act_title: str, number: str, text: str) -> str:
    """Plain-language summary whose numbers and article numbers all appear in the article.

    One retry with the problems pointed out; if it is still ungrounded, UngroundedSummaryError.
    """
    prompt = f"Ustawa: {act_title}\nArt. {number}\n\n{text}"
    result = await client.generate_structured(prompt, ArticleSummary, system_instruction=SYSTEM_INSTRUCTION, temperature=0)
    summary = result.summary.strip()
    if not (problems := ungrounded(summary, text)):
        return summary

    retry = (
        f"{prompt}\n\nPoprzednie streszczenie zawierało elementy, których nie ma w artykule: {', '.join(problems)}. "
        "Napisz je od nowa, używając tylko tego, co jest w treści artykułu."
    )
    result = await client.generate_structured(retry, ArticleSummary, system_instruction=SYSTEM_INSTRUCTION, temperature=0)
    summary = result.summary.strip()
    if problems := ungrounded(summary, text):
        raise UngroundedSummaryError(f"Art. {number}: {', '.join(problems)}")
    return summary
