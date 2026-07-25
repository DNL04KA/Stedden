"""Twilio webhook + ConversationRelay protocol, driven locally without telephony.

The WebSocket tests speak the real Relay message shapes against the real
ConversationSession and Action Engine; only the model and the audio are faked.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from core.config import Settings
from core.schemas import ConversationState as S
from core.schemas import Lead, TurnResult
from engine.engine import ActionEngine
from providers.internal import (
    InMemoryCustomerProvider,
    InMemoryNotificationProvider,
    InMemorySchedulingProvider,
)
from providers.registry import STEDDEN_INTERNAL, ProviderBundle, ProviderConfig, resolve_providers
from seed.lumera import lumera_internal_dataset, lumera_kb
from tests.fakes import RaisingGenerator, ScriptedGenerator, ScriptedTurn, ToolCall
from voice_worker.main import create_app
from voice_worker.relay import TURN_ERROR_LINE
from voice_worker.runtime import VoiceRuntime
from voice_worker.twiml import is_valid_twilio_signature

CALL_SID = "CA00000000000000000000000000000001"


def turn(say: str, state=S.FAQ, *, end_call=False, tools=None) -> ScriptedTurn:
    return ScriptedTurn(
        final=TurnResult(say=say, state=state, lead=Lead(), grounded=True, end_call=end_call),
        tool_calls=tools or [],
    )


def build_app(generator, *, settings: Settings | None = None, data=None):
    data = data or lumera_internal_dataset()
    bundle = ProviderBundle(
        customer=InMemoryCustomerProvider(data),
        scheduling=InMemorySchedulingProvider(data),
        notification=InMemoryNotificationProvider(data),
    )
    runtime = VoiceRuntime(
        kb=lumera_kb(),
        providers=resolve_providers(ProviderConfig(), bundles={STEDDEN_INTERNAL: bundle}),
        engine=ActionEngine(),
        generator_factory=lambda: generator,
    )
    settings = settings or Settings(
        public_base_url="https://demo.example.com",
        twilio_auth_token="",
        conversation_relay_voice="voice-id",
    )
    return create_app(runtime=runtime, settings=settings), data


def setup_msg(call_sid: str = CALL_SID) -> str:
    return json.dumps(
        {"type": "setup", "callSid": call_sid, "from": "+13055550100", "to": "+13055550142"}
    )


def prompt_msg(text: str, last: bool = True) -> str:
    return json.dumps({"type": "prompt", "voicePrompt": text, "last": last})


# ── Health ───────────────────────────────────────────────────────────────────
def test_health_reports_active_calls() -> None:
    app, _ = build_app(ScriptedGenerator([]))
    with TestClient(app) as client:
        body = client.get("/health").json()
    assert body == {"status": "ok", "service": "voice_worker", "active_calls": 0}


# ── Webhook / TwiML ──────────────────────────────────────────────────────────
def test_incoming_call_returns_conversationrelay_twiml() -> None:
    app, _ = build_app(ScriptedGenerator([]))
    with TestClient(app) as client:
        resp = client.post("/voice/incoming", data={"CallSid": CALL_SID, "From": "+13055550100"})

    assert resp.status_code == 200
    xml = resp.text
    assert "<ConversationRelay" in xml and "<Connect>" in xml
    assert 'url="wss://demo.example.com/voice/relay"' in xml
    assert "Lumera Aesthetics" in xml  # welcomeGreeting carries the disclosure
    assert "automated assistant" in xml
    assert 'ttsProvider="ElevenLabs"' in xml
    assert 'transcriptionProvider="Deepgram"' in xml
    assert 'interruptible="true"' in xml  # barge-in enabled


def test_incoming_call_rejects_bad_signature() -> None:
    settings = Settings(public_base_url="https://demo.example.com", twilio_auth_token="secret")
    app, _ = build_app(ScriptedGenerator([]), settings=settings)
    with TestClient(app) as client:
        resp = client.post(
            "/voice/incoming",
            data={"CallSid": CALL_SID},
            headers={"X-Twilio-Signature": "obviously-wrong"},
        )
    assert resp.status_code == 403


def test_incoming_call_accepts_valid_signature() -> None:
    token = "secret"
    settings = Settings(public_base_url="https://demo.example.com", twilio_auth_token=token)
    app, _ = build_app(ScriptedGenerator([]), settings=settings)
    params = {"CallSid": CALL_SID, "From": "+13055550100"}
    url = "https://demo.example.com/voice/incoming"

    import base64
    import hashlib
    import hmac

    payload = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    signature = base64.b64encode(
        hmac.new(token.encode(), payload.encode(), hashlib.sha1).digest()
    ).decode()

    with TestClient(app) as client:
        resp = client.post(
            "/voice/incoming", data=params, headers={"X-Twilio-Signature": signature}
        )
    assert resp.status_code == 200
    assert is_valid_twilio_signature(auth_token=token, url=url, params=params, signature=signature)


# ── Relay WebSocket ──────────────────────────────────────────────────────────
def test_prompt_reaches_brain_and_reply_is_spoken() -> None:
    app, _ = build_app(ScriptedGenerator([turn("We're open until 6pm today.")]))
    with TestClient(app) as client, client.websocket_connect("/voice/relay") as ws:
        ws.send_text(setup_msg())
        ws.send_text(prompt_msg("What are your hours?"))
        reply = ws.receive_json()

    assert reply == {"type": "text", "token": "We're open until 6pm today.", "last": True}


def test_partial_transcript_is_ignored_until_final() -> None:
    app, _ = build_app(ScriptedGenerator([turn("Botox is $12 per unit.")]))
    with TestClient(app) as client, client.websocket_connect("/voice/relay") as ws:
        ws.send_text(setup_msg())
        ws.send_text(prompt_msg("How much is", last=False))  # must not trigger a turn
        ws.send_text(prompt_msg("How much is Botox?"))
        reply = ws.receive_json()

    assert reply["token"] == "Botox is $12 per unit."  # only one turn ran


def test_booking_over_the_phone_executes_real_action() -> None:
    """A phone turn drives the same Action Engine as the text simulator."""
    from datetime import date, timedelta

    friday = date.today() + timedelta(days=((4 - date.today().weekday()) % 7) or 7)
    generator = ScriptedGenerator(
        [
            turn(
                "You're booked for Friday at 2 PM.",
                S.GOODBYE,
                end_call=True,
                tools=[
                    ToolCall(
                        "create_appointment",
                        {
                            "name": "Ana",
                            "phone": "305-555-0100",
                            "service": "Botox",
                            "start": f"{friday}T14:00:00",
                        },
                    )
                ],
            )
        ]
    )
    app, data = build_app(generator)
    with TestClient(app) as client, client.websocket_connect("/voice/relay") as ws:
        ws.send_text(setup_msg())
        ws.send_text(prompt_msg("Book me for Botox Friday at 2, this is Ana, 305-555-0100"))
        spoken = ws.receive_json()
        ended = ws.receive_json()

    assert spoken["type"] == "text"
    assert ended["type"] == "end"  # Brain asked to hang up
    assert len(data.appointments) == 1
    booked = next(iter(data.appointments.values()))
    assert booked.service == "Botox" and booked.status == "booked"


def test_model_failure_does_not_drop_the_call() -> None:
    """The Brain's safe fallback is spoken and the socket stays usable."""
    app, _ = build_app(RaisingGenerator())
    with TestClient(app) as client, client.websocket_connect("/voice/relay") as ws:
        ws.send_text(setup_msg())
        ws.send_text(prompt_msg("Do you take my insurance?"))
        reply = ws.receive_json()

        assert reply["type"] == "text"
        assert reply["token"] and reply["token"] != TURN_ERROR_LINE  # Brain-level fallback

        # The call survives: a second prompt is still answered.
        ws.send_text(prompt_msg("Are you there?"))
        second = ws.receive_json()
    assert second["type"] == "text"


def test_unknown_and_malformed_messages_are_survivable() -> None:
    app, _ = build_app(ScriptedGenerator([turn("Sure thing.")]))
    with TestClient(app) as client, client.websocket_connect("/voice/relay") as ws:
        ws.send_text(setup_msg())
        ws.send_text("{not json at all")
        ws.send_text(json.dumps({"type": "dtmf", "digit": "5"}))
        ws.send_text(json.dumps({"type": "error", "description": "twilio-side hiccup"}))
        ws.send_text(json.dumps({"type": "who-knows"}))
        ws.send_text(prompt_msg("Still there?"))
        reply = ws.receive_json()
    assert reply["token"] == "Sure thing."


def test_interrupt_truncates_assistant_history() -> None:
    """Barge-in: history must reflect what the caller actually heard."""
    app, _ = build_app(
        ScriptedGenerator(
            [turn("We offer Botox, fillers, facials, and lasers."), turn("Botox is $12 per unit.")]
        )
    )
    with TestClient(app) as client, client.websocket_connect("/voice/relay") as ws:
        ws.send_text(setup_msg())
        ws.send_text(prompt_msg("What do you offer?"))
        ws.receive_json()
        ws.send_text(
            json.dumps(
                {
                    "type": "interrupt",
                    "utteranceUntilInterrupt": "We offer Botox,",
                    "durationUntilInterruptMs": 900,
                }
            )
        )
        # The next reply is the synchronization point: by the time it arrives the
        # interrupt has been processed (messages are handled in order).
        ws.send_text(prompt_msg("How much is Botox?"))
        ws.receive_json()
        voice = app.state.manager.get(CALL_SID)
        assert voice is not None
        history = voice.session.history

    # [greeting, "What do you offer?", truncated reply, "How much is Botox?", reply]
    assert history[2].role == "assistant"
    assert history[2].content == "We offer Botox,"


def test_one_call_sid_creates_one_session() -> None:
    app, _ = build_app(ScriptedGenerator([turn("Hello again.")]))
    with TestClient(app) as client, client.websocket_connect("/voice/relay") as ws:
        ws.send_text(setup_msg())
        ws.send_text(setup_msg())  # duplicate setup must not reset the call
        manager = app.state.manager
        assert manager.active_count == 1
        first = manager.get(CALL_SID)
        ws.send_text(prompt_msg("Hi"))
        ws.receive_json()
        assert manager.get(CALL_SID) is first


def test_greeting_recorded_once_as_first_transcript_line() -> None:
    app, _ = build_app(ScriptedGenerator([turn("Of course.")]))
    with TestClient(app) as client, client.websocket_connect("/voice/relay") as ws:
        ws.send_text(setup_msg())
        ws.send_text(prompt_msg("Hi there"))
        ws.receive_json()
        voice = app.state.manager.get(CALL_SID)
        history = voice.session.history

    assert history[0].role == "assistant"
    assert "Lumera Aesthetics" in history[0].content  # what Twilio spoke as welcomeGreeting
    assert history[1].role == "user" and history[1].content == "Hi there"


def test_status_callback_closes_orphaned_session() -> None:
    """A hangup that never produced a clean WS close still finalizes the call."""
    app, _ = build_app(ScriptedGenerator([turn("Sure.")]))
    with TestClient(app) as client:
        with client.websocket_connect("/voice/relay") as ws:
            ws.send_text(setup_msg())
            ws.send_text(prompt_msg("Hi"))
            ws.receive_json()
            assert app.state.manager.active_count == 1

        resp = client.post(
            "/voice/status",
            data={"CallSid": CALL_SID, "CallStatus": "completed", "CallDuration": "31"},
        )
    assert resp.status_code == 204


@pytest.mark.parametrize("status", ["completed", "failed", "busy", "no-answer"])
def test_status_callback_accepts_terminal_statuses(status: str) -> None:
    app, _ = build_app(ScriptedGenerator([]))
    with TestClient(app) as client:
        resp = client.post("/voice/status", data={"CallSid": CALL_SID, "CallStatus": status})
    assert resp.status_code == 204


# ── Silence handling ─────────────────────────────────────────────────────────
def test_silence_nudges_then_hangs_up() -> None:
    """ConversationRelay emits no silence events, so the worker times out itself."""
    settings = Settings(
        public_base_url="https://demo.example.com",
        twilio_auth_token="",
        voice_silence_prompt_seconds=0.15,
        voice_silence_hangup_seconds=0.3,
    )
    app, _ = build_app(ScriptedGenerator([]), settings=settings)
    with TestClient(app) as client, client.websocket_connect("/voice/relay") as ws:
        ws.send_text(setup_msg())
        nudge = ws.receive_json()
        farewell = ws.receive_json()
        ended = ws.receive_json()

    assert nudge["token"] == "Are you still there?"
    assert "Goodbye" in farewell["token"]
    assert ended == {"type": "end", "handoffData": "silence"}


# ── Persistence (real PostgreSQL) ────────────────────────────────────────────
def test_call_transcript_and_status_are_persisted(sync_sm) -> None:
    """End of call: transcript rows exist and the call is marked completed."""
    import asyncio
    import uuid as uuid_mod

    from sqlalchemy import select

    from db.models import Call, TranscriptMessage
    from voice_worker.persistence import SqlCallPersistence

    app, _ = build_app(ScriptedGenerator([turn("We're open until 6pm.")]))
    app.state.runtime.persistence = SqlCallPersistence(sync_sm, lumera_kb().business_id)
    call_sid = f"CA{uuid_mod.uuid4().hex}"

    with TestClient(app) as client:
        with client.websocket_connect("/voice/relay") as ws:
            ws.send_text(setup_msg(call_sid))
            ws.send_text(prompt_msg("What are your hours?"))
            ws.receive_json()
        client.post("/voice/status", data={"CallSid": call_sid, "CallStatus": "completed"})

    async def read_back():
        async with sync_sm() as session:
            call = await session.scalar(select(Call).where(Call.external_call_sid == call_sid))
            assert call is not None
            messages = (
                await session.scalars(
                    select(TranscriptMessage)
                    .where(TranscriptMessage.call_id == call.id)
                    .order_by(TranscriptMessage.seq)
                )
            ).all()
            return call.status, [(m.role, m.content) for m in messages]

    status, transcript = asyncio.run(read_back())
    assert status == "completed"
    roles = [role for role, _ in transcript]
    assert roles == ["assistant", "user", "assistant"]  # greeting, caller, reply
    assert transcript[1][1] == "What are your hours?"
    assert transcript[2][1] == "We're open until 6pm."
