"""Deterministic test doubles — no network, no telephony.

The scripted generator replays a plan for each turn: a list of tool calls to run
through the real Action Engine (so providers, availability, and idempotency are
genuinely exercised), followed by the final structured ``TurnResult``. This lets
the regression suite prove the system's guarantees deterministically. Live model
quality is validated by running the interactive simulator.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.errors import BrainGenerationError
from core.llm import ExecuteTool
from core.schemas import Message, TurnResult


@dataclass(slots=True)
class ToolCall:
    name: str
    args: dict


@dataclass(slots=True)
class ScriptedTurn:
    final: TurnResult
    tool_calls: list[ToolCall] = field(default_factory=list)


class ScriptedGenerator:
    def __init__(self, turns: list[ScriptedTurn]) -> None:
        self._turns = list(turns)
        self._i = 0
        self.tool_results: list[dict] = []

    async def run(
        self, *, system_prompt: str, history: list[Message], execute_tool: ExecuteTool
    ) -> str:
        if self._i >= len(self._turns):
            raise AssertionError("ScriptedGenerator ran out of scripted turns")
        turn = self._turns[self._i]
        self._i += 1
        for call in turn.tool_calls:
            self.tool_results.append(await execute_tool(call.name, call.args))
        return turn.final.model_dump_json()


class MalformedGenerator:
    async def run(self, *, system_prompt, history, execute_tool) -> str:
        return "{ this is not valid json"


class RaisingGenerator:
    async def run(self, *, system_prompt, history, execute_tool) -> str:
        raise BrainGenerationError("simulated API failure")
