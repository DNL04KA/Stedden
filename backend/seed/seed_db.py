"""Seed the Lumera demo business into Postgres. Idempotent: safe to run repeatedly.

uv run python -m seed.seed_db
"""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.kb import KnowledgeBase
from db.base import get_sessionmaker
from db.models import Business, Faq, PolicyRow, Service
from seed.lumera import lumera_kb


async def ensure_seeded(sm: async_sessionmaker[AsyncSession]) -> None:
    kb: KnowledgeBase = lumera_kb()
    business_id = uuid.UUID(kb.business_id)
    async with sm() as session, session.begin():
        existing = await session.get(Business, business_id)
        if existing is not None:
            return
        business = Business(
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
        )
        session.add(business)
        for s in kb.services:
            session.add(
                Service(
                    business_id=business_id,
                    name=s.name,
                    category=s.category,
                    price=s.price,
                    description=s.description,
                )
            )
        for f in kb.faqs:
            session.add(Faq(business_id=business_id, question=f.question, answer=f.answer))
        for p in kb.policies:
            session.add(PolicyRow(business_id=business_id, title=p.title, content=p.content))


async def _main() -> None:
    sm = get_sessionmaker()
    await ensure_seeded(sm)
    print("Seeded Lumera Aesthetics.")


if __name__ == "__main__":
    asyncio.run(_main())
