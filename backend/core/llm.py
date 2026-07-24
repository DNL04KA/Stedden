"""LLM adapter: drives one turn's model↔tool loop and returns the final
structured ``TurnResult`` as JSON.

The generator owns all OpenAI-specific plumbing (function tools, tool outputs).
It calls back into an injected ``execute_tool`` for every action the model
requests, so the caller (the orchestrator) keeps side effects in the Action
Engine. Tests substitute a scripted generator with the same ``run`` shape.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Protocol

from openai import AsyncOpenAI

from core.actions import tool_specs
from core.errors import BrainGenerationError
from core.schemas import Message, TurnResult

ExecuteTool = Callable[[str, dict], Awaitable[dict]]

MAX_TOOL_ITERATIONS = 5


class TurnGenerator(Protocol):
    async def run(
        self, *, system_prompt: str, history: list[Message], execute_tool: ExecuteTool
    ) -> str:
        """Return the final TurnResult as JSON, executing tools via ``execute_tool``."""


class OpenAITurnGenerator:
    """OpenAI Responses API with function tools + strict structured final output.

    The model is injected (from env) so a faster model is a config change, not a
    code change.
    """

    def __init__(self, client: AsyncOpenAI, model: str, timeout: float) -> None:
        self._client = client
        self._model = model
        self._timeout = timeout
        self._tools = tool_specs()

    async def run(self, *, system_prompt, history, execute_tool) -> str:
        input_list: list = [{"role": "system", "content": system_prompt}]
        input_list += [{"role": m.role, "content": m.content} for m in history]

        for _ in range(MAX_TOOL_ITERATIONS):
            try:
                resp = await self._client.responses.parse(
                    model=self._model,
                    input=input_list,
                    tools=self._tools,
                    text_format=TurnResult,
                    timeout=self._timeout,
                )
            except Exception as exc:
                raise BrainGenerationError(f"OpenAI turn failed: {exc}") from exc

            if resp.output_parsed is not None:
                return resp.output_parsed.model_dump_json()

            calls = [item for item in resp.output if getattr(item, "type", None) == "function_call"]
            if not calls:
                raise BrainGenerationError("model produced neither tool calls nor final output")

            input_list.extend(resp.output)  # echo the model's tool-call items back
            for call in calls:
                try:
                    args = json.loads(call.arguments) if call.arguments else {}
                except json.JSONDecodeError:
                    args = {}
                result = await execute_tool(call.name, args)
                input_list.append(
                    {
                        "type": "function_call_output",
                        "call_id": call.call_id,
                        "output": json.dumps(result),
                    }
                )

        raise BrainGenerationError("exceeded tool-iteration budget")
