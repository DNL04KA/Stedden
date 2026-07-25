"""Environment-backed settings.

The OpenAI model is deliberately isolated here so we can benchmark a faster model
later by changing a single env var — no code changes.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    app_env: str = "local"
    log_level: str = "info"
    public_base_url: str = "http://localhost:8080"

    # Database (Supabase Postgres). psycopg3 URL works for async via SQLAlchemy.
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/stedden"

    # OpenAI — the Brain. Isolated so a faster model is a one-line env change.
    openai_api_key: str = ""
    openai_model: str = "gpt-4.1"
    openai_timeout_seconds: float = 8.0

    # Twilio + ConversationRelay. Twilio owns audio, STT and TTS; we own the text
    # Brain. These configure the <ConversationRelay> TwiML the webhook returns.
    twilio_auth_token: str = ""
    # Reject webhooks whose X-Twilio-Signature doesn't match. Enforced whenever an
    # auth token is configured; leave the token empty only for local dev.
    twilio_validate_signature: bool = True
    conversation_relay_tts_provider: str = "ElevenLabs"
    conversation_relay_voice: str = ""
    conversation_relay_stt_provider: str = "Deepgram"
    conversation_relay_language: str = "en-US"
    # Server-side inactivity handling (ConversationRelay sends no silence events).
    voice_silence_prompt_seconds: float = 12.0
    voice_silence_hangup_seconds: float = 24.0

    # Resend — lead notification email.
    resend_api_key: str = ""
    lead_email_from: str = "receptionist@stedden.ai"
    lead_email_reply_to: str = ""

    # Admin protection (single shared token; no auth system).
    admin_api_token: str = "change-me"


@lru_cache
def get_settings() -> Settings:
    return Settings()
