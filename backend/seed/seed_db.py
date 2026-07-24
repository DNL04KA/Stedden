"""Seed the Lumera demo business into Postgres. Idempotent: safe to run repeatedly.

uv run python -m seed.seed_db
"""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.kb import KnowledgeBase
from db.base import get_sessionmaker
from db.models import Business, BusinessHoursRow, Faq, PolicyRow, Service, Staff, StaffHours
from seed.lumera import lumera_internal_dataset, lumera_kb, lumera_provider_config


async def ensure_seeded(sm: async_sessionmaker[AsyncSession]) -> None:
    kb: KnowledgeBase = lumera_kb()
    dataset = lumera_internal_dataset()
    config = lumera_provider_config()
    business_id = uuid.UUID(kb.business_id)

    async with sm() as session, session.begin():
        existing = await session.get(Business, business_id)
        if existing is not None:
            return
        session.add(
            Business(
                id=business_id,
                name=kb.name,
                tagline=kb.tagline,
                phone=kb.phone,
                address=kb.address,
                timezone=kb.timezone,
                owner_email=kb.owner_email,
                greeting=kb.greeting,
                voice_personality=kb.voice_personality,
                escalation_line=kb.escalation_line,
                recording_disclosure=kb.recording_disclosure,
                customer_provider=config.customer_provider,
                scheduling_provider=config.scheduling_provider,
                notification_provider=config.notification_provider,
            )
        )
        for s in kb.services:
            session.add(
                Service(
                    business_id=business_id,
                    name=s.name,
                    category=s.category,
                    price=s.price,
                    description=s.description,
                    duration_min=dataset.service_durations.get(
                        s.name.strip().lower(), dataset.default_duration_min
                    ),
                )
            )
        for f in kb.faqs:
            session.add(Faq(business_id=business_id, question=f.question, answer=f.answer))
        for p in kb.policies:
            session.add(PolicyRow(business_id=business_id, title=p.title, content=p.content))

        # Front desk: opening hours + staff with weekly schedules.
        for h in dataset.business_hours:
            session.add(
                BusinessHoursRow(
                    business_id=business_id,
                    weekday=h.weekday,
                    start_time=h.start,
                    end_time=h.end,
                )
            )
        for member in dataset.staff:
            staff_row = Staff(business_id=business_id, name=member.name, active=True)
            session.add(staff_row)
            await session.flush()  # need staff_row.id for hours
            for h in member.hours:
                session.add(
                    StaffHours(
                        staff_id=staff_row.id,
                        weekday=h.weekday,
                        start_time=h.start,
                        end_time=h.end,
                    )
                )


async def _main() -> None:
    sm = get_sessionmaker()
    await ensure_seeded(sm)
    print("Seeded Lumera Aesthetics (business, services, FAQ, policies, hours, staff).")


if __name__ == "__main__":
    asyncio.run(_main())
