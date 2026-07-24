"""TurnResult schema validation and derived behavior."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.schemas import ConversationState, Lead, TurnResult


def test_valid_turn_result_round_trips() -> None:
    raw = TurnResult(
        say="We're open until 6pm today.",
        state=ConversationState.FAQ,
        lead=Lead(),
        grounded=True,
    ).model_dump_json()
    parsed = TurnResult.model_validate_json(raw)
    assert parsed.state is ConversationState.FAQ
    assert parsed.grounded is True
    assert parsed.end_call is False


def test_invalid_state_rejected() -> None:
    with pytest.raises(ValidationError):
        TurnResult.model_validate({"say": "hi", "state": "chatting", "lead": {}, "grounded": True})


def test_missing_required_field_rejected() -> None:
    with pytest.raises(ValidationError):
        TurnResult.model_validate({"say": "hi", "state": "faq", "lead": {}})


def test_should_end_call_derivation() -> None:
    via_state = TurnResult(say="Bye!", state=ConversationState.GOODBYE, lead=Lead(), grounded=True)
    assert via_state.should_end_call is True

    via_flag = TurnResult(
        say="Bye!", state=ConversationState.FAQ, lead=Lead(), grounded=True, end_call=True
    )
    assert via_flag.should_end_call is True

    ongoing = TurnResult(say="Sure!", state=ConversationState.FAQ, lead=Lead(), grounded=True)
    assert ongoing.should_end_call is False
