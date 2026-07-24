"""Small persistence helpers used by the --persist simulator path and (later) the
voice worker. Call creation and per-message transcript recording live here."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.schemas import ConversationState
from db.models import ActionLog, Call, TranscriptMessage
from engine.context import ActionRecord


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


async def save_action_records(
    sm: async_sessionmaker[AsyncSession],
    *,
    business_id: str,
    call_id: uuid.UUID,
    records: list[ActionRecord],
    start_seq: int,
) -> int:
    """Persist Action Engine audit records for one turn. Returns the next seq."""
    if not records:
        return start_seq
    async with sm() as session, session.begin():
        for offset, record in enumerate(records):
            session.add(
                ActionLog(
                    business_id=uuid.UUID(business_id),
                    call_id=call_id,
                    seq=start_seq + offset,
                    action=record.action,
                    args=record.args,
                    status=record.status,
                    result=record.result,
                    provider=record.provider,
                    external_ref=record.external_ref,
                    error=record.error,
                )
            )
    return start_seq + len(records)


async def mark_call_completed(sm: async_sessionmaker[AsyncSession], *, call_id: uuid.UUID) -> None:
    async with sm() as session, session.begin():
        call = await session.get(Call, call_id)
        if call is not None and call.status != "completed":
            call.status = "completed"
            call.ended_at = datetime.now(UTC)
