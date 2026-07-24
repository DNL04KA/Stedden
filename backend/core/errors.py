"""Core error types. Kept trivial and framework-free."""

from __future__ import annotations


class SteddenError(Exception):
    """Base class for all Stedden domain errors."""


class BrainGenerationError(SteddenError):
    """The LLM failed to produce a usable structured turn.

    Raised by ``TurnGenerator`` implementations on API failure, timeout, refusal,
    or empty output. The Brain catches this and returns a safe fallback.
    """
