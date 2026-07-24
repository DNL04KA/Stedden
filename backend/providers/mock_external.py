"""A mock external provider (Mode 1) — stands in for Mangomint/Boulevard/etc.

It proves the hybrid model end-to-end: availability and records come from an
"external system", appointments carry an ``external_ref``, and — crucially — it
never writes to the Stedden-internal customer/appointment tables. Real adapters
implement the same interfaces; the Action Engine and Brain don't change.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from providers.base import Appointment, Customer, Slot


def _ext(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


@dataclass
class MockExternalSystem:
    """Canned 'external' data. Deterministic for tests."""

    tz: ZoneInfo
    customers: dict[str, Customer] = field(default_factory=dict)
    appointments: dict[str, Appointment] = field(default_factory=dict)
    #: Fixed daily slot times (HH, MM) the external system reports as open.
    open_times: tuple[tuple[int, int], ...] = ((10, 0), (11, 0), (14, 0), (15, 30))


class MockExternalProvider:
    """One object implementing all three provider interfaces for a fake CRM."""

    def __init__(self, system: MockExternalSystem) -> None:
        self._s = system

    # CustomerProvider ---------------------------------------------------------
    async def find_customer_by_phone(self, phone: str) -> Customer | None:
        return self._s.customers.get(phone)

    async def create_customer(self, *, name: str, phone: str, email: str = "") -> Customer:
        customer = self._s.customers.get(phone) or Customer(
            id=_ext("cust"), name=name, phone=phone, email=email, external_ref=_ext("ext_cust")
        )
        self._s.customers[phone] = customer
        return customer

    async def update_customer(self, *, phone, name=None, email=None) -> Customer | None:
        customer = self._s.customers.get(phone)
        if customer is None:
            return None
        if name:
            customer.name = name
        if email:
            customer.email = email
        return customer

    async def add_customer_note(self, *, phone: str, note: str) -> bool:
        return phone in self._s.customers

    async def get_customer_history(self, *, phone: str) -> list[Appointment]:
        return [a for a in self._s.appointments.values() if a.customer_phone == phone]

    # SchedulingProvider -------------------------------------------------------
    async def check_availability(self, *, service, date, part_of_day=None) -> list[Slot]:
        day = _date(date)
        slots = [
            Slot(
                start=datetime(day.year, day.month, day.day, h, m, tzinfo=self._s.tz),
                end=datetime(day.year, day.month, day.day, h, m, tzinfo=self._s.tz)
                + timedelta(minutes=30),
                staff_id="ext-any",
                staff_name="Next available",
            )
            for h, m in self._s.open_times
        ]
        # Exclude any already taken in the external system.
        taken = {a.start for a in self._s.appointments.values() if a.status != "cancelled"}
        return [s for s in slots if s.start not in taken]

    async def create_appointment(
        self, *, name, phone, service, start, staff_id=None
    ) -> Appointment:
        start_dt = datetime.fromisoformat(start)
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=self._s.tz)
        appt = Appointment(
            id=_ext("appt"),
            customer_phone=phone,
            customer_name=name,
            service=service,
            start=start_dt,
            end=start_dt + timedelta(minutes=30),
            staff_id="ext-any",
            staff_name="Next available",
            status="booked",
            external_ref=_ext("ext_appt"),
            confirmation_code=_ext("EXT").upper(),
        )
        self._s.appointments[appt.id] = appt
        return appt

    async def reschedule_appointment(self, *, appointment_id, new_start) -> Appointment | None:
        appt = self._s.appointments.get(appointment_id)
        if appt is None:
            return None
        new_start_dt = datetime.fromisoformat(new_start)
        if new_start_dt.tzinfo is None:
            new_start_dt = new_start_dt.replace(tzinfo=self._s.tz)
        appt.start, appt.end, appt.status = (
            new_start_dt,
            new_start_dt + timedelta(minutes=30),
            "rescheduled",
        )
        return appt

    async def cancel_appointment(self, *, appointment_id) -> bool:
        appt = self._s.appointments.get(appointment_id)
        if appt is None:
            return False
        appt.status = "cancelled"
        return True

    async def get_appointment(self, *, phone: str) -> list[Appointment]:
        return [
            a
            for a in self._s.appointments.values()
            if a.customer_phone == phone and a.status != "cancelled"
        ]

    # NotificationProvider -----------------------------------------------------
    async def notify_staff(self, *, message: str, priority: str = "medium") -> bool:
        return True

    async def send_confirmation(self, *, phone, appointment, channel="email") -> bool:
        return True

    async def send_sms(self, *, to: str, body: str) -> bool:
        return True


def _date(value: str) -> date:
    return date.fromisoformat(value)
