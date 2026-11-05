from pydantic import BaseModel, Field

from app.utils.gemini_client.client import GeminiClient

SYSTEM_INSTRUCTION = """\
Opisujesz posiedzenia Sejmu RP dla osób, które nie śledzą polityki ani prawa.
Dostajesz tytuł posiedzenia i jego porządek obrad.

Zasady:
- Pisz po polsku, prostym językiem, bez żargonu.
- Opis to dokładnie dwa zdania o tym, czym zajmował się Sejm na tym posiedzeniu.
- Bądź neutralny politycznie: nie oceniaj.
- Opieraj się wyłącznie na porządku obrad. Nie dopowiadaj wyników ani przebiegu dyskusji.
- Skup się na punktach ważnych dla zwykłych ludzi (np. podatki, zdrowie, praca, mieszkania,
  prawo karne, budżet). Pomijaj sprawy organizacyjne i okolicznościowe
  (składy komisji, wybory sekretarzy, uchwały rocznicowe).
"""


class SittingSummary(BaseModel):
    description: str = Field(description="Dokładnie 2 zdania: czym zajmował się Sejm na tym posiedzeniu")


async def summarize_sitting(client: GeminiClient, title: str, agenda: str) -> SittingSummary:
    prompt = f"Posiedzenie: {title}\n\nPorządek obrad:\n{agenda}"
    return await client.generate_structured(prompt, SittingSummary, system_instruction=SYSTEM_INSTRUCTION)
