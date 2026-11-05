import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from google.genai import types

ToolHandler = Callable[..., Awaitable[dict[str, Any]] | dict[str, Any]]


@dataclass(frozen=True)
class GeminiTool:
    """A function Gemini may call. `parameters` is a JSON Schema for the arguments."""

    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler

    def declaration(self) -> types.FunctionDeclaration:
        return types.FunctionDeclaration(
            name=self.name,
            description=self.description,
            parameters_json_schema=self.parameters,
        )

    async def run(self, args: dict[str, Any]) -> dict[str, Any]:
        result = self.handler(**args)
        if inspect.isawaitable(result):
            result = await result
        return result
