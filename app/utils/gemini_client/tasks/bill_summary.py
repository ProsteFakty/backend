from pydantic import BaseModel, Field

from app.utils.gemini_client.client import GeminiClient

SYSTEM_INSTRUCTION = """\
Streszczasz projekty procedowane w Sejmie RP dla osób, które nie śledzą polityki ani prawa.

Zasady:
- Pisz po polsku, prostym językiem, bez żargonu.
- Dokładnie dwa zdania: pierwsze - co się zmienia, drugie - kogo to dotyczy.
- Bądź neutralny politycznie: nie oceniaj projektu.
- Opieraj się wyłącznie na podanym tytule i opisie. Nie dopowiadaj skutków, których tam nie ma.
"""


class BillSummary(BaseModel):
    summary: str = Field(description="Dokładnie 2 zdania: co się zmienia i kogo dotyczy")


async def summarize_bill(client: GeminiClient, title: str, description: str | None) -> str:
    prompt = f"Tytuł: {title}\n\nOpis: {description or 'brak'}"
    result = await client.generate_structured(prompt, BillSummary, system_instruction=SYSTEM_INSTRUCTION)
    return result.summary.strip()
