"""
ActionExecutor — runs a RegisteredAction, never a bare string.

Taking a full RegisteredAction (not an action_id string) is the point:
there is no code path anywhere in this executor that runs something the
model invented. The orchestrator resolves a model's selection against
RunbookRegistry BEFORE an executor ever sees it -- see
orchestrator/alert_orchestrator.py.

ShellActionExecutor is the first real implementation: fixed argv (no
template/interpolation from model output), per-action cooldown enforced
via a local JSON file (same auditable-by-reading pattern as
AlertTrustStore), a timeout, and an optional HTTP health check after
running to confirm the action actually helped, not just that it ran.
"""

from __future__ import annotations

import json
import subprocess
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from runbook.registry import RegisteredAction


@dataclass(frozen=True)
class ExecutionResult:
    action_id: str
    executed: bool
    detail: str


class ActionExecutor(ABC):
    @abstractmethod
    def execute(self, action: RegisteredAction) -> ExecutionResult: ...


class LoggingExecutor(ActionExecutor):
    """Never actually runs anything. Records the action it would have
    taken. Safe to wire into AUTO mode today without a real remediation
    backend behind it."""

    def __init__(self):
        self.log: list[str] = []

    def execute(self, action: RegisteredAction) -> ExecutionResult:
        self.log.append(action.action_id)
        return ExecutionResult(
            action_id=action.action_id,
            executed=False,
            detail=f"LoggingExecutor: would have executed '{action.action_id}' ({action.label}), took no real action",
        )


class CooldownActiveError(Exception):
    """Raised when an action is requested again before its cooldown has
    elapsed. The orchestrator's failure path (see AlertOrchestrator)
    already routes any exception here back to a human -- a cooldown hit
    is exactly the kind of thing a human should see, not silently retry."""


class ShellActionExecutor(ActionExecutor):
    def __init__(self, cooldown_state_path: Path):
        self.cooldown_state_path = cooldown_state_path

    def execute(self, action: RegisteredAction) -> ExecutionResult:
        self._check_cooldown(action)

        result = subprocess.run(
            action.argv,
            cwd=action.cwd,
            capture_output=True,
            text=True,
            timeout=action.timeout_sec,
            check=False,
        )
        self._record_run(action.action_id)

        if result.returncode != 0:
            raise RuntimeError(
                f"'{' '.join(action.argv)}' exited {result.returncode}: {result.stderr.strip()}"
            )

        detail = f"Ran: {' '.join(action.argv)}\n{result.stdout.strip()}"

        if action.verify is not None:
            time.sleep(action.verify.wait_sec)
            status = self._check_url(action.verify.url)
            if status != action.verify.expected_status:
                raise RuntimeError(
                    f"Action ran but verification failed: {action.verify.url} "
                    f"returned {status}, expected {action.verify.expected_status}"
                )
            detail += f"\nVerified: {action.verify.url} -> {status}"

        return ExecutionResult(action_id=action.action_id, executed=True, detail=detail)

    def _check_url(self, url: str) -> int | None:
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                return resp.status
        except urllib.error.HTTPError as e:
            return e.code
        except Exception:
            return None

    def _load_state(self) -> dict[str, float]:
        if not self.cooldown_state_path.exists():
            return {}
        return json.loads(self.cooldown_state_path.read_text())

    def _check_cooldown(self, action: RegisteredAction) -> None:
        state = self._load_state()
        last_run = state.get(action.action_id)
        if last_run is not None:
            elapsed = time.time() - last_run
            if elapsed < action.cooldown_sec:
                raise CooldownActiveError(
                    f"'{action.action_id}' ran {elapsed:.0f}s ago, "
                    f"cooldown is {action.cooldown_sec}s -- refusing to run again yet"
                )

    def _record_run(self, action_id: str) -> None:
        state = self._load_state()
        state[action_id] = time.time()
        self.cooldown_state_path.write_text(json.dumps(state, indent=2))
