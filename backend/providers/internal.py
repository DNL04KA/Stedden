"""Stedden-internal providers (Mode 2) — the lightweight front-desk system.

These are the source of truth when a business has no external CRM. This module
holds an in-memory implementation used by the simulator and tests; the SQL-backed
implementation (same interfaces, same availability function) follows in 4b. All
scheduling logic routes through the pure ``compute_slots`` function, so the two
implementations can never drift on availability.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from providers.availability import compute_slots
from providers.base import (
    Appointment,
    Customer,
    SchedulingConflict,
    Slot,
    StaffMember,
    Task,
    WorkingHours,
)


def _sid() -> str:
    return uuid.uuid4().hex[:12]


def _parse_dt(value: str, tz: ZoneInfo) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt.replace(tzinfo=tz) if dt.tzinfo is None else dt.astimezone(tz)


@dataclass
class InternalDataset:
    """The shared in-memory front-desk data for one business."""

    tz: ZoneInfo
    business_hours: list[WorkingHours]
    staff: list[StaffMember]
    service_durations: dict[str, int]  # lowercased service name -> minutes
    default_duration_min: int = 30
    customers: dict[str, Customer] = field(default_factory=dict)  # keyed by phone
    appointments: dict[str, Appointment] = field(default_factory=dict)  # keyed by id
    notes: dict[str, list[str]] = field(default_factory=dict)  # phone -> notes
    tasks: list[Task] = field(default_factory=list)
    staff_notices: list[tuple[str, str]] = field(default_factory=list)  # (priority, message)
    sent_confirmations: list[str] = field(default_factory=list)  # appointment ids / phones
    sent_sms: list[tuple[str, str]] = field(default_factory=list)

    def duration_for(self, service: str) -> timedelta:
        minutes = self.service_durations.get(service.strip().lower(), self.default_duration_min)
        return timedelta(minutes=minutes)

    def _is_free(self, staff: StaffMember, start: datetime, end: datetime) -> bool:
        weekday = start.date().weekday()
        within_staff = any(
            h.weekday == weekday
            and datetime.combine(start.date(), h.start, tzinfo=self.tz) <= start
            and end <= datetime.combine(start.date(), h.end, tzinfo=self.tz)
            for h in staff.hours
        )
        within_biz = any(
            h.weekday == weekday
            and datetime.combine(start.date(), h.start, tzinfo=self.tz) <= start
            and end <= datetime.combine(start.date(), h.end, tzinfo=self.tz)
            for h in self.business_hours
        )
        if not (within_staff and within_biz):
            return False
        return not any(
            a.staff_id == staff.id and a.status != "cancelled" and a.start < end and start < a.end
            for a in self.appointments.values()
        )


class InMemoryCustomerProvider:
    def __init__(self, data: InternalDataset) -> None:
        self._d = data

    async def find_customer_by_phone(self, phone: str) -> Customer | None:
        return self._d.customers.get(phone)

    async def create_customer(self, *, name: str, phone: str, email: str = "") -> Customer:
        existing = self._d.customers.get(phone)
        if existing is not None:
            return existing
        customer = Customer(id=_sid(), name=name, phone=phone, email=email)
        self._d.customers[phone] = customer
        return customer

    async def update_customer(
        self, *, phone: str, name: str | None = None, email: str | None = None
    ) -> Customer | None:
        customer = self._d.customers.get(phone)
        if customer is None:
            return None
        if name:
            customer.name = name
        if email:
            customer.email = email
        return customer

    async def add_customer_note(self, *, phone: str, note: str) -> bool:
        self._d.notes.setdefault(phone, []).append(note)
        return True

    async def get_customer_history(self, *, phone: str) -> list[Appointment]:
        return sorted(
            (a for a in self._d.appointments.values() if a.customer_phone == phone),
            key=lambda a: a.start,
        )


class InMemorySchedulingProvider:
    def __init__(self, data: InternalDataset) -> None:
        self._d = data

    async def check_availability(
        self, *, service: str, date: str, part_of_day: str | None = None
    ) -> list[Slot]:
        day = _date(date)
        slots = compute_slots(
            day=day,
            tz=self._d.tz,
            business_hours=self._d.business_hours,
            staff=self._d.staff,
            appointments=list(self._d.appointments.values()),
            duration=self._d.duration_for(service),
        )
        if part_of_day:
            slots = [s for s in slots if _matches_part_of_day(s.start, part_of_day)]
        return slots

    async def create_appointment(
        self, *, name: str, phone: str, service: str, start: str, staff_id: str | None = None
    ) -> Appointment:
        start_dt = _parse_dt(start, self._d.tz)
        end_dt = start_dt + self._d.duration_for(service)

        candidates = (
            [s for s in self._d.staff if s.id == staff_id] if staff_id else list(self._d.staff)
        )
        member = next((s for s in candidates if self._d._is_free(s, start_dt, end_dt)), None)
        if member is None:
            raise SchedulingConflict(f"No availability for {service} at {start}")

        self._d.customers.setdefault(phone, Customer(id=_sid(), name=name, phone=phone))
        appt = Appointment(
            id=_sid(),
            customer_phone=phone,
            customer_name=name,
            service=service,
            start=start_dt,
            end=end_dt,
            staff_id=member.id,
            staff_name=member.name,
            status="booked",
            confirmation_code=f"LUM-{_sid()[:6].upper()}",
        )
        self._d.appointments[appt.id] = appt
        return appt

    async def reschedule_appointment(
        self, *, appointment_id: str, new_start: str
    ) -> Appointment | None:
        appt = self._d.appointments.get(appointment_id)
        if appt is None or appt.status == "cancelled":
            return None
        new_start_dt = _parse_dt(new_start, self._d.tz)
        new_end_dt = new_start_dt + (appt.end - appt.start)
        member = next((s for s in self._d.staff if s.id == appt.staff_id), None)
        # Temporarily ignore this appointment when checking the new interval.
        saved = self._d.appointments.pop(appointment_id)
        free = member is not None and self._d._is_free(member, new_start_dt, new_end_dt)
        self._d.appointments[appointment_id] = saved
        if not free:
            raise SchedulingConflict(f"No availability at {new_start}")
        appt.start, appt.end, appt.status = new_start_dt, new_end_dt, "rescheduled"
        return appt

    async def cancel_appointment(self, *, appointment_id: str) -> bool:
        appt = self._d.appointments.get(appointment_id)
        if appt is None:
            return False
        appt.status = "cancelled"
        return True

    async def get_appointment(self, *, phone: str) -> list[Appointment]:
        return sorted(
            (
                a
                for a in self._d.appointments.values()
                if a.customer_phone == phone and a.status != "cancelled"
            ),
            key=lambda a: a.start,
        )


class InMemoryNotificationProvider:
    def __init__(self, data: InternalDataset) -> None:
        self._d = data

    async def notify_staff(self, *, message: str, priority: str = "medium") -> bool:
        self._d.staff_notices.append((priority, message))
        return True

    async def send_confirmation(self, *, phone, appointment, channel="email") -> bool:
        key = appointment.id if appointment is not None else phone
        self._d.sent_confirmations.append(key)
        return True

    async def send_sms(self, *, to: str, body: str) -> bool:
        # SMS is mocked until Twilio is wired (build order keeps telephony last).
        self._d.sent_sms.append((to, body))
        return True


def _date(value: str) -> date:
    return date.fromisoformat(value)


def _matches_part_of_day(dt: datetime, part: str) -> bool:
    hour = dt.hour
    part = part.strip().lower()
    if part == "morning":
        return hour < 12
    if part == "afternoon":
        return 12 <= hour < 17
    if part == "evening":
        return hour >= 17
    return True
