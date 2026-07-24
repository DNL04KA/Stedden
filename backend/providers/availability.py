"""Pure availability computation — deliberately small.

    slots = Business Hours ∩ Staff Working Hours − Existing Appointments

Timezone-aware, per-staff, service-duration-based. No recurring appointments,
rooms, resources, packages, memberships, waitlists, or optimization — by design.

This function has no I/O; both the in-memory and (later) SQL scheduling providers
call it, and it's unit-tested directly.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from providers.base import Appointment, Slot, StaffMember, WorkingHours

DEFAULT_GRANULARITY = timedelta(minutes=15)


def _windows_for_weekday(hours: list[WorkingHours], weekday: int) -> list[tuple[time, time]]:
    return [(h.start, h.end) for h in hours if h.weekday == weekday]


def _overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return a_start < b_end and b_start < a_end


def compute_slots(
    *,
    day: date,
    tz: ZoneInfo,
    business_hours: list[WorkingHours],
    staff: list[StaffMember],
    appointments: list[Appointment],
    duration: timedelta,
    granularity: timedelta = DEFAULT_GRANULARITY,
    now: datetime | None = None,
) -> list[Slot]:
    """Return bookable slots for ``day``. Slots in the past (relative to ``now``)
    are excluded. Results are sorted by start time then staff name."""
    weekday = day.weekday()
    biz_windows = _windows_for_weekday(business_hours, weekday)
    if not biz_windows:
        return []  # business closed this day

    active_appts = [a for a in appointments if a.status != "cancelled"]
    slots: list[Slot] = []

    for member in staff:
        for staff_start, staff_end in _windows_for_weekday(member.hours, weekday):
            for biz_start, biz_end in biz_windows:
                # Intersect staff window with a business window.
                win_start = max(staff_start, biz_start)
                win_end = min(staff_end, biz_end)
                if win_start >= win_end:
                    continue

                cursor = datetime.combine(day, win_start, tzinfo=tz)
                window_end = datetime.combine(day, win_end, tzinfo=tz)
                while cursor + duration <= window_end:
                    slot_start, slot_end = cursor, cursor + duration
                    in_past = now is not None and slot_start < now
                    conflict = any(
                        a.staff_id == member.id and _overlaps(slot_start, slot_end, a.start, a.end)
                        for a in active_appts
                    )
                    if not in_past and not conflict:
                        slots.append(
                            Slot(
                                start=slot_start,
                                end=slot_end,
                                staff_id=member.id,
                                staff_name=member.name,
                            )
                        )
                    cursor += granularity

    slots.sort(key=lambda s: (s.start, s.staff_name))
    return slots
