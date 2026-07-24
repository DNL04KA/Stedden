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


def _build_session() -> ConversationSession:
    settings = get_settings()
    kb = lumera_kb()

    data = lumera_internal_dataset()
    internal = ProviderBundle(
        customer=InMemoryCustomerProvider(data),
        scheduling=InMemorySchedulingProvider(data),
        notification=InMemoryNotificationProvider(data),
    )
    providers = resolve_providers(lumera_provider_config(), bundles={STEDDEN_INTERNAL: internal})

    from openai import AsyncOpenAI

    from core.llm import OpenAITurnGenerator

    generator = OpenAITurnGenerator(
        AsyncOpenAI(api_key=settings.openai_api_key),
        model=settings.openai_model,
        timeout=settings.openai_timeout_seconds,
    )
    return ConversationSession(kb, generator, ActionEngine(), providers)


async def _amain() -> None:
    settings = get_settings()
    if not settings.openai_api_key:
        print(f"{RED}OPENAI_API_KEY is not set. Add it to backend/.env.{RESET}", file=sys.stderr)
        raise SystemExit(1)

    session = _build_session()
    kb = session.kb
    print(f"{BOLD}Stedden simulator — {kb.name}{RESET}  {DIM}(Ctrl-D or 'quit' to end){RESET}")
    print(f"{DIM}source of truth: Stedden internal · today books against a real calendar{RESET}")

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
        if view.result.should_end_call:
            print(f"\n{DIM}— call ended —{RESET}")
            break


def main() -> None:
    _configure_logging(get_settings().log_level)
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
