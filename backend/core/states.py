"""Finite-state-machine definition for a Stedden conversation.

The Brain proposes the next state; the worker validates it here. If the model
ever proposes an illegal jump, we clamp to a safe state rather than trust it —
the FSM is a guardrail, not just documentation.
"""

from __future__ import annotations

from core.schemas import ConversationState as S

#: The state every call begins in.
INITIAL_STATE: S = S.GREETING

#: Terminal state. No transitions out.
TERMINAL_STATE: S = S.GOODBYE

#: Allowed transitions. ESCALATION and LEAD_CAPTURE are reachable from most
#: states because a caller can pivot to "book me in" or "I need a person" at any
#: point. GOODBYE is reachable from everywhere (caller can always hang up).
ALLOWED_TRANSITIONS: dict[S, set[S]] = {
    S.GREETING: {S.GREETING, S.INTENT_DISCOVERY, S.FAQ, S.LEAD_CAPTURE, S.ESCALATION, S.GOODBYE},
    S.INTENT_DISCOVERY: {S.INTENT_DISCOVERY, S.FAQ, S.LEAD_CAPTURE, S.ESCALATION, S.GOODBYE},
    S.FAQ: {S.FAQ, S.INTENT_DISCOVERY, S.LEAD_CAPTURE, S.ESCALATION, S.GOODBYE},
    S.LEAD_CAPTURE: {S.LEAD_CAPTURE, S.FAQ, S.CONFIRMATION, S.ESCALATION, S.GOODBYE},
    S.CONFIRMATION: {S.CONFIRMATION, S.LEAD_CAPTURE, S.FAQ, S.GOODBYE},
    S.ESCALATION: {S.ESCALATION, S.LEAD_CAPTURE, S.CONFIRMATION, S.GOODBYE},
    S.GOODBYE: set(),
}


def is_valid_transition(current: S, proposed: S) -> bool:
    return proposed in ALLOWED_TRANSITIONS.get(current, set())


def clamp_transition(current: S, proposed: S) -> S:
    """Return the proposed state if legal, else stay put (or end if current is terminal).

    Staying in the current state on an illegal proposal is always safe: the
    conversation continues rather than teleporting somewhere incoherent.
    """
    if current is TERMINAL_STATE:
        return TERMINAL_STATE
    return proposed if is_valid_transition(current, proposed) else current
