"""Lead.is_deliverable() and the cross-turn merge accumulator."""

from __future__ import annotations

from core.schemas import Lead, Priority, merge_lead


def test_empty_lead_not_deliverable() -> None:
    assert Lead().is_deliverable() is False


def test_contact_plus_intent_is_deliverable() -> None:
    assert Lead(name="Ana", intent="Botox consultation").is_deliverable() is True
    assert Lead(phone="305-555-0100", service="Lip Filler").is_deliverable() is True


def test_contact_without_intent_not_deliverable() -> None:
    assert Lead(name="Ana").is_deliverable() is False


def test_needs_human_always_deliverable() -> None:
    # Even with no contact, an escalation must reach the team.
    assert Lead(needs_human=True).is_deliverable() is True


def test_merge_keeps_prior_values_when_new_is_empty() -> None:
    prev = Lead(name="Ana", phone="305-555-0100")
    latest = Lead(service="Botox")  # new turn only supplied a service
    merged = merge_lead(prev, latest)
    assert merged.name == "Ana"
    assert merged.phone == "305-555-0100"
    assert merged.service == "Botox"


def test_merge_applies_correction() -> None:
    prev = Lead(phone="305-111-1111")
    latest = Lead(phone="305-222-2222")  # caller corrected their number
    assert merge_lead(prev, latest).phone == "305-222-2222"


def test_merge_needs_human_is_sticky() -> None:
    prev = Lead(needs_human=True)
    latest = Lead(needs_human=False)
    assert merge_lead(prev, latest).needs_human is True


def test_merge_priority_only_rises() -> None:
    prev = Lead(priority=Priority.HIGH)
    latest = Lead(priority=Priority.LOW)
    assert merge_lead(prev, latest).priority is Priority.HIGH
