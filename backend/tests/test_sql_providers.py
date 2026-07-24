"""SQL providers against a real PostgreSQL (stedden_test database).

These tests prove the 4b Definition of Done end-to-end:
- customers are created and deduplicated by phone,
- availability comes from seeded hours in Postgres,
- appointments persist, reschedule, and cancel,
- double-booking is rejected by business logic AND by the database EXCLUDE
  constraint (race-safe),
- a full scripted conversation writes customer, appointment, and action_log rows.

Skipped automatically when PostgreSQL is not reachable.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.schemas import ConversationState as S
from core.schemas import Lead, TurnResult
from db.models import ActionLog, AppointmentRow, TaskRow
from db.models import Customer as CustomerRow
from db.repository import create_call, save_action_records
from engine.engine import ActionEngine
from providers.base import SchedulingConflict
from providers.registry import STEDDEN_INTERNAL, ProviderBundle, resolve_providers
from providers.sql_internal import (
    SqlCustomerProvider,
    SqlNotificationProvider,
    SqlSchedulingProvider,
)
from seed.lumera import LUMERA_BUSINESS_ID, lumera_kb, lumera_provider_config
from seed.seed_db import ensure_seeded
from simulator.session import ConversationSession
from tests.fakes import ScriptedGenerator, ScriptedTurn, ToolCall

TEST_DB = "stedden_test"
TEST_DB_URL = f"postgresql+psycopg://localhost:5432/{TEST_DB}"
TZ = ZoneInfo("America/New_York")

_pg_up = subprocess.run(["pg_isready", "-q"], check=False).returncode == 0
pytestmark = pytest.mark.skipif(not _pg_up, reason="PostgreSQL is not running")


def next_weekday(target: int) -> date:
    """Next occurrence of weekday `target` (Mon=0), at least tomorrow."""
    today = date.today()
    delta = (target - today.weekday()) % 7
    return today + timedelta(days=delta or 7)


FRIDAY = next_weekday(4)
SUNDAY = next_weekday(6)


@pytest.fixture(scope="session", autouse=True)
def _migrated_test_db() -> None:
    subprocess.run(["createdb", TEST_DB], check=False, capture_output=True)
    env = {**os.environ, "DATABASE_URL": TEST_DB_URL}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"alembic upgrade failed:\n{result.stderr}"


@pytest.fixture
async def sm():
    """Fresh engine per test (per event loop), truncated and re-seeded schema."""
    engine = create_async_engine(TEST_DB_URL)
    maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE action_log, task, appointment, customer_note, customer, "
                "staff_hours, staff, business_hours, notification, lead, "
                "transcript_message, call, policy, faq, service, business CASCADE"
            )
        )
    await ensure_seeded(maker)
    yield maker
    await engine.dispose()


def providers_for(sm):
    bundle = ProviderBundle(
        customer=SqlCustomerProvider(sm, LUMERA_BUSINESS_ID, TZ),
        scheduling=SqlSchedulingProvider(sm, LUMERA_BUSINESS_ID, TZ),
        notification=SqlNotificationProvider(sm, LUMERA_BUSINESS_ID, TZ),
    )
    return resolve_providers(lumera_provider_config(), bundles={STEDDEN_INTERNAL: bundle})


# ── Availability from seeded hours ───────────────────────────────────────────
async def test_availability_from_seeded_hours(sm) -> None:
    scheduling = providers_for(sm).scheduling
    friday_slots = await scheduling.check_availability(service="Botox", date=FRIDAY.isoformat())
    sunday_slots = await scheduling.check_availability(service="Botox", date=SUNDAY.isoformat())

    assert friday_slots, "expected bookable Friday slots from seeded hours"
    assert all(9 <= s.start.hour < 17 for s in friday_slots)  # Fri: biz 9–17
    assert sunday_slots == []  # closed


# ── Booking persists customer + appointment; dedup by phone ──────────────────
async def test_booking_persists_and_dedupes_customer(sm) -> None:
    scheduling = providers_for(sm).scheduling
    a1 = await scheduling.create_appointment(
        name="Ana", phone="305-555-0100", service="Botox", start=f"{FRIDAY}T10:00:00"
    )
    await scheduling.create_appointment(
        name="Ana", phone="305-555-0100", service="HydraFacial", start=f"{FRIDAY}T11:00:00"
    )
    assert a1.confirmation_code and a1.status == "booked"

    async with sm() as session:
        customers = (await session.scalars(select(CustomerRow))).all()
        appointments = (await session.scalars(select(AppointmentRow))).all()
    assert len(customers) == 1  # same phone → one customer row
    assert customers[0].name == "Ana"
    assert len(appointments) == 2
    assert {a.service for a in appointments} == {"Botox", "HydraFacial"}
    # HydraFacial is seeded at 60 min; Botox at 30.
    durations = {a.service: (a.end_at - a.start_at) for a in appointments}
    assert durations["Botox"] == timedelta(minutes=30)
    assert durations["HydraFacial"] == timedelta(minutes=60)


# ── Double-booking: business logic ───────────────────────────────────────────
async def test_double_booking_rejected_by_business_logic(sm) -> None:
    scheduling = providers_for(sm).scheduling
    start = f"{FRIDAY}T14:00:00"
    first = await scheduling.create_appointment(
        name="Ana", phone="305-555-0100", service="Botox", start=start
    )
    # Force the same staff member: with staff pinned, the identical slot must fail.
    with pytest.raises(SchedulingConflict):
        await scheduling.create_appointment(
            name="Bea",
            phone="305-555-0111",
            service="Botox",
            start=start,
            staff_id=first.staff_id,
        )


# ── Double-booking: database EXCLUDE constraint (race safety) ────────────────
async def test_double_booking_rejected_by_db_constraint(sm) -> None:
    scheduling = providers_for(sm).scheduling
    booked = await scheduling.create_appointment(
        name="Ana", phone="305-555-0100", service="Botox", start=f"{FRIDAY}T14:00:00"
    )
    # Bypass business logic entirely and insert an overlapping row directly:
    # the ex_appointment_no_overlap constraint must reject it.
    async with sm() as session:
        customer = (await session.scalars(select(CustomerRow))).one()
        overlap = AppointmentRow(
            business_id=uuid.UUID(LUMERA_BUSINESS_ID),
            customer_id=customer.id,
            staff_id=uuid.UUID(booked.staff_id),
            service="Botox",
            start_at=datetime.combine(FRIDAY, datetime.min.time(), tzinfo=TZ).replace(
                hour=14, minute=15
            ),
            end_at=datetime.combine(FRIDAY, datetime.min.time(), tzinfo=TZ).replace(
                hour=14, minute=45
            ),
            status="booked",
        )
        session.add(overlap)
        with pytest.raises(IntegrityError):
            await session.commit()


# ── Reschedule and cancel persist ────────────────────────────────────────────
async def test_reschedule_and_cancel_persist(sm) -> None:
    scheduling = providers_for(sm).scheduling
    appt = await scheduling.create_appointment(
        name="Ana", phone="305-555-0100", service="Botox", start=f"{FRIDAY}T14:00:00"
    )
    moved = await scheduling.reschedule_appointment(
        appointment_id=appt.id, new_start=f"{FRIDAY}T15:00:00"
    )
    assert moved is not None and moved.start.hour == 15 and moved.status == "rescheduled"

    # The old 14:00 slot is free again for the same staff member.
    retaken = await scheduling.create_appointment(
        name="Bea",
        phone="305-555-0111",
        service="Botox",
        start=f"{FRIDAY}T14:00:00",
        staff_id=appt.staff_id,
    )
    assert retaken.status == "booked"

    assert await scheduling.cancel_appointment(appointment_id=appt.id) is True
    async with sm() as session:
        row = await session.get(AppointmentRow, uuid.UUID(appt.id))
        assert row is not None and row.status == "cancelled"


# ── Full scripted conversation → customer + appointment + action_log ─────────
async def test_scripted_booking_flow_writes_all_rows(sm) -> None:
    """The reproducible 4b scenario: a conversation books a real appointment and
    every business action lands in action_log, tied to a persisted call."""
    providers = providers_for(sm)
    kb = lumera_kb()
    start = f"{FRIDAY}T14:00:00"

    turns = [
        ScriptedTurn(
            final=TurnResult(
                say="Certainly. May I have your name and number?",
                state=S.LEAD_CAPTURE,
                lead=Lead(service="Botox", intent="booking"),
                grounded=True,
            ),
            tool_calls=[],
        ),
        ScriptedTurn(
            final=TurnResult(
                say="One moment while I check availability…",
                state=S.LEAD_CAPTURE,
                lead=Lead(name="Ana", phone="305-555-0100", service="Botox", intent="booking"),
                grounded=True,
            ),
            tool_calls=[
                ToolCall("check_availability", {"service": "Botox", "date": FRIDAY.isoformat()})
            ],
        ),
        ScriptedTurn(
            final=TurnResult(
                say="You're booked for Friday. Anything else?",
                state=S.GOODBYE,
                lead=Lead(name="Ana", phone="305-555-0100", service="Botox", intent="booking"),
                grounded=True,
                end_call=True,
            ),
            tool_calls=[
                ToolCall(
                    "create_appointment",
                    {"name": "Ana", "phone": "305-555-0100", "service": "Botox", "start": start},
                ),
                ToolCall(
                    "notify_staff", {"message": "New booking: Ana, Botox", "priority": "medium"}
                ),
            ],
        ),
    ]

    call_id = await create_call(sm, business_id=kb.business_id)
    session = ConversationSession(
        kb, ScriptedGenerator(turns), ActionEngine(), providers, call_id=str(call_id)
    )
    seq = 0
    for line in ["I'd like Botox on Friday", "Ana, 305-555-0100", "Yes, book it"]:
        view = await session.send(line)
        seq = await save_action_records(
            sm, business_id=kb.business_id, call_id=call_id, records=view.actions, start_seq=seq
        )

    async with sm() as db:
        customers = (await db.scalars(select(CustomerRow))).all()
        appointments = (await db.scalars(select(AppointmentRow))).all()
        logs = (await db.scalars(select(ActionLog).order_by(ActionLog.seq))).all()
        tasks = (await db.scalars(select(TaskRow))).all()

    assert len(customers) == 1 and customers[0].phone == "305-555-0100"
    assert len(appointments) == 1 and appointments[0].status == "booked"
    assert appointments[0].start_at.astimezone(TZ).hour == 14

    actions_logged = [(log.action, log.status) for log in logs]
    assert ("check_availability", "success") in actions_logged
    assert ("create_appointment", "success") in actions_logged
    assert ("notify_staff", "success") in actions_logged
    assert all(log.call_id == call_id for log in logs)
    assert len(tasks) == 1  # notify_staff became a persistent front-desk task

    # Re-running the same booking must NOT create a second appointment.
    with pytest.raises(SchedulingConflict):
        await providers.scheduling.create_appointment(
            name="Ana",
            phone="305-555-0100",
            service="Botox",
            start=start,
            staff_id=appointments[0].staff_id.__str__(),
        )
