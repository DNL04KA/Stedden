"""Orchestrator guardrails: safe fallback, FSM clamp, grounding backstop."""

from __future__ import annotations

from core.actions import ActionName
from core.schemas import ConversationState as S
from core.schemas import Lead, TurnResult
from tests.fakes import MalformedGenerator, RaisingGenerator, ScriptedTurn
from tests.harness import internal_session_with, make_internal_session


async def test_malformed_output_triggers_safe_fallback() -> None:
    session, _ = internal_session_with(MalformedGenerator())
    view = await session.send("hi")
    assert view.result.grounded is True  # fallback invents nothing
    assert view.lead.needs_human is True
    assert view.state is S.ESCALATION


async def test_api_failure_triggers_safe_fallback() -> None:
    session, _ = internal_session_with(RaisingGenerator())
    view = await session.send("hi")
    assert view.lead.needs_human is True
    assert view.state is S.ESCALATION


async def test_illegal_state_is_clamped() -> None:
    # GREETING -> CONFIRMATION is illegal; must clamp back to GREETING.
    session, _ = make_internal_session(
        [
            ScriptedTurn(
                final=TurnResult(say="Sure.", state=S.CONFIRMATION, lead=Lead(), grounded=True)
            )
        ]
    )
    view = await session.send("hello")
    assert view.state is S.GREETING


async def test_ungrounded_turn_captures_lead_and_flags_human() -> None:
    session, _ = make_internal_session(
        [
            ScriptedTurn(
                final=TurnResult(
                    say="I'll have someone follow up on that.",
                    state=S.ESCALATION,
                    lead=Lead(name="Ana", phone="305-555-0100"),
                    grounded=False,
                )
            )
        ]
    )
    view = await session.send("Do you use product X I read about?")
    assert view.lead.needs_human is True
    # Orchestrator backstop records a lead for human follow-up.
    assert any(a.action == ActionName.CREATE_LEAD.value for a in view.actions)
