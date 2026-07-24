"""The Brain — pure decision helpers.

The Brain decides WHAT to say and WHICH actions to request; it performs no side
effects and knows nothing about how actions are implemented. These helpers are
the pure parts: the safe fallback and the grounding backstop. The model↔tool
loop that connects the Brain's decisions to the Action Engine lives in
``core.orchestrator`` so that this reasoning layer stays free of I/O.

Only operational metadata and the final structured output are ever logged —
never hidden reasoning (there is none; the model returns only the structured
fields).
"""

from __future__ import annotations

from core.schemas import ConversationState, Lead, TurnResult
from core.states import clamp_transition

_FALLBACK_SAY = (
    "I'm sorry, I'm having a little trouble on my end right now. Let me take your "
    "details so someone from our team can follow up with you shortly. May I get "
    "your name and the best phone number to reach you?"
)


def safe_fallback(state: ConversationState, lead: Lead) -> TurnResult:
    """Deterministic, never-hallucinating response used when a turn fails."""
    return TurnResult(
        say=_FALLBACK_SAY,
        state=clamp_transition(state, ConversationState.ESCALATION),
        lead=lead.model_copy(update={"needs_human": True}),
        grounded=True,  # invents no facts
        end_call=False,
    )


def enforce_grounding_safety(result: TurnResult) -> TurnResult:
    """Backstop for ungrounded turns: flag for human follow-up.

    When the model reports grounded=false it must not have invented anything; it
    should be handing off to a human. We guarantee needs_human regardless of what
    else the model returned. (The orchestrator additionally records a lead when a
    turn is ungrounded.)
    """
    if result.grounded:
        return result
    return result.model_copy(update={"lead": result.lead.model_copy(update={"needs_human": True})})
