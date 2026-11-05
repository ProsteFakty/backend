import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from functools import lru_cache
from typing import Any, TypeVar

from google import genai
from google.genai import errors, types
from pydantic import BaseModel
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_random_exponential

from app.config import get_settings
from app.utils.gemini_client.tools.base import GeminiTool

logger = logging.getLogger(__name__)

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class GeminiError(RuntimeError):
    pass


@dataclass
class ToolCallRecord:
    name: str
    args: dict[str, Any]
    result: dict[str, Any]


@dataclass
class GeminiResponse:
    text: str
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    usage: types.GenerateContentResponseUsageMetadata | None = None


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, errors.ServerError):
        return True
    return isinstance(exc, errors.ClientError) and exc.code == 429


def retry_after(exc: BaseException) -> timedelta | None:
    """How long Gemini asked us to wait (RetryInfo of a 429); None when the error does not say."""
    if not isinstance(exc, errors.APIError) or not isinstance(exc.details, dict):
        return None
    for detail in exc.details.get("error", {}).get("details", []):
        try:
            return timedelta(seconds=float(detail["retryDelay"].removesuffix("s")))
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    return None


class GeminiClient:
    """Thin async wrapper over google-genai with a manual tool-calling loop and retries.

    New features should build on `generate` (free text, optional tools) or
    `generate_structured` (JSON parsed into a Pydantic model) - see `tasks/`.
    """

    def __init__(self, api_key: str, model: str, *, max_tool_rounds: int = 5) -> None:
        if not api_key:
            raise GeminiError("GEMINI_API_KEY is not set")
        self._client = genai.Client(api_key=api_key)
        self.model = model
        self.max_tool_rounds = max_tool_rounds

    async def generate(
        self,
        prompt: str,
        *,
        system_instruction: str | None = None,
        tools: Sequence[GeminiTool] = (),
        attachments: Sequence[types.Part] = (),
        temperature: float | None = None,
        model: str | None = None,
    ) -> GeminiResponse:
        tool_map = {tool.name: tool for tool in tools}
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=temperature,
            tools=[types.Tool(function_declarations=[t.declaration() for t in tools])] if tools else None,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        # Attachments (e.g. a PDF via types.Part.from_bytes) go before the instruction text.
        user_parts = [*attachments, types.Part.from_text(text=prompt)]
        contents: list[types.Content] = [types.Content(role="user", parts=user_parts)]
        records: list[ToolCallRecord] = []

        for round_no in range(self.max_tool_rounds + 1):
            if round_no == self.max_tool_rounds and tools:
                # Out of tool budget: force the model to answer with what it has.
                config.tool_config = types.ToolConfig(
                    function_calling_config=types.FunctionCallingConfig(mode=types.FunctionCallingConfigMode.NONE)
                )

            response = await self._generate_content(model or self.model, contents, config)
            calls = response.function_calls or []
            if not calls:
                return GeminiResponse(text=response.text or "", tool_calls=records, usage=response.usage_metadata)

            # Append the model turn untouched - it carries thought signatures Gemini needs back.
            contents.append(response.candidates[0].content)
            result_parts = []
            for call in calls:
                args = dict(call.args or {})
                result = await self._run_tool(tool_map, call.name, args)
                records.append(ToolCallRecord(name=call.name, args=args, result=result))
                result_parts.append(
                    types.Part(function_response=types.FunctionResponse(id=call.id, name=call.name, response=result))
                )
            contents.append(types.Content(role="user", parts=result_parts))

        raise GeminiError(f"No final answer after {self.max_tool_rounds} tool rounds")

    async def generate_structured(
        self,
        prompt: str,
        schema: type[SchemaT],
        *,
        system_instruction: str | None = None,
        temperature: float | None = None,
        model: str | None = None,
    ) -> SchemaT:
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=temperature,
            response_mime_type="application/json",
            response_schema=schema,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        response = await self._generate_content(model or self.model, prompt, config)
        if isinstance(response.parsed, schema):
            return response.parsed
        if not response.text:
            raise GeminiError("Empty structured response")
        return schema.model_validate_json(response.text)

    @retry(
        retry=retry_if_exception(_is_retryable),
        # 503 "high demand" spikes can last minutes; randomised waits keep parallel calls from retrying in sync.
        wait=wait_random_exponential(multiplier=2, max=60),
        stop=stop_after_attempt(8),
        reraise=True,
    )
    async def _generate_content(
        self, model: str, contents: str | list[types.Content], config: types.GenerateContentConfig
    ) -> types.GenerateContentResponse:
        return await self._client.aio.models.generate_content(model=model, contents=contents, config=config)

    @staticmethod
    async def _run_tool(tool_map: dict[str, GeminiTool], name: str, args: dict[str, Any]) -> dict[str, Any]:
        tool = tool_map.get(name)
        if tool is None:
            return {"error": f"Unknown tool: {name}"}
        try:
            return await tool.run(args)
        except Exception as exc:  # report to the model so it can recover instead of failing the request
            logger.exception("Tool %s failed with args %s", name, args)
            return {"error": f"{type(exc).__name__}: {exc}"}


@lru_cache
def get_gemini_client() -> GeminiClient:
    return GeminiClient(api_key=get_settings().gemini_api_key, model=get_settings().gemini_model)
