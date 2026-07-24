"""Text-only simulator. Talk to the AI front desk with your keyboard — no phone.

    uv run stedden-sim

Runs on Stedden-internal providers (in-memory) so you can actually book,
reschedule, and cancel against a real availability engine. After every turn it
prints the assistant reply, the FSM state, the running lead, the grounded flag,
and every business action the Action Engine executed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys

import structlog

from core.config import get_settings
from engine.engine import ActionEngine
from providers.internal import (
    InMemoryCustomerProvider,
    InMemoryNotificationProvider,
    InMemorySchedulingProvider,
)
from providers.registry import STEDDEN_INTERNAL, ProviderBundle, resolve_providers
from seed.lumera import lumera_internal_dataset, lumera_kb, lumera_provider_config
from simulator.session import ConversationSession, TurnView

RESET, BOLD, DIM, CYAN, GREEN, YELLOW, RED = (
    "\033[0m",
    "\033[1m",
    "\033[2m",
    "\033[36m",
    "\033[32m",
    "\033[33m",
    "\033[31m",
)


def _configure_logging(level: str) -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stderr, level=level.upper())
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level.upper())),
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
    )


def _print_turn(view: TurnView) -> None:
    r = view.result
    grounded = f"{GREEN}true{RESET}" if r.grounded else f"{RED}false{RESET}"
    print(f"\n{BOLD}{CYAN}Assistant:{RESET} {r.say}")
    print(f"  {DIM}state:{RESET}    {YELLOW}{view.state.value}{RESET}")
    print(f"  {DIM}grounded:{RESET} {grounded}")
    for a in view.actions:
        color = GREEN if a.status == "success" else (YELLOW if a.status == "skipped" else RED)
        detail = a.error or json.dumps(a.result)[:80]
        line = f"{color}{a.action} · {a.status}{RESET} ({a.provider})"
        print(f"  {DIM}action:{RESET}   {line} {DIM}{detail}{RESET}")
    print(f"  {DIM}lead:{RESET}     {json.dumps(view.lead.model_dump(mode='json'))}")


def _generator():
    settings = get_settings()
    from openai import AsyncOpenAI

    from core.llm import OpenAITurnGenerator

    return OpenAITurnGenerator(
        AsyncOpenAI(api_key=settings.openai_api_key),
        model=settings.openai_model,
        timeout=settings.openai_timeout_seconds,
    )


def _build_session() -> ConversationSession:
    kb = lumera_kb()
    data = lumera_internal_dataset()
    internal = ProviderBundle(
        customer=InMemoryCustomerProvider(data),
        scheduling=InMemorySchedulingProvider(data),
        notification=InMemoryNotificationProvider(data),
    )
    providers = resolve_providers(lumera_provider_config(), bundles={STEDDEN_INTERNAL: internal})
    return ConversationSession(kb, _generator(), ActionEngine(), providers)


async def _build_persistent_session() -> tuple[ConversationSession, PersistHooks]:
    """SQL providers + call/transcript/action-log persistence in Postgres."""
    from zoneinfo import ZoneInfo

    from db.base import get_sessionmaker
    from db.repository import add_message, create_call
    from providers.sql_internal import (
        SqlCustomerProvider,
        SqlNotificationProvider,
        SqlSchedulingProvider,
    )
    from seed.seed_db import ensure_seeded

    kb = lumera_kb()
    sm = get_sessionmaker()
    await ensure_seeded(sm)

    tz = ZoneInfo(kb.timezone)
    internal = ProviderBundle(
        customer=SqlCustomerProvider(sm, kb.business_id, tz),
        scheduling=SqlSchedulingProvider(sm, kb.business_id, tz),
        notification=SqlNotificationProvider(sm, kb.business_id, tz),
    )
    providers = resolve_providers(lumera_provider_config(), bundles={STEDDEN_INTERNAL: internal})

    call_id = await create_call(sm, business_id=kb.business_id)

    async def sink(role: str, content: str, state, seq: int) -> None:
        await add_message(sm, call_id=call_id, seq=seq, role=role, content=content, state=state)

    session = ConversationSession(
        kb, _generator(), ActionEngine(), providers, call_id=str(call_id), message_sink=sink
    )
    return session, PersistHooks(sm=sm, business_id=kb.business_id, call_id=call_id)


class PersistHooks:
    """Per-turn action-log persistence + call completion for the CLI loop."""

    def __init__(self, sm, business_id: str, call_id) -> None:
        self._sm = sm
        self._business_id = business_id
        self.call_id = call_id
        self._seq = 0
        self._audit_len = 0

    async def after_turn(self, view: TurnView) -> None:
        from db.repository import save_action_records

        self._seq = await save_action_records(
            self._sm,
            business_id=self._business_id,
            call_id=self.call_id,
            records=view.actions,
            start_seq=self._seq,
        )

    async def on_end(self) -> None:
        from db.repository import mark_call_completed

        await mark_call_completed(self._sm, call_id=self.call_id)


async def _amain(persist: bool) -> None:
    settings = get_settings()
    if not settings.openai_api_key:
        print(f"{RED}OPENAI_API_KEY is not set. Add it to backend/.env.{RESET}", file=sys.stderr)
        raise SystemExit(1)

    hooks: PersistHooks | None = None
    if persist:
        session, hooks = await _build_persistent_session()
    else:
        session = _build_session()

    kb = session.kb
    print(f"{BOLD}Stedden simulator — {kb.name}{RESET}  {DIM}(Ctrl-D or 'quit' to end){RESET}")
    mode = (
        "Postgres (call, transcript, customers, appointments, action log)"
        if persist
        else "in-memory"
    )
    print(f"{DIM}source of truth: Stedden internal · persistence: {mode}{RESET}")
    if hooks is not None:
        print(f"{DIM}call_id: {hooks.call_id}{RESET}")

    greeting = await session.open_with_greeting()
    print(f"\n{BOLD}{CYAN}Assistant:{RESET} {greeting}")

    while True:
        try:
            user_text = (await asyncio.to_thread(input, f"\n{BOLD}You:{RESET} ")).strip()
        except (EOFError, KeyboardInterrupt):
            user_text = "I have to go, goodbye."
            print(user_text)
        if not user_text:
            continue
        if user_text.lower() in {"quit", "exit"}:
            break

        view = await session.send(user_text)
        _print_turn(view)
        if hooks is not None:
            await hooks.after_turn(view)
        if view.result.should_end_call:
            print(f"\n{DIM}— call ended —{RESET}")
            break

    if hooks is not None:
        await hooks.on_end()
        print(f"{DIM}call {hooks.call_id} marked completed in Postgres{RESET}")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Stedden text simulator")
    parser.add_argument(
        "--persist",
        action="store_true",
        help="use SQL providers and persist call, transcript, and action log to Postgres",
    )
    args = parser.parse_args()
    _configure_logging(get_settings().log_level)
    asyncio.run(_amain(args.persist))


if __name__ == "__main__":
    main()
