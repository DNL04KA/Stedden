"""Select the Brain's LLM backend from configuration.

The rest of the system depends only on the ``TurnGenerator`` protocol, so
swapping providers is an env change. Each provider's SDK is imported lazily:
running on Gemini must not require the OpenAI package or key, and vice versa.
"""

from __future__ import annotations

from core.config import Settings, get_settings
from core.llm import TurnGenerator

GEMINI = "gemini"
OPENAI = "openai"
SUPPORTED = (GEMINI, OPENAI)


class LLMConfigurationError(RuntimeError):
    """Raised when the configured provider is unknown or missing its API key."""


def build_generator(settings: Settings | None = None) -> TurnGenerator:
    settings = settings or get_settings()
    provider = settings.llm_provider.strip().lower()

    if provider == GEMINI:
        if not settings.gemini_api_key:
            raise LLMConfigurationError(
                "LLM_PROVIDER=gemini but GEMINI_API_KEY is not set (backend/.env)"
            )
        from google import genai

        from core.llm_gemini import GeminiTurnGenerator

        return GeminiTurnGenerator(
            genai.Client(api_key=settings.gemini_api_key),
            model=settings.gemini_model,
            timeout=settings.gemini_timeout_seconds,
        )

    if provider == OPENAI:
        if not settings.openai_api_key:
            raise LLMConfigurationError(
                "LLM_PROVIDER=openai but OPENAI_API_KEY is not set (backend/.env)"
            )
        from openai import AsyncOpenAI

        from core.llm import OpenAITurnGenerator

        return OpenAITurnGenerator(
            AsyncOpenAI(api_key=settings.openai_api_key),
            model=settings.openai_model,
            timeout=settings.openai_timeout_seconds,
        )

    raise LLMConfigurationError(
        f"unknown LLM_PROVIDER '{settings.llm_provider}' (supported: {', '.join(SUPPORTED)})"
    )


def describe_provider(settings: Settings | None = None) -> str:
    """Human-readable provider/model line for CLI and logs."""
    settings = settings or get_settings()
    provider = settings.llm_provider.strip().lower()
    model = settings.gemini_model if provider == GEMINI else settings.openai_model
    return f"{provider}:{model}"
