from dataclasses import dataclass

from pydantic import BaseModel, Field

from app.pipeline.grounding import quote_found, ungrounded
from app.utils.gemini_client.client import GeminiClient

SYSTEM_INSTRUCTION = """\
Wyjaśniasz przepisy polskiego prawa prostym językiem osobom, które nie znają prawa.
Dostajesz wyjaśniany artykuł oraz przepisy, do których on odsyła (i do których odsyłają one).
Każdy przepis ma identyfikator, np. [Z1].

Zasady:
- Wyjaśnij, co wynika z wyjaśnianego artykułu, uwzględniając treść przepisów, do których odsyła:
  zamiast "zgodnie z art. 115 § 11" napisz, co ten przepis mówi.
- Pisz po polsku, prostym językiem, bez żargonu; od 2 do 8 punktów, każdy to 1-3 zdania.
- Korzystaj wyłącznie z podanych przepisów. Nie dopowiadaj niczego z własnej wiedzy, nawet jeśli wydaje się oczywiste.
- Każdy punkt musi mieć co najmniej jeden cytat: identyfikator przepisu i dosłowny fragment jego treści
  (do 200 znaków), który potwierdza punkt. Cytat przepisz znak w znak.
- Liczby (kwoty, stawki, terminy, kary) zapisuj dokładnie tak, jak w cytowanych przepisach.
- Jeśli wyjaśniany artykuł odsyła do przepisu, którego nie dostałeś, napisz tylko, że odsyła, bez zgadywania treści.
- Bądź neutralny: nie oceniaj przepisów.
"""


class Citation(BaseModel):
    source: str = Field(description="Identyfikator przepisu, np. Z1")
    quote: str = Field(description="Dosłowny fragment treści tego przepisu, do 200 znaków")


class Point(BaseModel):
    text: str = Field(description="1-3 zdania prostym językiem")
    citations: list[Citation] = Field(description="Co najmniej jeden cytat potwierdzający punkt")


class Explanation(BaseModel):
    points: list[Point]


@dataclass(frozen=True)
class Source:
    id: str  # "Z1"
    label: str  # "Kodeks karny, art. 115 § 11"
    role: str  # "wyjaśniany artykuł" / "odesłanie z art. 148 (art. 115 § 11)"
    text: str


class UngroundedExplanationError(ValueError):
    pass


def check_points(points: list[Point], sources: dict[str, Source]) -> tuple[list[Point], list[str]]:
    """(points that are fully backed by their citations, problems found in the others)."""
    valid, problems = [], []
    for number, point in enumerate(points, start=1):
        issues = []
        if not point.citations:
            issues.append("brak cytatu")
        cited = [sources.get(c.source) for c in point.citations]
        for citation, source in zip(point.citations, cited, strict=True):
            if source is None:
                issues.append(f"nieznany identyfikator {citation.source}")
            elif not quote_found(citation.quote, source.text):
                issues.append(f"cytat z {citation.source} nie występuje w jego treści: \"{citation.quote[:80]}\"")
        backing = "\n".join(f"{s.label}\n{s.text}" for s in cited if s is not None)
        issues += [f"{p} nie występuje w cytowanych przepisach" for p in ungrounded(point.text, backing)]
        if issues:
            problems.append(f"Punkt {number}: " + "; ".join(issues))
        else:
            valid.append(point)
    return valid, problems


def _prompt(sources: list[Source], missing: list[str]) -> str:
    parts = [f"[{s.id}] {s.label} ({s.role})\n{s.text}" for s in sources]
    if missing:
        parts.append("Przepisy, do których artykuł odsyła, ale których nie masz: " + ", ".join(missing))
    return "\n\n".join(parts)


async def explain_article(client: GeminiClient, sources: list[Source], missing: list[str]) -> list[Point]:
    """Points about sources[0] (the explained article), each backed by verbatim quotes from `sources`.

    One retry with the problems pointed out; points still not backed are dropped. If none is left,
    UngroundedExplanationError.
    """
    by_id = {s.id: s for s in sources}
    prompt = _prompt(sources, missing)
    result = await client.generate_structured(prompt, Explanation, system_instruction=SYSTEM_INSTRUCTION, temperature=0)
    valid, problems = check_points(result.points, by_id)
    if not problems:
        return valid

    retry = (
        f"{prompt}\n\nTwoje poprzednie wyjaśnienie miało błędy:\n" + "\n".join(problems) + "\n"
        "Napisz całe wyjaśnienie od nowa. Cytaty przepisuj dosłownie z treści przepisów."
    )
    result = await client.generate_structured(retry, Explanation, system_instruction=SYSTEM_INSTRUCTION, temperature=0)
    retried, retry_problems = check_points(result.points, by_id)
    best = retried if len(retried) >= len(valid) else valid
    if not best:
        raise UngroundedExplanationError("; ".join(retry_problems or problems))
    return best
