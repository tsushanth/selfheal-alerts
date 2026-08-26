"""
ActionExecutor — deliberately NOT a general remote-command runner.

Arbitrary command execution against real infrastructure needs its own
careful, scoped-permission design first: an allowlist of specific
actions (not "run this string"), verification after running, and ideally
something like the scoped-RBAC + network-isolated pattern HolmesGPT's
opt-in Kubernetes Remediation MCP uses. Bolting that onto this
orchestrator as a side effect would be exactly the kind of half-finished,
unsafe-by-accident implementation this project avoids.

LoggingExecutor is the only implementation for now: it records what WOULD
happen and does nothing else. That's a real, honest default -- not a
stand-in pretending to be the real thing -- and it's enough to exercise
the full orchestrator loop (including the AUTO-mode path) safely before a
real executor exists.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutionResult:
    action_id: str
    executed: bool
    detail: str


class ActionExecutor(ABC):
    @abstractmethod
    def execute(self, action_id: str) -> ExecutionResult: ...


class LoggingExecutor(ActionExecutor):
    """Never actually runs anything. Records the action it would have
    taken. Safe to wire into AUTO mode today without a real remediation
    backend behind it."""

    def __init__(self):
        self.log: list[str] = []

    def execute(self, action_id: str) -> ExecutionResult:
        self.log.append(action_id)
        return ExecutionResult(
            action_id=action_id,
            executed=False,
            detail=f"LoggingExecutor: would have executed '{action_id}', took no real action",
        )
