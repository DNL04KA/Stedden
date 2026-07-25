"""Shared dependencies and the voice session registry.

``VoiceRuntime`` is built once at startup (or handed in by tests) and holds the
knowledge base, providers, Action Engine, and LLM generator. ``VoiceSessionManager``
maps one Twilio CallSid to exactly one ``ConversationSession`` for the life of the
call.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

import structlog

from core.kb import KnowledgeBase
from core.llm import TurnGenerator
from engine.engine import ActionEngine
from providers.registry import ProviderSet
from simulator.session import ConversationSession
from voice_worker.persistence import CallPersistence, NullCallPersistence

log = structlog.get_logger("stedden.voice")


@dataclass(slots=True)
class VoiceRuntime:
    kb: KnowledgeBase
    providers: ProviderSet
    engine: ActionEngine
    generator_factory: Callable[[], TurnGenerator]
    persistence: CallPersistence = field(default_factory=NullCallPersistence)


@dataclass(slots=True)
class VoiceSession:
    """One live phone call."""

    call_sid: str
    session: ConversationSession
    call_id: uuid.UUID | None
    caller_number: str | None = None
    action_seq: int = 0
    #: Text the caller interrupted, reported by ConversationRelay on barge-in.
    last_interrupted_text: str = ""


class VoiceSessionManager:
    """CallSid -> VoiceSession. One call, one ConversationSession."""

    def __init__(self, runtime: VoiceRuntime) -> None:
        self._runtime = runtime
        self._sessions: dict[str, VoiceSession] = {}

    @property
    def runtime(self) -> VoiceRuntime:
        return self._runtime

    def get(self, call_sid: str) -> VoiceSession | None:
        return self._sessions.get(call_sid)

    @property
    def active_count(self) -> int:
        return len(self._sessions)

    async def start(self, *, call_sid: str, caller_number: str | None) -> VoiceSession:
        """Create the session for a call. Idempotent: a duplicate `setup` for the
        same CallSid returns the existing session rather than resetting the call."""
        existing = self._sessions.get(call_sid)
        if existing is not None:
            return existing

        rt = self._runtime
        call_id = await rt.persistence.start_call(call_sid=call_sid, caller_number=caller_number)

        async def sink(role: str, content: str, state, seq: int) -> None:
            await rt.persistence.add_message(
                call_id=call_id, seq=seq, role=role, content=content, state=state
            )

        conversation = ConversationSession(
            rt.kb,
            rt.generator_factory(),
            rt.engine,
            rt.providers,
            call_id=str(call_id) if call_id else f"voice-{call_sid}",
            message_sink=sink,
        )
        voice = VoiceSession(
            call_sid=call_sid,
            session=conversation,
            call_id=call_id,
            caller_number=caller_number,
        )
        self._sessions[call_sid] = voice
        log.info("voice.session_started", call_sid=call_sid, call_id=str(call_id))
        return voice

    async def record_actions(self, voice: VoiceSession, records) -> None:
        voice.action_seq = await self._runtime.persistence.save_actions(
            call_id=voice.call_id, records=records, start_seq=voice.action_seq
        )

    async def end(self, call_sid: str, *, reason: str = "completed") -> None:
        voice = self._sessions.pop(call_sid, None)
        if voice is None:
            return
        await self._runtime.persistence.complete_call(call_id=voice.call_id)
        log.info("voice.session_ended", call_sid=call_sid, reason=reason)

    async def finalize_by_sid(self, call_sid: str, *, reason: str) -> None:
        """Close out a call from Twilio's status callback: drop any in-memory
        session and mark the record completed even if the session is already gone
        (WebSocket closed first, or the worker restarted)."""
        await self.end(call_sid, reason=reason)
        await self._runtime.persistence.complete_call_by_sid(call_sid=call_sid)


def build_sql_runtime() -> VoiceRuntime:
    """Production wiring: Lumera on Stedden-internal SQL providers."""
    from core.config import get_settings
    from db.base import get_sessionmaker
    from providers.registry import STEDDEN_INTERNAL, ProviderBundle, resolve_providers
    from providers.sql_internal import (
        SqlCustomerProvider,
        SqlNotificationProvider,
        SqlSchedulingProvider,
    )
    from seed.lumera import lumera_kb, lumera_provider_config
    from voice_worker.persistence import SqlCallPersistence

    settings = get_settings()
    kb = lumera_kb()
    sm = get_sessionmaker()
    tz = ZoneInfo(kb.timezone)

    bundle = ProviderBundle(
        customer=SqlCustomerProvider(sm, kb.business_id, tz),
        scheduling=SqlSchedulingProvider(sm, kb.business_id, tz),
        notification=SqlNotificationProvider(sm, kb.business_id, tz),
    )
    providers = resolve_providers(lumera_provider_config(), bundles={STEDDEN_INTERNAL: bundle})

    def generator_factory() -> TurnGenerator:
        from openai import AsyncOpenAI

        from core.llm import OpenAITurnGenerator

        return OpenAITurnGenerator(
            AsyncOpenAI(api_key=settings.openai_api_key),
            model=settings.openai_model,
            timeout=settings.openai_timeout_seconds,
        )

    return VoiceRuntime(
        kb=kb,
        providers=providers,
        engine=ActionEngine(),
        generator_factory=generator_factory,
        persistence=SqlCallPersistence(sm, kb.business_id),
    )
