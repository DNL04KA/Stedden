"""The pure availability engine: Business Hours ∩ Staff Hours − Existing."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from providers.availability import compute_slots
from providers.base import Appointment, StaffMember, WorkingHours

TZ = ZoneInfo("America/New_York")

# 2026-07-24 is a Friday; 2026-07-26 is a Sunday.
FRIDAY = date(2026, 7, 24)
SUNDAY = date(2026, 7, 26)

BIZ = [WorkingHours(4, time(9, 0), time(17, 0))]  # Fri 9–17
STAFF = [StaffMember(id="s1", name="Sofia", hours=[WorkingHours(4, time(9, 0), time(12, 0))])]


def _appt(staff_id: str, hour: int, minute: int = 0, dur: int = 30) -> Appointment:
    start = datetime(2026, 7, 24, hour, minute, tzinfo=TZ)
    return Appointment(
        id="a",
        customer_phone="p",
        customer_name="n",
        service="Botox",
        start=start,
        end=start + timedelta(minutes=dur),
        staff_id=staff_id,
        staff_name="Sofia",
    )


def test_closed_day_returns_no_slots() -> None:
    slots = compute_slots(
        day=SUNDAY,
        tz=TZ,
        business_hours=BIZ,
        staff=STAFF,
        appointments=[],
        duration=timedelta(minutes=30),
    )
    assert slots == []


def test_slots_bounded_by_staff_window() -> None:
    # Staff work 9–12; with 30-min slots and 15-min granularity the last start is 11:30.
    slots = compute_slots(
        day=FRIDAY,
        tz=TZ,
        business_hours=BIZ,
        staff=STAFF,
        appointments=[],
        duration=timedelta(minutes=30),
    )
    assert slots[0].start == datetime(2026, 7, 24, 9, 0, tzinfo=TZ)
    assert slots[-1].start == datetime(2026, 7, 24, 11, 30, tzinfo=TZ)


def test_existing_appointment_blocks_overlapping_slots() -> None:
    slots = compute_slots(
        day=FRIDAY,
        tz=TZ,
        business_hours=BIZ,
        staff=STAFF,
        appointments=[_appt("s1", 10, 0)],
        duration=timedelta(minutes=30),
    )
    starts = {s.start.hour * 60 + s.start.minute for s in slots}
    # 9:45, 10:00, 10:15 all overlap a 10:00–10:30 appointment and must be gone.
    assert 10 * 60 not in starts
    assert 9 * 60 + 45 not in starts
    assert 10 * 60 + 30 in starts  # butts up against the end — allowed


def test_cancelled_appointment_does_not_block() -> None:
    appt = _appt("s1", 10, 0)
    appt.status = "cancelled"
    slots = compute_slots(
        day=FRIDAY,
        tz=TZ,
        business_hours=BIZ,
        staff=STAFF,
        appointments=[appt],
        duration=timedelta(minutes=30),
    )
    assert any(s.start == datetime(2026, 7, 24, 10, 0, tzinfo=TZ) for s in slots)


def test_past_slots_excluded() -> None:
    now = datetime(2026, 7, 24, 10, 30, tzinfo=TZ)
    slots = compute_slots(
        day=FRIDAY,
        tz=TZ,
        business_hours=BIZ,
        staff=STAFF,
        appointments=[],
        duration=timedelta(minutes=30),
        now=now,
    )
    assert all(s.start >= now for s in slots)
