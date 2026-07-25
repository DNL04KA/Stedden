"""ConversationRelay WebSocket protocol.

Twilio sends us text (it owns STT, TTS, and audio); we reply with the text to
speak. Messages we receive: ``setup``, ``prompt``, ``interrupt``, ``dtmf``,
``error``. Messages we send: ``text`` (speak this) and ``end`` (hang up).

The handler is transport-agnostic — it takes a ``send`` callable — so the protocol
is testable without a socket. Every branch is defensive: a failure handling one
message must never drop the call.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import structlog

from core.config import Settings
from voice_worker.runtime import VoiceSession, VoiceSessionManager

log = structlog.get_logger("stedden.voice.relay")

Send = Callable[[dict[str, Any]], Awaitable[None]]

#: Spoken when the turn pipeline itself fails. The Brain has its own safe
#: fallback; this covers everything outside it (transport, persistence, bugs).
TURN_ERROR_LINE = (
    "I'm sorry, I'm having a little trouble on my end. Let me have someone from "
    "our team follow up with you."
)
SILENCE_PROMPT_LINE = "Are you still there?"
SILENCE_HANGUP_LINE = "I'll let you go for now. Please call us back any time. Goodbye."


class RelayHandler:
    """Owns one WebSocket connection's conversation."""

    def __init__(self, *, manager: VoiceSessionManager, send: Send, settings: Settings) -> None:
        self._manager = manager
        self._send = send
        self._settings = settings
        self.call_sid: str | None = None
        self.finished = False
        self._voice: VoiceSession | None = None
        self._silence_prompted = False

    # ── Inbound ──────────────────────────────────────────────────────────────
    async def handle(self, message: dict[str, Any]) -> None:
        kind = message.get("type")
        try:
            match kind:
                case "setup":
                    await self._on_setup(message)
                case "prompt":
                    await self._on_prompt(message)
                case "interrupt":
                    self._on_interrupt(message)
                case "dtmf":
                    log.info("relay.dtmf", call_sid=self.call_sid, digit=message.get("digit"))
                case "error":
                    log.warning(
                        "relay.twilio_error",
                        call_sid=self.call_sid,
                        description=message.get("description"),
                    )
                case _:
                    log.info("relay.unknown_message", call_sid=self.call_sid, kind=kind)
        except Exception as exc:  # never let one bad message end the call
            log.exception("relay.handler_failed", call_sid=self.call_sid, kind=kind, error=str(exc))
            await self._speak(TURN_ERROR_LINE)

    async def _on_setup(self, message: dict[str, Any]) -> None:
        self.call_sid = message.get("callSid") or message.get("callsid")
        if not self.call_sid:
            log.warning("relay.setup_without_call_sid")
            return
        self._voice = await self._manager.start(
            call_sid=self.call_sid, caller_number=message.get("from")
        )
        # Twilio already spoke `welcomeGreeting` from the TwiML; record it so the
        # transcript and the Brain's history both start with what the caller heard.
        if not self._voice.session.history:
            await self._voice.session.open_with_greeting()

    async def _on_prompt(self, message: dict[str, Any]) -> None:
        # Partial transcripts arrive with last=false; act only on the final one.
        if not message.get("last", True):
            return
        text = (message.get("voicePrompt") or "").strip()
        if not text or self._voice is None:
            return

        self._silence_prompted = False
        view = await self._voice.session.send(text)
        await self._manager.record_actions(self._voice, view.actions)
        await self._speak(view.result.say)

        if view.result.should_end_call:
            await self.finish(reason="brain_ended")

    def _on_interrupt(self, message: dict[str, Any]) -> None:
        """Barge-in: the caller talked over the assistant. ConversationRelay stops
        playback on its own and tells us how much was actually heard, so we correct
        the transcript to what the caller really received."""
        if self._voice is None:
            return
        heard = (message.get("utteranceUntilInterrupt") or "").strip()
        self._voice.last_interrupted_text = heard
        history = self._voice.session.history
        if heard and history and history[-1].role == "assistant":
            history[-1].content = heard
        log.info("relay.interrupt", call_sid=self.call_sid, heard_chars=len(heard))

    # ── Silence watchdog ─────────────────────────────────────────────────────
    async def on_silence(self) -> None:
        """Called by the endpoint's watchdog. First a nudge, then a polite hangup."""
        if self.finished:
            return
        if not self._silence_prompted:
            self._silence_prompted = True
            await self._speak(SILENCE_PROMPT_LINE)
            return
        await self._speak(SILENCE_HANGUP_LINE)
        await self.finish(reason="silence")

    @property
    def silence_prompted(self) -> bool:
        """True once the caller has been nudged; the next timeout hangs up."""
        return self._silence_prompted

    @property
    def silence_prompt_seconds(self) -> float:
        return self._settings.voice_silence_prompt_seconds

    @property
    def silence_hangup_seconds(self) -> float:
        return self._settings.voice_silence_hangup_seconds

    # ── Outbound ─────────────────────────────────────────────────────────────
    async def _speak(self, text: str) -> None:
        if not text:
            return
        await self._send({"type": "text", "token": text, "last": True})

    async def finish(self, *, reason: str) -> None:
        """End the Relay session and close out the call record. Idempotent."""
        if self.finished:
            return
        self.finished = True
        try:
            await self._send({"type": "end", "handoffData": reason})
        except Exception as exc:  # socket already gone — still finalize the call
            log.warning("relay.end_send_failed", call_sid=self.call_sid, error=str(exc))
        await self.close(reason=reason)

    async def close(self, *, reason: str) -> None:
        """Finalize persistence. Safe to call more than once."""
        if self.call_sid is None:
            return
        await self._manager.end(self.call_sid, reason=reason)
        self.call_sid = None
