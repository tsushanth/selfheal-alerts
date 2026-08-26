"""
RunbookRegistry — the actual safety boundary for AUTO-mode execution.

The model NEVER gets to invent an action_id that runs. Every action a
ShellActionExecutor can execute is pre-registered here, ahead of time, by
a human — a real file a person reviews and edits, matching this whole
project's "solidify" step (observe -> propose -> human review -> THEN it
becomes real). At alert time, the model's only job is to rank/select
among the actions already registered for that alert_rule_id; anything it
returns that isn't in this registry is discarded, never executed.

This is the same shape as HolmesGPT's opt-in Kubernetes Remediation MCP:
an allowlist of specific, reviewed operations, not "run this string an
LLM produced."
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class VerifySpec:
    """How to check the action actually worked. url is polled with a plain
    GET; expected_status is what counts as healthy."""

    url: str
    expected_status: int = 200
    wait_sec: int = 10


@dataclass(frozen=True)
class RegisteredAction:
    action_id: str
    label: str
    argv: list[str]  # fixed, no template/interpolation from model output
    cwd: str | None = None
    timeout_sec: int = 60
    cooldown_sec: int = 600  # don't re-run this exact action more than once per cooldown window
    verify: VerifySpec | None = None


class RunbookRegistry:
    def __init__(self):
        self._by_rule: dict[str, list[RegisteredAction]] = {}

    def register(self, alert_rule_id: str, action: RegisteredAction) -> None:
        self._by_rule.setdefault(alert_rule_id, []).append(action)

    def get_actions(self, alert_rule_id: str) -> list[RegisteredAction]:
        return list(self._by_rule.get(alert_rule_id, []))

    def get_action(self, alert_rule_id: str, action_id: str) -> RegisteredAction | None:
        for action in self.get_actions(alert_rule_id):
            if action.action_id == action_id:
                return action
        return None
