"""Core contracts for Stedden.

This module is the single source of truth for the shapes that flow between the
three layers of the system:

    ConversationRelay (audio)  ->  voice_worker  ->  orchestrator  ->  Brain
                                                             |
                                                             v
                                                     Business Action Engine

Two architectural invariants are encoded here:

1. The Brain decides WHAT to say and WHICH business actions to request; it never
   performs side effects and never knows how an action is implemented. Actions
   are executed mid-turn by the Action Engine (see ``core.actions``) so the Brain
   can read real data before making factual claims; the final ``TurnResult`` it
   returns carries speech, the running lead, the next FSM state, grounding, and
   the end-call control flag.

2. Every conversation is a finite state machine. The Brain always receives the
   current ``ConversationState`` and returns the next one.

Nothing in this file imports web, database, or telephony code. Keep it that way.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class ConversationState(StrEnum):
    """The finite set of states a call can be in.

    The Brain receives the current state as input and returns the next state on
    every turn. Transitions are advisory to the model but validated by the
    worker against ``ALLOWED_TRANSITIONS`` in ``core.states``.
    """

    GREETING = "greeting"
    INTENT_DISCOVERY = "intent_discovery"
    FAQ = "faq"
    LEAD_CAPTURE = "lead_capture"
    CONFIRMATION = "confirmation"
    ESCALATION = "escalation"
    GOODBYE = "goodbye"


class Priority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Lead(BaseModel):
    """The structured lead, refined incrementally on every turn.

    Fields default to empty strings (never null) so the model always returns a
    complete object and the worker never has to reason about missing keys. A
    field is considered "captured" when it is non-empty.
    """

    name: str = ""
    phone: str = ""
    service: str = ""
    intent: str = ""
    preferred_time: str = ""
    summary: str = ""
    priority: Priority = Priority.LOW
    needs_human: bool = False

    def is_deliverable(self) -> bool:
        """Enough signal to be worth emailing to the spa owner.

        We deliver on either a reachable contact (name or phone) plus a stated
        intent, or an explicit human-escalation flag. Tuned to favour recall:
        a lost consultation request is far more costly than an extra email.
        """
        has_contact = bool(self.name or self.phone)
        return self.needs_human or (has_contact and bool(self.intent or self.service))


class TurnResult(BaseModel):
    """The Brain's final spoken output for one turn.

    Business actions have already been executed by the Action Engine (mid-turn,
    via the tool loop) by the time this is produced, so ``say`` can safely reflect
    real results. This is the strict structured output handed to the Responses
    API, so nothing downstream parses free text.
    """

    say: str = Field(description="Exactly what the receptionist should speak next.")
    state: ConversationState = Field(description="The state the call is now in.")
    lead: Lead = Field(description="The running lead, refined this turn.")
    grounded: bool = Field(
        description=(
            "True only if `say` is fully supported by the knowledge base or by data "
            "returned from a business action this turn. False signals a "
            "hallucination risk and is logged on every turn."
        ),
    )
    end_call: bool = Field(
        default=False,
        description="True when the conversation is complete and the call should hang up.",
    )

    @property
    def should_end_call(self) -> bool:
        """Single source of truth for hangup, derived — never guessed."""
        return self.end_call or self.state is ConversationState.GOODBYE


class Message(BaseModel):
    """One turn of conversation history. Only speech is stored — never any hidden
    model reasoning."""

    role: Literal["user", "assistant"]
    content: str


#: Ordering used to combine priorities when merging leads across turns.
_PRIORITY_RANK: dict[Priority, int] = {
    Priority.LOW: 0,
    Priority.MEDIUM: 1,
    Priority.HIGH: 2,
}


def merge_lead(previous: Lead, latest: Lead) -> Lead:
    """Combine the running lead with the Brain's newest lead for this turn.

    The Brain already receives the running lead and is asked to refine it, but we
    never trust it to remember: this accumulator keeps previously captured values
    unless the caller supplied a new, non-empty one (which is how corrections —
    e.g. a fixed phone number — take effect). ``needs_human`` is sticky and
    ``priority`` only ever rises.
    """

    def keep_latest_if_set(old: str, new: str) -> str:
        return new if new.strip() else old

    priority = max(previous.priority, latest.priority, key=lambda p: _PRIORITY_RANK[p])
    return Lead(
        name=keep_latest_if_set(previous.name, latest.name),
        phone=keep_latest_if_set(previous.phone, latest.phone),
        service=keep_latest_if_set(previous.service, latest.service),
        intent=keep_latest_if_set(previous.intent, latest.intent),
        preferred_time=keep_latest_if_set(previous.preferred_time, latest.preferred_time),
        # Summary is a rolling synopsis: prefer the newest non-empty version.
        summary=latest.summary.strip() or previous.summary,
        priority=priority,
        needs_human=previous.needs_human or latest.needs_human,
    )
