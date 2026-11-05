import asyncio
from datetime import timedelta

from google.genai import errors, types

from app.services.sittings import agenda_text, print_number
from app.utils.gemini_client import GeminiClient, retry_after
from app.utils.gemini_client.tasks import summarize_sitting

AGENDA_HTML = """
<div class="PorzadekObrad"><table><tbody><tr><td>
<p><strong>ZREALIZOWANY PORZĄDEK DZIENNY<br>30. POSIEDZENIA SEJMU</strong></p>
<ol>
  <li><p><strong>Sprawozdanie</strong> Komisji o rządowym projekcie ustawy
      o zmianie ustawy - Kodeks karny (druki nr <a href="#">876, 984 i 984-A</a>).</p></li>
  <li><p><strong>Zmiany</strong> w składach osobowych komisji sejmowych (druk nr 1087).</p></li>
</ol>
</td></tr></tbody></table></div>
"""


class FakeGeminiClient(GeminiClient):
    def __init__(self, response_json: str) -> None:
        super().__init__(api_key="test", model="test-model")
        self.response_json = response_json
        self.prompts: list[str] = []

    async def _generate_content(self, model, contents, config):
        self.prompts.append(contents)
        part = types.Part.from_text(text=self.response_json)
        return types.GenerateContentResponse(
            candidates=[types.Candidate(content=types.Content(role="model", parts=[part]))]
        )


def test_agenda_text_returns_one_point_per_line_without_header():
    assert agenda_text(AGENDA_HTML) == (
        "Sprawozdanie Komisji o rządowym projekcie ustawy o zmianie ustawy - Kodeks karny "
        "(druki nr 876, 984 i 984-A).\n"
        "Zmiany w składach osobowych komisji sejmowych (druk nr 1087)."
    )


def test_agenda_text_is_none_without_points():
    assert agenda_text(None) is None
    assert agenda_text("") is None
    assert agenda_text("<div><p>Porządek zostanie ogłoszony.</p></div>") is None


def test_summarize_sitting_sends_agenda_and_parses_result():
    client = FakeGeminiClient('{"description": "Zdanie pierwsze. Zdanie drugie."}')

    summary = asyncio.run(summarize_sitting(client, "30. Posiedzenie Sejmu RP", "Punkt o Kodeksie karnym."))

    assert summary.description == "Zdanie pierwsze. Zdanie drugie."
    assert "30. Posiedzenie Sejmu RP" in client.prompts[0]
    assert "Punkt o Kodeksie karnym." in client.prompts[0]


def test_retry_after_reads_delay_from_rate_limit_error():
    rate_limited = errors.ClientError(
        429,
        {"error": {"code": 429, "details": [{"@type": "x/google.rpc.Help"}, {"retryDelay": "30s"}]}},
    )

    assert retry_after(rate_limited) == timedelta(seconds=30)
    assert retry_after(errors.ServerError(503, {"error": {"code": 503}})) is None
    assert retry_after(RuntimeError("boom")) is None


def test_print_number_takes_first_print_of_voting_title():
    assert print_number("Pkt. 2 Sprawozdanie Komisji (druki nr 876, 984 i 984-A)") == "876"
    assert print_number("Pkt. 5 Zmiany w składach komisji (druk nr 1087)") == "1087"
    assert print_number("Wniosek o przerwę") is None
