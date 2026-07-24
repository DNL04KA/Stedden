"""SQL-backed Stedden-internal providers (Mode 2) — Postgres is the source of truth.

Same interfaces and the same pure ``compute_slots`` as the in-memory versions, so
availability can never drift between them. Double-booking is prevented twice:

1. Business logic: the requested window is validated against hours and existing
   appointments inside the booking transaction.
2. Database: the ``ex_appointment_no_overlap`` EXCLUDE constraint (migration
   0002) rejects concurrent overlapping inserts; we surface that as
   ``SchedulingConflict`` so the Brain can offer another slot.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from db.models import (
    AppointmentRow,
    BusinessHoursRow,
    CustomerNote,
    TaskRow,
)
from db.models import (
    Customer as CustomerRow,
)
from db.models import (
    Service as ServiceRow,
)
from db.models import (
    Staff as StaffRow,
)
from providers.availability import compute_slots
from providers.base import (
    Appointment,
    Customer,
    SchedulingConflict,
    Slot,
    StaffMember,
    WorkingHours,
)

log = structlog.get_logger("stedden.providers.sql")

_ACTIVE = ("booked", "rescheduled")


def _code() -> str:
    return f"LUM-{uuid.uuid4().hex[:6].upper()}"


class SqlProviderBase:
    def __init__(
        self, sm: async_sessionmaker[AsyncSession], business_id: str, tz: ZoneInfo
    ) -> None:
        self._sm = sm
        self._business_id = uuid.UUID(business_id)
        self._tz = tz

    def _local(self, dt: datetime) -> datetime:
        """Normalize a DB timestamp to business-local time."""
        return dt.astimezone(self._tz)

    def _parse(self, value: str) -> datetime:
        dt = datetime.fromisoformat(value)
        return dt.replace(tzinfo=self._tz) if dt.tzinfo is None else dt.astimezone(self._tz)

    def _appt(self, row: AppointmentRow, staff_name: str, phone: str, name: str) -> Appointment:
        return Appointment(
            id=str(row.id),
            customer_phone=phone,
            customer_name=name,
            service=row.service,
            start=self._local(row.start_at),
            end=self._local(row.end_at),
            staff_id=str(row.staff_id),
            staff_name=staff_name,
            status=row.status,
            external_ref=row.external_ref,
            confirmation_code=row.confirmation_code or None,
        )


class SqlCustomerProvider(SqlProviderBase):
    async def find_customer_by_phone(self, phone: str) -> Customer | None:
        async with self._sm() as session:
            row = await self._by_phone(session, phone)
            if row is None:
                return None
            return Customer(
                id=str(row.id),
                name=row.name,
                phone=row.phone,
                email=row.email,
                external_ref=row.external_ref,
            )

    async def create_customer(self, *, name: str, phone: str, email: str = "") -> Customer:
        try:
            async with self._sm() as session, session.begin():
                row = await self._by_phone(session, phone)
                if row is None:
                    row = CustomerRow(
                        business_id=self._business_id, name=name, phone=phone, email=email
                    )
                    session.add(row)
                    await session.flush()
                return Customer(
                    id=str(row.id),
                    name=row.name,
                    phone=row.phone,
                    email=row.email,
                    external_ref=row.external_ref,
                )
        except IntegrityError:
            # Concurrent create for the same phone lost the race — the unique
            # (business_id, phone) constraint guarantees the row now exists.
            async with self._sm() as session:
                row = await self._by_phone(session, phone)
                assert row is not None
                return Customer(
                    id=str(row.id),
                    name=row.name,
                    phone=row.phone,
                    email=row.email,
                    external_ref=row.external_ref,
                )

    async def update_customer(
        self, *, phone: str, name: str | None = None, email: str | None = None
    ) -> Customer | None:
        async with self._sm() as session, session.begin():
            row = await self._by_phone(session, phone)
            if row is None:
                return None
            if name:
                row.name = name
            if email:
                row.email = email
            return Customer(
                id=str(row.id),
                name=row.name,
                phone=row.phone,
                email=row.email,
                external_ref=row.external_ref,
            )

    async def add_customer_note(self, *, phone: str, note: str) -> bool:
        async with self._sm() as session, session.begin():
            row = await self._by_phone(session, phone)
            if row is None:
                return False
            session.add(CustomerNote(customer_id=row.id, note=note))
            return True

    async def get_customer_history(self, *, phone: str) -> list[Appointment]:
        async with self._sm() as session:
            customer = await self._by_phone(session, phone)
            if customer is None:
                return []
            rows = (
                await session.scalars(
                    select(AppointmentRow)
                    .where(AppointmentRow.customer_id == customer.id)
                    .options(selectinload(AppointmentRow.staff))
                    .order_by(AppointmentRow.start_at)
                )
            ).all()
            return [self._appt(r, r.staff.name, customer.phone, customer.name) for r in rows]

    async def _by_phone(self, session: AsyncSession, phone: str) -> CustomerRow | None:
        return await session.scalar(
            select(CustomerRow).where(
                CustomerRow.business_id == self._business_id, CustomerRow.phone == phone
            )
        )


class SqlSchedulingProvider(SqlProviderBase):
    async def check_availability(
        self, *, service: str, date: str, part_of_day: str | None = None
    ) -> list[Slot]:
        day = _date(date)
        async with self._sm() as session:
            staff = await self._staff_members(session)
            biz_hours = await self._business_hours(session)
            appointments = await self._day_appointments(session, day)
            duration = await self._duration(session, service)

        slots = compute_slots(
            day=day,
            tz=self._tz,
            business_hours=biz_hours,
            staff=staff,
            appointments=appointments,
            duration=duration,
            now=datetime.now(self._tz),  # never offer slots in the past
        )
        if part_of_day:
            slots = [s for s in slots if _matches_part_of_day(s.start, part_of_day)]
        return slots

    async def create_appointment(
        self, *, name: str, phone: str, service: str, start: str, staff_id: str | None = None
    ) -> Appointment:
        start_dt = self._parse(start)
        async with self._sm() as session:
            async with session.begin():
                duration = await self._duration(session, service)
                end_dt = start_dt + duration
                staff = await self._staff_members(session)
                biz_hours = await self._business_hours(session)
                appointments = await self._day_appointments(session, start_dt.date())

                candidates = [s for s in staff if staff_id in (None, s.id)]
                member = _first_free(
                    candidates, biz_hours, appointments, start_dt, end_dt, self._tz
                )
                if member is None:
                    raise SchedulingConflict(f"No availability for {service} at {start}")

                # Upsert customer inside the same transaction (dedup by phone).
                customer = await session.scalar(
                    select(CustomerRow).where(
                        CustomerRow.business_id == self._business_id, CustomerRow.phone == phone
                    )
                )
                if customer is None:
                    customer = CustomerRow(business_id=self._business_id, name=name, phone=phone)
                    session.add(customer)
                    await session.flush()
                elif name and not customer.name:
                    customer.name = name

                row = AppointmentRow(
                    business_id=self._business_id,
                    customer_id=customer.id,
                    staff_id=uuid.UUID(member.id),
                    service=service,
                    start_at=start_dt,
                    end_at=end_dt,
                    status="booked",
                    confirmation_code=_code(),
                )
                session.add(row)
                try:
                    await session.flush()
                except IntegrityError as exc:
                    # The EXCLUDE constraint caught a concurrent overlap.
                    raise SchedulingConflict(f"Slot at {start} was just taken") from exc

                return self._appt(row, member.name, phone, customer.name or name)

    async def reschedule_appointment(
        self, *, appointment_id: str, new_start: str
    ) -> Appointment | None:
        new_start_dt = self._parse(new_start)
        async with self._sm() as session, session.begin():
            row = await session.get(
                AppointmentRow,
                uuid.UUID(appointment_id),
                options=[selectinload(AppointmentRow.staff), selectinload(AppointmentRow.customer)],
            )
            if row is None or row.status == "cancelled":
                return None
            duration = row.end_at - row.start_at
            new_end_dt = new_start_dt + duration

            staff = await self._staff_members(session)
            biz_hours = await self._business_hours(session)
            appointments = [
                a
                for a in await self._day_appointments(session, new_start_dt.date())
                if a.id != str(row.id)
            ]
            member = next((s for s in staff if s.id == str(row.staff_id)), None)
            free = member is not None and _first_free(
                [member], biz_hours, appointments, new_start_dt, new_end_dt, self._tz
            )
            if not free:
                raise SchedulingConflict(f"No availability at {new_start}")

            row.start_at, row.end_at, row.status = new_start_dt, new_end_dt, "rescheduled"
            try:
                await session.flush()
            except IntegrityError as exc:
                raise SchedulingConflict(f"Slot at {new_start} was just taken") from exc
            return self._appt(row, row.staff.name, row.customer.phone, row.customer.name)

    async def cancel_appointment(self, *, appointment_id: str) -> bool:
        async with self._sm() as session, session.begin():
            row = await session.get(AppointmentRow, uuid.UUID(appointment_id))
            if row is None:
                return False
            row.status = "cancelled"
            return True

    async def get_appointment(self, *, phone: str) -> list[Appointment]:
        async with self._sm() as session:
            rows = (
                await session.scalars(
                    select(AppointmentRow)
                    .join(CustomerRow, AppointmentRow.customer_id == CustomerRow.id)
                    .where(
                        CustomerRow.business_id == self._business_id,
                        CustomerRow.phone == phone,
                        AppointmentRow.status.in_(_ACTIVE),
                    )
                    .options(
                        selectinload(AppointmentRow.staff), selectinload(AppointmentRow.customer)
                    )
                    .order_by(AppointmentRow.start_at)
                )
            ).all()
            return [self._appt(r, r.staff.name, r.customer.phone, r.customer.name) for r in rows]

    # ── Loaders (domain shapes for compute_slots) ────────────────────────────
    async def _staff_members(self, session: AsyncSession) -> list[StaffMember]:
        rows = (
            await session.scalars(
                select(StaffRow)
                .where(StaffRow.business_id == self._business_id, StaffRow.active.is_(True))
                .options(selectinload(StaffRow.hours))
            )
        ).all()
        return [
            StaffMember(
                id=str(r.id),
                name=r.name,
                hours=[WorkingHours(h.weekday, h.start_time, h.end_time) for h in r.hours],
            )
            for r in rows
        ]

    async def _business_hours(self, session: AsyncSession) -> list[WorkingHours]:
        rows = (
            await session.scalars(
                select(BusinessHoursRow).where(BusinessHoursRow.business_id == self._business_id)
            )
        ).all()
        return [WorkingHours(r.weekday, r.start_time, r.end_time) for r in rows]

    async def _day_appointments(self, session: AsyncSession, day: date) -> list[Appointment]:
        day_start = datetime.combine(day, datetime.min.time(), tzinfo=self._tz)
        rows = (
            await session.scalars(
                select(AppointmentRow)
                .where(
                    AppointmentRow.business_id == self._business_id,
                    AppointmentRow.status.in_(_ACTIVE),
                    AppointmentRow.start_at >= day_start,
                    AppointmentRow.start_at < day_start + timedelta(days=1),
                )
                .options(selectinload(AppointmentRow.staff), selectinload(AppointmentRow.customer))
            )
        ).all()
        return [self._appt(r, r.staff.name, r.customer.phone, r.customer.name) for r in rows]

    async def _duration(self, session: AsyncSession, service: str) -> timedelta:
        minutes = await session.scalar(
            select(ServiceRow.duration_min).where(
                ServiceRow.business_id == self._business_id,
                ServiceRow.name.ilike(service.strip()),
            )
        )
        return timedelta(minutes=minutes or 30)


class SqlNotificationProvider(SqlProviderBase):
    """Staff notices become persistent front-desk tasks. Confirmation/SMS delivery
    channels (Resend, Twilio SMS) are wired in later steps; the action itself is
    always recorded in action_log by the engine."""

    async def notify_staff(self, *, message: str, priority: str = "medium") -> bool:
        async with self._sm() as session, session.begin():
            session.add(TaskRow(business_id=self._business_id, reason=message, priority=priority))
            return True

    async def send_confirmation(self, *, phone, appointment, channel: str = "email") -> bool:
        log.info("notify.confirmation_recorded", phone=phone, channel=channel)
        return True

    async def send_sms(self, *, to: str, body: str) -> bool:
        log.info("notify.sms_recorded", to=to)
        return True


def _first_free(
    candidates: list[StaffMember],
    biz_hours: list[WorkingHours],
    appointments: list[Appointment],
    start: datetime,
    end: datetime,
    tz: ZoneInfo,
) -> StaffMember | None:
    """First staff member whose hours cover [start, end) with no overlap."""
    weekday = start.date().weekday()

    def within(windows: list[WorkingHours]) -> bool:
        return any(
            h.weekday == weekday
            and datetime.combine(start.date(), h.start, tzinfo=tz) <= start
            and end <= datetime.combine(start.date(), h.end, tzinfo=tz)
            for h in windows
        )

    if not within(biz_hours):
        return None
    for member in candidates:
        if not within(member.hours):
            continue
        busy = any(
            a.staff_id == member.id and a.status != "cancelled" and a.start < end and start < a.end
            for a in appointments
        )
        if not busy:
            return member
    return None


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
