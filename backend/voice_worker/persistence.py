"""Call persistence for the voice worker.

A tiny seam over ``db.repository`` so handler tests can run without PostgreSQL
while the real path still writes call, transcript, and action-log rows.
"""

from __future__ import annotations

import uuid
from typing import Protocol

from core.schemas import ConversationState
from engine.context import ActionRecord


class CallPersistence(Protocol):
    async def start_call(self, *, call_sid: str, caller_number: str | None) -> uuid.UUID | None: ...
    async def add_message(
        self,
        *,
        call_id: uuid.UUID | None,
        seq: int,
        role: str,
        content: str,
        state: ConversationState,
    ) -> None: ...
    async def save_actions(
        self, *, call_id: uuid.UUID | None, records: list[ActionRecord], start_seq: int
    ) -> int: ...
    async def complete_call(self, *, call_id: uuid.UUID | None) -> None: ...
    async def complete_call_by_sid(self, *, call_sid: str) -> None: ...


class NullCallPersistence:
    """No-op implementation for tests that only exercise the relay protocol."""

    async def start_call(self, *, call_sid: str, caller_number: str | None) -> uuid.UUID | None:
        return None

    async def add_message(self, *, call_id, seq, role, content, state) -> None:
        return None

    async def save_actions(self, *, call_id, records, start_seq) -> int:
        return start_seq + len(records)

    async def complete_call(self, *, call_id) -> None:
        return None

    async def complete_call_by_sid(self, *, call_sid: str) -> None:
        return None


class SqlCallPersistence:
    """Writes to PostgreSQL. CallSid is the unique external identifier, so a
    Twilio webhook retry reuses the existing call row instead of duplicating it."""

    def __init__(self, sessionmaker, business_id: str) -> None:
        self._sm = sessionmaker
        self._business_id = business_id

    async def start_call(self, *, call_sid: str, caller_number: str | None) -> uuid.UUID | None:
        from sqlalchemy import select

        from db.models import Call
        from db.repository import create_call

        async with self._sm() as session:
            existing = await session.scalar(select(Call).where(Call.external_call_sid == call_sid))
            if existing is not None:
                return existing.id
        return await create_call(
            self._sm,
            business_id=self._business_id,
            external_call_sid=call_sid,
            caller_number=caller_number,
        )

    async def add_message(self, *, call_id, seq, role, content, state) -> None:
        if call_id is None:
            return
        from db.repository import add_message

        await add_message(
            self._sm, call_id=call_id, seq=seq, role=role, content=content, state=state
        )

    async def save_actions(self, *, call_id, records, start_seq) -> int:
        if call_id is None or not records:
            return start_seq
        from db.repository import save_action_records

        return await save_action_records(
            self._sm,
            business_id=self._business_id,
            call_id=call_id,
            records=records,
            start_seq=start_seq,
        )

    async def complete_call(self, *, call_id) -> None:
        if call_id is None:
            return
        from db.repository import mark_call_completed

        await mark_call_completed(self._sm, call_id=call_id)

    async def complete_call_by_sid(self, *, call_sid: str) -> None:
        """Finalize by CallSid. Twilio's status callback is authoritative for call
        completion, and may arrive after the WebSocket is gone or after a restart,
        so it must not depend on an in-memory session."""
        from sqlalchemy import select

        from db.models import Call
        from db.repository import mark_call_completed

        async with self._sm() as session:
            call = await session.scalar(select(Call).where(Call.external_call_sid == call_sid))
        if call is not None:
            await mark_call_completed(self._sm, call_id=call.id)
