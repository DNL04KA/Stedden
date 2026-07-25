"""TwiML generation and Twilio webhook signature validation.

We emit the XML directly rather than pulling in the Twilio SDK: the document is
one element deep and the signature algorithm is a documented HMAC-SHA1, so the
dependency would buy nothing.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from xml.sax.saxutils import quoteattr

from core.config import Settings


def relay_twiml(*, websocket_url: str, greeting: str, settings: Settings) -> str:
    """<Connect><ConversationRelay> — hands the call's audio to Twilio.

    ``welcomeGreeting`` is spoken by Twilio's TTS before our WebSocket receives
    anything, so the caller hears the disclosure immediately even if the Brain is
    still warming up.
    """
    attrs = {
        "url": websocket_url,
        "welcomeGreeting": greeting,
        "ttsProvider": settings.conversation_relay_tts_provider,
        "transcriptionProvider": settings.conversation_relay_stt_provider,
        "language": settings.conversation_relay_language,
        # Barge-in: let the caller interrupt the assistant mid-sentence.
        "interruptible": "true",
    }
    if settings.conversation_relay_voice:
        attrs["voice"] = settings.conversation_relay_voice

    rendered = " ".join(f"{key}={quoteattr(value)}" for key, value in attrs.items())
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response><Connect>"
        f"<ConversationRelay {rendered} />"
        "</Connect></Response>"
    )


def hangup_twiml(message: str) -> str:
    """Spoken apology + hangup, used when a call can't be routed to a business."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<Response><Say>{_escape_text(message)}</Say><Hangup /></Response>"
    )


def is_valid_twilio_signature(
    *, auth_token: str, url: str, params: dict[str, str], signature: str
) -> bool:
    """Twilio's documented scheme: HMAC-SHA1 over the URL with sorted POST params
    appended as key+value pairs, base64-encoded."""
    if not auth_token or not signature:
        return False
    payload = url + "".join(f"{key}{params[key]}" for key in sorted(params))
    digest = hmac.new(auth_token.encode("utf-8"), payload.encode("utf-8"), hashlib.sha1).digest()
    expected = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(expected, signature)


def _escape_text(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
