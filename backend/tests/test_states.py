"""FSM transition rules."""

from __future__ import annotations

from core.schemas import ConversationState as S
from core.states import (
    ALLOWED_TRANSITIONS,
    INITIAL_STATE,
    TERMINAL_STATE,
    clamp_transition,
    is_valid_transition,
)


def test_initial_and_terminal_states() -> None:
    assert INITIAL_STATE is S.GREETING
    assert TERMINAL_STATE is S.GOODBYE
    assert ALLOWED_TRANSITIONS[S.GOODBYE] == set()


def test_greeting_can_reach_core_states() -> None:
    for target in (S.INTENT_DISCOVERY, S.FAQ, S.LEAD_CAPTURE, S.ESCALATION, S.GOODBYE):
        assert is_valid_transition(S.GREETING, target)


def test_goodbye_is_reachable_from_every_non_terminal_state() -> None:
    for state in S:
        if state is TERMINAL_STATE:
            continue
        assert is_valid_transition(state, S.GOODBYE)


def test_illegal_transition_is_clamped_to_current() -> None:
    # FAQ cannot jump straight to CONFIRMATION.
    assert not is_valid_transition(S.FAQ, S.CONFIRMATION)
    assert clamp_transition(S.FAQ, S.CONFIRMATION) is S.FAQ


def test_legal_transition_passes_through() -> None:
    assert clamp_transition(S.LEAD_CAPTURE, S.CONFIRMATION) is S.CONFIRMATION


def test_terminal_state_never_transitions_out() -> None:
    assert clamp_transition(S.GOODBYE, S.GREETING) is S.GOODBYE
