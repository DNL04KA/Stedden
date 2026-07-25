"""Voice worker: Twilio webhook + ConversationRelay WebSocket.

Kept separate from the REST API so a crash here can never take down the admin
service, and vice versa. Business logic lives in the Brain, the Action Engine,
and the providers — routes only translate transport.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import APIRouter, FastAPI, Request, Response, WebSocket, WebSocketDisconnect

from core.config import Settings, get_settings
from voice_worker.relay import RelayHandler
from voice_worker.runtime import VoiceRuntime, VoiceSessionManager, build_sql_runtime
from voice_worker.twiml import hangup_twiml, is_valid_twilio_signature, relay_twiml

log = structlog.get_logger("stedden.voice.app")

ROUTE_INCOMING = "/voice/incoming"
ROUTE_STATUS = "/voice/status"
ROUTE_RELAY = "/voice/relay"

UNAVAILABLE_LINE = (
    "Sorry, our virtual receptionist is unavailable right now. Please call back shortly."
)


def websocket_url(settings: Settings) -> str:
    base = settings.public_base_url.rstrip("/")
    if base.startswith("https://"):
        base = "wss://" + base[len("https://") :]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://") :]
    return base + ROUTE_RELAY


def create_app(runtime: VoiceRuntime | None = None, settings: Settings | None = None) -> FastAPI:
    """Build the app. Tests inject a runtime; production builds the SQL one."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Tests inject a runtime; production builds it on first boot.
        if app.state.runtime is None:
            app.state.runtime = build_sql_runtime()
            app.state.manager = VoiceSessionManager(app.state.runtime)
        yield

    app = FastAPI(title="Stedden Voice Worker", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings or get_settings()
    app.state.runtime = runtime
    app.state.manager = VoiceSessionManager(runtime) if runtime is not None else None
    app.include_router(_router())
    return app


def _router() -> APIRouter:
    router = APIRouter()

    @router.get("/health")
    async def health(request: Request) -> dict:
        manager: VoiceSessionManager | None = request.app.state.manager
        return {
            "status": "ok",
            "service": "voice_worker",
            "active_calls": manager.active_count if manager else 0,
        }

    @router.post(ROUTE_INCOMING)
    async def incoming_call(request: Request) -> Response:
        """Twilio's inbound-call webhook. Returns TwiML connecting the audio to
        ConversationRelay, which then opens a WebSocket back to us."""
        settings: Settings = request.app.state.settings
        form = dict(await request.form())
        if not _signature_ok(request, settings, form):
            log.warning("voice.invalid_signature", path=ROUTE_INCOMING)
            return Response(status_code=403, content="invalid signature")

        runtime: VoiceRuntime | None = request.app.state.runtime
        if runtime is None:
            return Response(content=hangup_twiml(UNAVAILABLE_LINE), media_type="application/xml")

        log.info(
            "voice.incoming",
            call_sid=form.get("CallSid"),
            from_number=form.get("From"),
            to_number=form.get("To"),
        )
        xml = relay_twiml(
            websocket_url=websocket_url(settings),
            greeting=runtime.kb.greeting,
            settings=settings,
        )
        return Response(content=xml, media_type="application/xml")

    @router.post(ROUTE_STATUS)
    async def call_status(request: Request) -> Response:
        """Twilio status callback — final call metadata after hangup."""
        settings: Settings = request.app.state.settings
        form = dict(await request.form())
        if not _signature_ok(request, settings, form):
            return Response(status_code=403, content="invalid signature")

        call_sid = form.get("CallSid", "")
        status = form.get("CallStatus", "")
        log.info(
            "voice.status", call_sid=call_sid, status=status, duration=form.get("CallDuration")
        )

        manager: VoiceSessionManager | None = request.app.state.manager
        # Authoritative for completion: covers hangups where the WebSocket closed
        # without a clean end message, and calls whose session is already gone.
        terminal = {"completed", "failed", "busy", "no-answer", "canceled"}
        if manager is not None and call_sid and status in terminal:
            await manager.finalize_by_sid(call_sid, reason=status)
        return Response(status_code=204)

    @router.websocket(ROUTE_RELAY)
    async def relay(websocket: WebSocket) -> None:
        await websocket.accept()
        settings: Settings = websocket.app.state.settings
        manager: VoiceSessionManager | None = websocket.app.state.manager
        if manager is None:
            await websocket.close(code=1011)
            return

        async def send(payload: dict) -> None:
            await websocket.send_text(json.dumps(payload))

        handler = RelayHandler(manager=manager, send=send, settings=settings)
        try:
            await _receive_loop(websocket, handler)
        except WebSocketDisconnect:
            log.info("relay.disconnected", call_sid=handler.call_sid)
        except Exception as exc:
            log.exception("relay.loop_failed", call_sid=handler.call_sid, error=str(exc))
        finally:
            await handler.close(reason="socket_closed")

    return router


async def _receive_loop(websocket: WebSocket, handler: RelayHandler) -> None:
    """Read Relay messages, applying a two-stage silence watchdog.

    ConversationRelay emits no silence events, so an idle timeout is our own:
    first a nudge, then a polite hangup.
    """
    while not handler.finished:
        timeout = (
            handler.silence_prompt_seconds
            if not handler.silence_prompted
            else max(handler.silence_hangup_seconds - handler.silence_prompt_seconds, 1.0)
        )
        try:
            raw = await asyncio.wait_for(websocket.receive_text(), timeout=timeout)
        except TimeoutError:
            await handler.on_silence()
            continue

        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("relay.bad_json", call_sid=handler.call_sid)
            continue
        await handler.handle(message)


def _signature_ok(request: Request, settings: Settings, form: dict) -> bool:
    """Validate X-Twilio-Signature when an auth token is configured.

    The signed URL is rebuilt from PUBLIC_BASE_URL because behind a tunnel or
    proxy `request.url` carries the internal host, which would never match.
    """
    if not settings.twilio_validate_signature or not settings.twilio_auth_token:
        return True
    url = settings.public_base_url.rstrip("/") + request.url.path
    return is_valid_twilio_signature(
        auth_token=settings.twilio_auth_token,
        url=url,
        params={k: str(v) for k, v in form.items()},
        signature=request.headers.get("X-Twilio-Signature", ""),
    )


app = create_app()
