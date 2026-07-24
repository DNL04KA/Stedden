"""Small persistence helpers used by the --persist simulator path and (later) the
voice worker. Call creation and per-message transcript recording live here."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.schemas import ConversationState
from db.models import Call, TranscriptMessage


async def create_call(
    sm: async_sessionmaker[AsyncSession],
    *,
    business_id: str,
    external_call_sid: str | None = None,
    caller_number: str | None = None,
) -> uuid.UUID:
    async with sm() as session, session.begin():
        call = Call(
            business_id=uuid.UUID(business_id),
            external_call_sid=external_call_sid,
            caller_number=caller_number,
            status="in_progress",
            started_at=datetime.now(UTC),
        )
        session.add(call)
        await session.flush()
        return call.id


async def add_message(
    sm: async_sessionmaker[AsyncSession],
    *,
    call_id: uuid.UUID,
    seq: int,
    role: str,
    content: str,
    state: ConversationState,
) -> None:
    async with sm() as session, session.begin():
        session.add(
            TranscriptMessage(
                call_id=call_id,
                seq=seq,
                role=role,
                content=content,
                state=state.value,
            )
        )
