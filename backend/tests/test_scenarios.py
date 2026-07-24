"""Regression scenarios — the required ten plus the new front-desk operations.

All run offline (no telephony, no network). The scripted generator issues real
tool calls through the Action Engine, so providers, availability, booking, and
idempotency are genuinely exercised; only the model's wording is scripted.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from core.actions import ActionName
from core.schemas import ConversationState as S
from core.schemas import Lead, Priority, TurnResult
from providers.base import Appointment
from seed.lumera import LUMERA_TZ, lumera_internal_dataset
from tests.fakes import ScriptedTurn, ToolCall
from tests.harness import make_external_session, make_internal_session

FRIDAY = "2026-07-24"  # a Friday
SUNDAY = "2026-07-26"  # closed


def turn(say, state, *, lead=None, grounded=True, end_call=False, tools=None) -> ScriptedTurn:
    return ScriptedTurn(
        final=TurnResult(
            say=say, state=state, lead=lead or Lead(), grounded=grounded, end_call=end_call
        ),
        tool_calls=tools or [],
    )


def _seed_appointment(data, *, appt_id="APT1", phone="305-555-0100", hour=14) -> Appointment:
    start = datetime(2026, 7, 24, hour, 0, tzinfo=LUMERA_TZ)
    appt = Appointment(
        id=appt_id,
        customer_phone=phone,
        customer_name="Ana",
        service="Botox",
        start=start,
        end=start + timedelta(minutes=30),
        staff_id="staff-sofia",
        staff_name="Nurse Sofia",
        status="booked",
    )
    data.appointments[appt_id] = appt
    return appt


# 1 ── Asking business hours ────────────────────────────────────────────────
async def test_business_hours() -> None:
    session, _ = make_internal_session([turn("We're open Mon–Fri, and Saturday morning.", S.FAQ)])
    view = await session.send("What are your hours?")
    assert view.state is S.FAQ and view.result.grounded is True
    assert view.actions == []


# 2 ── Known service price ──────────────────────────────────────────────────
async def test_known_price() -> None:
    session, _ = make_internal_session([turn("Botox is $12 per unit.", S.FAQ)])
    view = await session.send("How much is Botox?")
    assert view.result.grounded is True and "12" in view.result.say


# 3 ── Unknown price → escalate + capture ───────────────────────────────────
async def test_unknown_price_escalates() -> None:
    session, _ = make_internal_session(
        [
            turn(
                "I'll have someone follow up with exact pricing.",
                S.ESCALATION,
                lead=Lead(phone="305-555-0100"),
                grounded=False,
            )
        ]
    )
    view = await session.send("How much is Morpheus8 exactly?")
    assert view.result.grounded is False and view.lead.needs_human is True
    assert any(a.action == ActionName.CREATE_LEAD.value for a in view.actions)


# 4 ── Booking a Botox consultation (checks real availability, then books) ───
async def test_botox_booking_flow() -> None:
    session, data = make_internal_session(
        [
            turn(
                "Certainly. Before I check availability, may I have your name and number?",
                S.LEAD_CAPTURE,
                lead=Lead(service="Botox", intent="booking"),
            ),
            turn(
                "One moment… I have 2:00 PM Friday with Nurse Sofia. Shall I book it?",
                S.LEAD_CAPTURE,
                lead=Lead(name="Ana", phone="305-555-0100", service="Botox", intent="booking"),
                tools=[ToolCall("check_availability", {"service": "Botox", "date": FRIDAY})],
            ),
            turn(
                "All set for Friday at 2:00 PM with Sofia — confirmation sent. Anything else?",
                S.GOODBYE,
                end_call=True,
                lead=Lead(
                    name="Ana",
                    phone="305-555-0100",
                    service="Botox",
                    intent="booking",
                    preferred_time="Fri 2pm",
                    priority=Priority.HIGH,
                ),
                tools=[
                    ToolCall(
                        "create_appointment",
                        {
                            "name": "Ana",
                            "phone": "305-555-0100",
                            "service": "Botox",
                            "start": f"{FRIDAY}T14:00:00",
                        },
                    ),
                    ToolCall("send_confirmation", {"phone": "305-555-0100"}),
                ],
            ),
        ]
    )
    await session.send("I'd like Botox this Friday")
    view2 = await session.send("Ana, 305-555-0100")
    view3 = await session.send("Yes, please book it")

    # Availability was really queried and returned slots.
    avail = next(a for a in view2.actions if a.action == "check_availability")
    assert avail.result["count"] > 0
    # A real appointment now exists at the booked time.
    assert len(data.appointments) == 1
    booked = next(iter(data.appointments.values()))
    assert booked.start == datetime(2026, 7, 24, 14, 0, tzinfo=LUMERA_TZ)
    assert booked.status == "booked"
    assert view3.result.should_end_call is True


# 5 ── Medical advice is refused, not answered ──────────────────────────────
async def test_medical_advice_refused() -> None:
    session, _ = make_internal_session(
        [
            turn(
                "I can't advise on that, but our medical team will follow up.",
                S.ESCALATION,
                lead=Lead(needs_human=True),
                grounded=False,
            )
        ]
    )
    view = await session.send("Is Botox safe with my heart medication?")
    assert view.result.grounded is False and view.lead.needs_human is True


# 6 ── Insurance is escalated ───────────────────────────────────────────────
async def test_insurance_escalated() -> None:
    session, _ = make_internal_session(
        [turn("Our team can follow up about that.", S.ESCALATION, grounded=False)]
    )
    view = await session.send("Do you take my insurance?")
    assert view.result.grounded is False and view.lead.needs_human is True


# 7 ── Complaint → high-priority escalation + staff notified ────────────────
async def test_complaint_high_priority() -> None:
    session, data = make_internal_session(
        [
            turn(
                "I'm so sorry — I'll have a manager reach out today.",
                S.ESCALATION,
                lead=Lead(
                    name="Bea",
                    phone="305-555-0111",
                    intent="complaint",
                    needs_human=True,
                    priority=Priority.HIGH,
                ),
                tools=[
                    ToolCall("notify_staff", {"message": "Complaint from Bea", "priority": "high"})
                ],
            )
        ]
    )
    view = await session.send("My last Botox was terrible and I'm upset")
    assert view.lead.priority is Priority.HIGH and view.lead.needs_human is True
    assert len(data.staff_notices) == 1


# 8 ── Partial lead details accumulate across turns ─────────────────────────
async def test_partial_details_accumulate() -> None:
    session, _ = make_internal_session(
        [
            turn("Thanks! And your phone number?", S.LEAD_CAPTURE, lead=Lead(name="Ana")),
            turn("Perfect.", S.LEAD_CAPTURE, lead=Lead(phone="305-555-0100", intent="Botox")),
        ]
    )
    await session.send("My name is Ana")
    view = await session.send("My number is 305-555-0100")
    assert view.lead.name == "Ana" and view.lead.phone == "305-555-0100"


# 9 ── Correcting a phone number ────────────────────────────────────────────
async def test_phone_correction() -> None:
    session, _ = make_internal_session(
        [
            turn(
                "Got it, 305-111-1111.", S.LEAD_CAPTURE, lead=Lead(name="Ana", phone="305-111-1111")
            ),
            turn("Updated to 305-222-2222.", S.LEAD_CAPTURE, lead=Lead(phone="305-222-2222")),
        ]
    )
    await session.send("It's Ana, 305-111-1111")
    view = await session.send("Actually it's 305-222-2222")
    assert view.lead.phone == "305-222-2222" and view.lead.name == "Ana"


# 10 ── Ending without completing a lead ────────────────────────────────────
async def test_end_without_completed_lead() -> None:
    session, data = make_internal_session(
        [turn("Thanks for calling — have a great day!", S.GOODBYE, end_call=True)]
    )
    view = await session.send("Just checking your hours, thanks, bye")
    assert view.result.should_end_call is True
    assert len(data.appointments) == 0
    assert not any(a.action == ActionName.CREATE_LEAD.value for a in view.actions)


# 11 ── Rescheduling an existing appointment ────────────────────────────────
async def test_reschedule_existing_appointment() -> None:
    data = lumera_internal_dataset()
    _seed_appointment(data, hour=14)
    session, data = make_internal_session(
        [
            turn(
                "Done — moved to 3:00 PM.",
                S.GOODBYE,
                end_call=True,
                tools=[
                    ToolCall(
                        "reschedule_appointment",
                        {"appointment_id": "APT1", "new_start": f"{FRIDAY}T15:00:00"},
                    )
                ],
            )
        ],
        data=data,
    )
    await session.send("Can you move my Friday appointment to 3pm?")
    assert data.appointments["APT1"].start.hour == 15
    assert data.appointments["APT1"].status == "rescheduled"


# 12 ── Cancelling an appointment ───────────────────────────────────────────
async def test_cancel_appointment() -> None:
    session, data = make_internal_session(
        [
            turn(
                "Your appointment is cancelled.",
                S.GOODBYE,
                end_call=True,
                tools=[ToolCall("cancel_appointment", {"appointment_id": "APT1"})],
            )
        ]
    )
    _seed_appointment(data)
    await session.send("Please cancel my Friday appointment")
    assert data.appointments["APT1"].status == "cancelled"


# 13 ── Recognizing a returning customer ────────────────────────────────────
async def test_returning_customer_lookup() -> None:
    session, data = make_internal_session(
        [
            turn(
                "Welcome back, Ana!",
                S.INTENT_DISCOVERY,
                tools=[ToolCall("find_customer_by_phone", {"phone": "305-555-0100"})],
            )
        ]
    )
    from providers.base import Customer

    data.customers["305-555-0100"] = Customer(id="c1", name="Ana", phone="305-555-0100")
    view = await session.send("Hi, it's Ana")
    lookup = next(a for a in view.actions if a.action == "find_customer_by_phone")
    assert lookup.result["found"] is True


# 14 ── External CRM mode (Mode 1) ──────────────────────────────────────────
async def test_external_mode_books_in_external_system() -> None:
    session, system = make_external_session(
        [
            turn(
                "One moment… I have a few times available.",
                S.LEAD_CAPTURE,
                tools=[ToolCall("check_availability", {"service": "Botox", "date": FRIDAY})],
            ),
            turn(
                "Booked with the next available provider — confirmation sent.",
                S.GOODBYE,
                end_call=True,
                tools=[
                    ToolCall(
                        "create_appointment",
                        {
                            "name": "Ana",
                            "phone": "305-555-0100",
                            "service": "Botox",
                            "start": f"{FRIDAY}T14:00:00",
                        },
                    )
                ],
            ),
        ]
    )
    view1 = await session.send("I'd like to book Botox Friday")
    view2 = await session.send("Yes, book it")
    avail = next(a for a in view1.actions if a.action == "check_availability")
    assert avail.result["source"] == "external"
    create = next(a for a in view2.actions if a.action == "create_appointment")
    assert create.provider == "mock_external"
    assert create.external_ref is not None
    assert len(system.appointments) == 1


# 15 ── Booking conflict falls back to a callback task ──────────────────────
async def test_booking_conflict_creates_callback() -> None:
    session, data = make_internal_session(
        [
            turn(
                "I couldn't book that slot, so I've asked our team to call you back.",
                S.ESCALATION,
                lead=Lead(name="Ana", phone="305-555-0100", needs_human=True),
                tools=[
                    ToolCall(
                        "create_appointment",
                        {
                            "name": "Ana",
                            "phone": "305-555-0100",
                            "service": "Botox",
                            "start": f"{SUNDAY}T14:00:00",
                        },
                    ),  # Sunday: closed
                    ToolCall(
                        "create_callback_task",
                        {"reason": "no availability", "name": "Ana", "phone": "305-555-0100"},
                    ),
                ],
            )
        ]
    )
    view = await session.send("Book me Sunday at 2")
    assert len(data.appointments) == 0
    create = next(a for a in view.actions if a.action == "create_appointment")
    assert create.status == "failed"
    assert any(a.action == ActionName.CREATE_CALLBACK_TASK.value for a in view.actions)
    assert len(data.staff_notices) == 1  # callback notified staff
