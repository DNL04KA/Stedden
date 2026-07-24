"""Action Engine: routing, provider selection, error handling, idempotency."""

from __future__ import annotations

from engine.context import ActionContext
from engine.engine import ActionEngine
from providers.internal import (
    InMemoryCustomerProvider,
    InMemoryNotificationProvider,
    InMemorySchedulingProvider,
)
from providers.registry import STEDDEN_INTERNAL, ProviderBundle, ProviderConfig, resolve_providers
from seed.lumera import lumera_internal_dataset, lumera_kb

FRIDAY = "2026-07-24"


def _ctx():
    data = lumera_internal_dataset()
    internal = ProviderBundle(
        customer=InMemoryCustomerProvider(data),
        scheduling=InMemorySchedulingProvider(data),
        notification=InMemoryNotificationProvider(data),
    )
    providers = resolve_providers(ProviderConfig(), bundles={STEDDEN_INTERNAL: internal})
    ctx = ActionContext(
        call_id="sim-x", business_id=lumera_kb().business_id, kb=lumera_kb(), providers=providers
    )
    return ActionEngine(), ctx, data


async def test_unknown_action_is_skipped() -> None:
    engine, ctx, _ = _ctx()
    result = await engine.dispatch("delete_everything", {}, ctx)
    assert result["error"].startswith("unknown action")
    assert ctx.audit[-1].status == "skipped"


async def test_check_availability_returns_real_slots() -> None:
    engine, ctx, _ = _ctx()
    result = await engine.dispatch("check_availability", {"service": "Botox", "date": FRIDAY}, ctx)
    assert result["count"] > 0
    assert result["source"] == STEDDEN_INTERNAL
    assert "start" in result["slots"][0]


async def test_create_appointment_books_and_audits() -> None:
    engine, ctx, data = _ctx()
    result = await engine.dispatch(
        "create_appointment",
        {"name": "Ana", "phone": "305-555-0100", "service": "Botox", "start": f"{FRIDAY}T14:00:00"},
        ctx,
    )
    assert result["ok"] is True
    assert len(data.appointments) == 1
    assert ctx.audit[-1].action == "create_appointment"
    assert ctx.audit[-1].status == "success"


async def test_create_appointment_conflict_fails_gracefully() -> None:
    engine, ctx, data = _ctx()
    # Sunday: business closed -> no availability -> SchedulingConflict.
    result = await engine.dispatch(
        "create_appointment",
        {
            "name": "Ana",
            "phone": "305-555-0100",
            "service": "Botox",
            "start": "2026-07-26T14:00:00",
        },
        ctx,
    )
    assert result["ok"] is False
    assert result["reason"] == "unavailable"
    assert len(data.appointments) == 0
    assert ctx.audit[-1].status == "failed"


async def test_send_confirmation_is_idempotent_per_call() -> None:
    engine, ctx, _ = _ctx()
    first = await engine.dispatch(
        "send_confirmation", {"phone": "305-555-0100", "appointment_id": "A1"}, ctx
    )
    second = await engine.dispatch(
        "send_confirmation", {"phone": "305-555-0100", "appointment_id": "A1"}, ctx
    )
    assert first["ok"] is True and "already_sent" not in first
    assert second["already_sent"] is True
    assert ctx.audit[-1].status == "skipped"


async def test_callback_task_notifies_staff() -> None:
    engine, ctx, data = _ctx()
    result = await engine.dispatch(
        "create_callback_task",
        {"reason": "no slot fit", "name": "Ana", "phone": "305-555-0100"},
        ctx,
    )
    assert result["ok"] is True
    assert len(data.staff_notices) == 1
