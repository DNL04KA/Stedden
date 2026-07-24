"""The context and audit records the Action Engine operates with."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.kb import KnowledgeBase
from providers.registry import ProviderSet


@dataclass(slots=True)
class ActionRecord:
    """One executed action — the audit trail (also persisted to action_log)."""

    action: str
    args: dict[str, Any]
    status: str  # success | failed | skipped
    result: dict[str, Any] = field(default_factory=dict)
    provider: str = ""
    external_ref: str | None = None
    error: str = ""


@dataclass(slots=True)
class ActionContext:
    """Everything the engine needs to fulfil actions for one call/turn."""

    call_id: str
    business_id: str
    kb: KnowledgeBase
    providers: ProviderSet
    audit: list[ActionRecord] = field(default_factory=list)
    #: Idempotency guards scoped to this call.
    _emailed: set[str] = field(default_factory=set)
