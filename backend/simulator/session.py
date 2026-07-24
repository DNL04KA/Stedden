"""ConversationSession — the turn loop, independent of telephony.

Both the interactive CLI and the regression tests drive this. It owns the running
state, the running lead (accumulated across turns), and history, and it connects
the Brain (via the orchestrator) to the Business Action Engine. One
``ActionContext`` lives for the whole call, so idempotency guards and the action
audit trail accumulate correctly.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from core.kb import KnowledgeBase
from core.llm import TurnGenerator
from core.orchestrator import run_turn
from core.schemas import ConversationState, Lead, Message, TurnResult, merge_lead
from core.states import INITIAL_STATE
from engine.context import ActionContext, ActionRecord
from engine.engine import ActionEngine
from providers.registry import ProviderSet

MessageSink = Callable[[str, str, ConversationState, int], Awaitable[None]]


@dataclass(slots=True)
class TurnView:
    result: TurnResult  # result.lead is the merged running lead
    actions: list[ActionRecord]
    state: ConversationState
    lead: Lead


class ConversationSession:
    def __init__(
        self,
        kb: KnowledgeBase,
        generator: TurnGenerator,
        engine: ActionEngine,
        providers: ProviderSet,
        *,
        call_id: str | None = None,
        message_sink: MessageSink | None = None,
    ) -> None:
        self.kb = kb
        self._generator = generator
        self._engine = engine
        self.call_id = call_id or f"sim-{uuid.uuid4()}"
        self._sink = message_sink

        self.state: ConversationState = INITIAL_STATE
        self.lead = Lead()
        self.history: list[Message] = []
        self._seq = 0
        self._ctx = ActionContext(
            call_id=self.call_id,
            business_id=kb.business_id,
            kb=kb,
            providers=providers,
        )

    async def open_with_greeting(self) -> str:
        greeting = self.kb.greeting
        self.history.append(Message(role="assistant", content=greeting))
        await self._record("assistant", greeting)
        return greeting

    async def send(self, user_text: str) -> TurnView:
        self.history.append(Message(role="user", content=user_text))
        await self._record("user", user_text)

        outcome = await run_turn(
            kb=self.kb,
            history=self.history,
            state=self.state,
            lead=self.lead,
            generator=self._generator,
            engine=self._engine,
            ctx=self._ctx,
        )

        self.lead = merge_lead(self.lead, outcome.result.lead)
        self.state = outcome.result.state
        self.history.append(Message(role="assistant", content=outcome.result.say))
        await self._record("assistant", outcome.result.say)

        merged = outcome.result.model_copy(update={"lead": self.lead})
        return TurnView(result=merged, actions=outcome.actions, state=self.state, lead=self.lead)

    async def _record(self, role: str, content: str) -> None:
        seq = self._seq
        self._seq += 1
        if self._sink is not None:
            await self._sink(role, content, self.state, seq)
