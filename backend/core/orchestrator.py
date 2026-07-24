"""Turn orchestrator — wires the Brain's decisions to the Action Engine.

For one turn it:
  1. builds the system prompt (Brain, pure),
  2. runs the generator's model↔tool loop, executing each requested action
     through the Action Engine mid-turn (so the Brain reads real data / sees real
     write results before it speaks),
  3. validates the final structured output, clamps the FSM transition, and
     applies the grounding backstop.

Side effects live in the Action Engine; reasoning lives in the Brain; this module
is the thin conductor between them. Both the simulator and the voice worker use
it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import structlog
from pydantic import ValidationError

from core.actions import ActionName
from core.brain import enforce_grounding_safety, safe_fallback
from core.errors import BrainGenerationError
from core.kb import KnowledgeBase
from core.llm import TurnGenerator
from core.prompt import build_system_prompt
from core.schemas import ConversationState, Lead, Message, TurnResult
from core.states import clamp_transition
from engine.context import ActionContext, ActionRecord
from engine.engine import ActionEngine

log = structlog.get_logger("stedden.orchestrator")


@dataclass(slots=True)
class TurnOutcome:
    result: TurnResult
    actions: list[ActionRecord]


async def run_turn(
    *,
    kb: KnowledgeBase,
    history: list[Message],
    state: ConversationState,
    lead: Lead,
    generator: TurnGenerator,
    engine: ActionEngine,
    ctx: ActionContext,
) -> TurnOutcome:
    """Produce one turn. Business actions are executed via ``engine`` into ``ctx``."""
    system_prompt = build_system_prompt(kb, state, lead)
    started = time.perf_counter()
    audit_start = len(ctx.audit)  # ctx is per-call; slice out just this turn's actions

    async def execute_tool(name: str, args: dict) -> dict:
        return await engine.dispatch(name, args, ctx)

    try:
        final_json = await generator.run(
            system_prompt=system_prompt, history=history, execute_tool=execute_tool
        )
        result = TurnResult.model_validate_json(final_json)
    except (BrainGenerationError, ValidationError, ValueError) as exc:
        log.warning(
            "orchestrator.fallback",
            state_in=state.value,
            error=type(exc).__name__,
            detail=str(exc)[:200],
        )
        return TurnOutcome(result=safe_fallback(state, lead), actions=ctx.audit[audit_start:])

    result = result.model_copy(update={"state": clamp_transition(state, result.state)})
    result = enforce_grounding_safety(result)

    # Ungrounded backstop: make sure the caller's details are captured for a human.
    if not result.grounded and not _has_lead_action(ctx):
        await engine.dispatch(
            ActionName.CREATE_LEAD.value,
            {
                "name": result.lead.name,
                "phone": result.lead.phone,
                "service": result.lead.service,
                "intent": result.lead.intent or "unresolved question",
                "summary": result.lead.summary,
                "needs_human": True,
            },
            ctx,
        )

    log.info(
        "orchestrator.turn",
        state_in=state.value,
        state_out=result.state.value,
        grounded=result.grounded,
        actions=[r.action for r in ctx.audit[audit_start:]],
        needs_human=result.lead.needs_human,
        latency_ms=round((time.perf_counter() - started) * 1000),
    )
    return TurnOutcome(result=result, actions=ctx.audit[audit_start:])


def _has_lead_action(ctx: ActionContext) -> bool:
    return any(r.action == ActionName.CREATE_LEAD.value for r in ctx.audit)
