"""Gemini provider against a mocked Google Gen AI client — no network, no key.

What these lock down:
- the two-phase turn (tools, then structured output),
- the model may only *request* actions; execution stays in our injected callback,
- automatic function calling is disabled so the SDK can never act on its own,
- tool declarations carry exactly the allowed action vocabulary,
- malformed or empty output degrades to the Brain's safe fallback, not a crash.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest
from google.genai import types

from core.actions import ActionName
from core.errors import BrainGenerationError
from core.llm_factory import GEMINI, OPENAI, LLMConfigurationError, build_generator
from core.llm_gemini import GeminiTurnGenerator, _gemini_schema, build_tools
from core.schemas import ConversationState as S
from core.schemas import Lead, Message, TurnResult
from tests.harness import internal_session_with


# ── Mock SDK surface ─────────────────────────────────────────────────────────
@dataclass
class FakeCall:
    name: str
    args: dict[str, Any]


@dataclass
class FakeResponse:
    text: str | None = None
    function_calls: list[FakeCall] = field(default_factory=list)
    candidates: list = field(default_factory=list)


class FakeModels:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        if not self._responses:
            raise AssertionError("FakeModels ran out of scripted responses")
        return self._responses.pop(0)


class FakeClient:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.models = FakeModels(responses)
        self.aio = type("Aio", (), {"models": self.models})()


def final_turn(say: str, state=S.FAQ, *, lead=None, grounded=True, end_call=False) -> FakeResponse:
    payload = TurnResult(
        say=say, state=state, lead=lead or Lead(), grounded=grounded, end_call=end_call
    ).model_dump_json()
    return FakeResponse(text=payload)


def generator_for(responses: list[FakeResponse]) -> tuple[GeminiTurnGenerator, FakeClient]:
    client = FakeClient(responses)
    return GeminiTurnGenerator(client, model="gemini-2.5-flash", timeout=8.0), client


async def _noop_tool(name: str, args: dict) -> dict:
    return {"ok": True}


# ── Tool declarations ────────────────────────────────────────────────────────
def test_declares_exactly_the_allowed_actions() -> None:
    tools = build_tools()
    assert len(tools) == 1
    declared = {d.name for d in tools[0].function_declarations}
    assert declared == {a.value for a in ActionName}


def test_schema_conversion_strips_unsupported_keys() -> None:
    """Gemini rejects additionalProperties; nested objects must be cleaned too."""
    cleaned = _gemini_schema(
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["phone"],
            "properties": {
                "phone": {"type": "string"},
                "nested": {"type": "object", "additionalProperties": False, "properties": {}},
            },
        }
    )
    assert "additionalProperties" not in cleaned
    assert "additionalProperties" not in cleaned["properties"]["nested"]
    assert cleaned["required"] == ["phone"]  # everything else survives


# ── Turn shape ───────────────────────────────────────────────────────────────
async def test_returns_valid_turnresult_json() -> None:
    generator, client = generator_for([FakeResponse(), final_turn("We're open until 6pm.")])
    raw = await generator.run(
        system_prompt="rules",
        history=[Message(role="user", content="hours?")],
        execute_tool=_noop_tool,
    )
    parsed = TurnResult.model_validate_json(raw)  # re-validated, as in production
    assert parsed.say == "We're open until 6pm."
    assert parsed.state is S.FAQ


async def test_final_phase_requests_structured_output_without_tools() -> None:
    generator, client = generator_for([FakeResponse(), final_turn("Sure.")])
    await generator.run(system_prompt="rules", history=[], execute_tool=_noop_tool)

    tool_phase, final_phase = client.models.calls
    assert tool_phase["config"].tools  # phase 1 offers the actions
    assert tool_phase["config"].response_schema is None
    # Phase 2 is schema-constrained and tool-free (Gemini can't combine them).
    assert final_phase["config"].tools is None
    assert final_phase["config"].response_schema is TurnResult
    assert final_phase["config"].response_mime_type == "application/json"


async def test_automatic_function_calling_is_disabled() -> None:
    """The SDK must never invoke a business action itself — our engine does."""
    generator, client = generator_for([FakeResponse(), final_turn("Sure.")])
    await generator.run(system_prompt="rules", history=[], execute_tool=_noop_tool)
    afc = client.models.calls[0]["config"].automatic_function_calling
    assert afc is not None and afc.disable is True


async def test_history_roles_are_mapped_to_gemini() -> None:
    generator, client = generator_for([FakeResponse(), final_turn("Hi.")])
    await generator.run(
        system_prompt="rules",
        history=[
            Message(role="assistant", content="Thanks for calling."),
            Message(role="user", content="Hi"),
        ],
        execute_tool=_noop_tool,
    )
    roles = [c.role for c in client.models.calls[0]["contents"]]
    assert roles == ["model", "user"]  # Gemini calls the assistant "model"


# ── Tool loop ────────────────────────────────────────────────────────────────
async def test_requested_action_is_executed_by_our_callback() -> None:
    executed: list[tuple[str, dict]] = []

    async def record(name: str, args: dict) -> dict:
        executed.append((name, args))
        return {"slots": [{"label": "Fri 2:00 PM"}], "count": 1}

    generator, client = generator_for(
        [
            FakeResponse(
                function_calls=[
                    FakeCall("check_availability", {"service": "Botox", "date": "2026-07-31"})
                ]
            ),
            FakeResponse(),  # no more calls
            final_turn("I have Friday at 2 PM."),
        ]
    )
    raw = await generator.run(
        system_prompt="rules",
        history=[Message(role="user", content="Botox Friday?")],
        execute_tool=record,
    )

    assert executed == [("check_availability", {"service": "Botox", "date": "2026-07-31"})]
    assert TurnResult.model_validate_json(raw).say == "I have Friday at 2 PM."
    # The tool result was fed back to the model before it produced the answer.
    second_turn_contents = client.models.calls[1]["contents"]
    assert any(
        part.function_response is not None
        for content in second_turn_contents
        for part in (content.parts or [])
    )


async def test_tool_iteration_budget_is_bounded() -> None:
    """A model that keeps calling tools still terminates with a structured turn."""
    looping = [FakeResponse(function_calls=[FakeCall("get_appointment", {"phone": "1"})])] * 5
    generator, client = generator_for([*looping, final_turn("Let me take a message.")])
    raw = await generator.run(system_prompt="rules", history=[], execute_tool=_noop_tool)
    assert TurnResult.model_validate_json(raw).say == "Let me take a message."


# ── Failure handling ─────────────────────────────────────────────────────────
async def test_empty_output_raises_generation_error() -> None:
    generator, _ = generator_for([FakeResponse(), FakeResponse(text="")])
    with pytest.raises(BrainGenerationError):
        await generator.run(system_prompt="rules", history=[], execute_tool=_noop_tool)


async def test_sdk_exception_becomes_generation_error() -> None:
    class ExplodingModels:
        async def generate_content(self, **kwargs):
            raise RuntimeError("quota exceeded")

    client = type("C", (), {"aio": type("A", (), {"models": ExplodingModels()})()})()
    generator = GeminiTurnGenerator(client, model="gemini-2.5-flash", timeout=8.0)
    with pytest.raises(BrainGenerationError):
        await generator.run(system_prompt="rules", history=[], execute_tool=_noop_tool)


async def test_malformed_json_degrades_to_safe_fallback_not_a_crash() -> None:
    """End-to-end through the orchestrator: a broken Gemini reply keeps the
    conversation alive and flags it for a human."""
    generator, _ = generator_for([FakeResponse(), FakeResponse(text="{ not json")])
    session, _ = internal_session_with(generator)
    view = await session.send("Do you take my insurance?")

    assert view.result.say  # something was said
    assert view.lead.needs_human is True
    assert view.state is S.ESCALATION


async def test_illegal_state_from_gemini_is_clamped() -> None:
    """FSM enforcement stays deterministic regardless of what the model returns."""
    generator, _ = generator_for(
        [FakeResponse(), final_turn("Great.", S.CONFIRMATION)]  # FAQ->CONFIRMATION is illegal
    )
    session, _ = internal_session_with(generator)
    await session.send("What are your hours?")
    assert session.state is not S.CONFIRMATION


async def test_gemini_drives_a_real_booking_through_the_action_engine() -> None:
    """The Gemini path uses the same Action Engine and providers as OpenAI."""
    from datetime import date, timedelta

    friday = date.today() + timedelta(days=((4 - date.today().weekday()) % 7) or 7)
    generator, _ = generator_for(
        [
            FakeResponse(
                function_calls=[
                    FakeCall(
                        "create_appointment",
                        {
                            "name": "Ana",
                            "phone": "305-555-0100",
                            "service": "Botox",
                            "start": f"{friday}T14:00:00",
                        },
                    )
                ]
            ),
            FakeResponse(),
            final_turn("You're booked for Friday at 2 PM.", S.GOODBYE, end_call=True),
        ]
    )
    session, data = internal_session_with(generator)
    view = await session.send("Book Botox Friday 2pm, Ana, 305-555-0100")

    assert len(data.appointments) == 1
    booked = next(iter(data.appointments.values()))
    assert booked.service == "Botox" and booked.status == "booked"
    assert any(a.action == "create_appointment" and a.status == "success" for a in view.actions)


# ── Provider factory ─────────────────────────────────────────────────────────
def test_factory_builds_gemini_without_openai_key() -> None:
    from core.config import Settings

    settings = Settings(llm_provider=GEMINI, gemini_api_key="test-key", openai_api_key="")
    generator = build_generator(settings)
    assert isinstance(generator, GeminiTurnGenerator)


def test_factory_requires_the_selected_provider_key() -> None:
    from core.config import Settings

    with pytest.raises(LLMConfigurationError, match="GEMINI_API_KEY"):
        build_generator(Settings(llm_provider=GEMINI, gemini_api_key=""))
    with pytest.raises(LLMConfigurationError, match="OPENAI_API_KEY"):
        build_generator(Settings(llm_provider=OPENAI, openai_api_key=""))


def test_factory_rejects_unknown_provider() -> None:
    from core.config import Settings

    with pytest.raises(LLMConfigurationError, match="unknown LLM_PROVIDER"):
        build_generator(Settings(llm_provider="llama-at-home"))


def test_openai_remains_available_as_an_alternative() -> None:
    from core.config import Settings
    from core.llm import OpenAITurnGenerator

    generator = build_generator(Settings(llm_provider=OPENAI, openai_api_key="sk-test"))
    assert isinstance(generator, OpenAITurnGenerator)


def test_default_provider_is_gemini() -> None:
    from core.config import Settings

    assert Settings().llm_provider == "gemini"


def test_function_response_parts_are_json_serializable() -> None:
    """Engine results must round-trip into a Gemini function response part."""
    part = types.Part.from_function_response(
        name="check_availability", response={"count": 2, "slots": [{"label": "Fri 2 PM"}]}
    )
    assert part.function_response is not None
    assert json.loads(json.dumps(part.function_response.response))["count"] == 2
