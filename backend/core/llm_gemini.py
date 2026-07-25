"""Gemini implementation of ``TurnGenerator`` (Google Gen AI SDK).

One turn runs in two phases, because Gemini cannot combine function tools with a
strict ``response_schema`` in a single request:

1. **Tool phase** — the model is given the business-action declarations and may
   request actions. It never executes them: each call is handed to the injected
   ``execute_tool``, which routes through the Action Engine, and the result is fed
   back as a function response. Automatic function calling is explicitly disabled
   so the SDK cannot invoke anything on its own.
2. **Final phase** — tools off, ``response_schema=TurnResult`` on. The model turns
   what it learned into the structured turn.

The returned JSON is re-validated by the orchestrator against the Pydantic model,
and FSM transitions stay under deterministic control there — this module only
produces candidate output.
"""

from __future__ import annotations

from typing import Any

import structlog
from google import genai
from google.genai import types

from core.actions import TOOL_DESCRIPTIONS, TOOL_PARAMETERS, ActionName
from core.errors import BrainGenerationError
from core.schemas import Message, TurnResult

log = structlog.get_logger("stedden.llm.gemini")

MAX_TOOL_ITERATIONS = 5

FINAL_INSTRUCTION = (
    "Now produce the structured result for this turn: what to say, the FSM state "
    "the call is in after your reply, the refined lead, whether you were grounded, "
    "and whether the call should end. Base any factual claim only on the knowledge "
    "base and on results returned by the actions above."
)


def _gemini_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Convert our JSON Schema to the OpenAPI subset Gemini accepts.

    Gemini rejects ``additionalProperties``; everything else we emit (object,
    string, boolean, enum, description) is already supported.
    """
    cleaned = {k: v for k, v in schema.items() if k != "additionalProperties"}
    if "properties" in cleaned:
        cleaned["properties"] = {
            name: _gemini_schema(spec) if isinstance(spec, dict) else spec
            for name, spec in cleaned["properties"].items()
        }
    return cleaned


def build_tools() -> list[types.Tool]:
    """One Tool carrying every allowed business action. The model can only choose
    from this list — it cannot invent an action."""
    declarations = [
        types.FunctionDeclaration(
            name=name.value,
            description=TOOL_DESCRIPTIONS[name],
            parameters=_gemini_schema(TOOL_PARAMETERS[name]),
        )
        for name in ActionName
    ]
    return [types.Tool(function_declarations=declarations)]


def _to_contents(history: list[Message]) -> list[types.Content]:
    """Map our transcript to Gemini turns. Gemini's assistant role is ``model``."""
    return [
        types.Content(
            role="model" if message.role == "assistant" else "user",
            parts=[types.Part(text=message.content)],
        )
        for message in history
    ]


class GeminiTurnGenerator:
    """Gemini-backed Brain. The model decides; the Action Engine acts."""

    def __init__(self, client: genai.Client, model: str, timeout: float) -> None:
        self._client = client
        self._model = model
        # The SDK takes HTTP options in milliseconds.
        self._http_options = types.HttpOptions(timeout=int(timeout * 1000))
        self._tools = build_tools()

    async def run(self, *, system_prompt: str, history: list[Message], execute_tool) -> str:
        contents = _to_contents(history)

        # ── Phase 1: let the model gather real data / request writes ──────────
        for _ in range(MAX_TOOL_ITERATIONS):
            response = await self._generate(
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    tools=self._tools,
                    # Never let the SDK execute anything itself.
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                    http_options=self._http_options,
                ),
            )
            calls = list(response.function_calls or [])
            if not calls:
                break

            contents.append(_model_turn(response))
            results = []
            for call in calls:
                result = await execute_tool(call.name, dict(call.args or {}))
                results.append(types.Part.from_function_response(name=call.name, response=result))
            contents.append(types.Content(role="user", parts=results))
        else:
            log.warning("gemini.tool_budget_exhausted", model=self._model)

        # ── Phase 2: structured turn, tools off ───────────────────────────────
        contents.append(types.Content(role="user", parts=[types.Part(text=FINAL_INSTRUCTION)]))
        final = await self._generate(
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                response_mime_type="application/json",
                response_schema=TurnResult,
                http_options=self._http_options,
            ),
        )
        text = (final.text or "").strip()
        if not text:
            raise BrainGenerationError("Gemini returned no structured output")
        return text

    async def _generate(self, *, contents, config):
        try:
            return await self._client.aio.models.generate_content(
                # Snapshot: `contents` keeps growing across the turn's phases, and
                # a request must not be mutated after it has been handed off.
                model=self._model,
                contents=list(contents),
                config=config,
            )
        except Exception as exc:
            raise BrainGenerationError(f"Gemini turn failed: {exc}") from exc


def _model_turn(response) -> types.Content:
    """Echo the model's own tool-call turn back into the conversation."""
    candidates = getattr(response, "candidates", None)
    if candidates and candidates[0].content is not None:
        return candidates[0].content
    # Fall back to reconstructing the calls if the SDK shape differs.
    return types.Content(
        role="model",
        parts=[
            types.Part(function_call=types.FunctionCall(name=c.name, args=c.args))
            for c in (response.function_calls or [])
        ],
    )
