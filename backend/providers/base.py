"""Provider interfaces and the domain models that cross them.

These three interfaces are the contract between the Business Action Engine and
whatever system is the source of truth for a given business — Stedden-internal or
an external CRM/booking platform. The Brain never touches these; the Action Engine
resolves the configured provider per business and calls it.

Adding Mangomint / Boulevard / Zenoti / Google Calendar later means implementing
these interfaces in a new adapter. Nothing else changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Protocol


# ── Domain models (provider-neutral) ─────────────────────────────────────────
@dataclass(slots=True)
class Customer:
    id: str
    name: str
    phone: str
    email: str = ""
    external_ref: str | None = None


@dataclass(slots=True)
class Slot:
    start: datetime  # timezone-aware
    end: datetime
    staff_id: str
    staff_name: str


@dataclass(slots=True)
class Appointment:
    id: str
    customer_phone: str
    customer_name: str
    service: str
    start: datetime
    end: datetime
    staff_id: str
    staff_name: str
    status: str = "booked"  # booked | rescheduled | cancelled
    external_ref: str | None = None
    confirmation_code: str | None = None


@dataclass(slots=True)
class Task:
    id: str
    reason: str
    details: str = ""
    phone: str = ""
    name: str = ""
    status: str = "open"


@dataclass(slots=True)
class WorkingHours:
    weekday: int  # 0=Monday … 6=Sunday
    start: time
    end: time


@dataclass(slots=True)
class StaffMember:
    id: str
    name: str
    hours: list[WorkingHours] = field(default_factory=list)


# ── Provider interfaces ──────────────────────────────────────────────────────
class CustomerProvider(Protocol):
    async def find_customer_by_phone(self, phone: str) -> Customer | None: ...
    async def create_customer(self, *, name: str, phone: str, email: str = "") -> Customer: ...
    async def update_customer(
        self, *, phone: str, name: str | None = None, email: str | None = None
    ) -> Customer | None: ...
    async def add_customer_note(self, *, phone: str, note: str) -> bool: ...
    async def get_customer_history(self, *, phone: str) -> list[Appointment]: ...


class SchedulingProvider(Protocol):
    async def check_availability(
        self, *, service: str, date: str, part_of_day: str | None = None
    ) -> list[Slot]: ...
    async def create_appointment(
        self, *, name: str, phone: str, service: str, start: str, staff_id: str | None = None
    ) -> Appointment: ...
    async def reschedule_appointment(
        self, *, appointment_id: str, new_start: str
    ) -> Appointment | None: ...
    async def cancel_appointment(self, *, appointment_id: str) -> bool: ...
    async def get_appointment(self, *, phone: str) -> list[Appointment]: ...


class NotificationProvider(Protocol):
    async def notify_staff(self, *, message: str, priority: str = "medium") -> bool: ...
    async def send_confirmation(
        self, *, phone: str, appointment: Appointment | None, channel: str = "email"
    ) -> bool: ...
    async def send_sms(self, *, to: str, body: str) -> bool: ...


class SchedulingConflict(Exception):
    """Raised when a requested slot is no longer available."""


class ProviderNotConfigured(Exception):
    """Raised when a business names a provider we don't have an adapter for."""
