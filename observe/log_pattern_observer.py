"""
LogPatternObserver — the "Observe" phase: watch a log source, turn a
known-bad pattern into a finding automatically, instead of a human
calling AlertOrchestrator.handle_finding by hand.

Deliberately narrow for a first version: substring matching against a
periodically-fetched log blob, not a full telemetry pipeline. This is the
same shape as the existing heal-daemon's `known_patterns` (log_contains_any
signatures) already running in this portfolio's own infra -- reusing a
validated pattern, not inventing a new one.

Decoupled from any specific log source: fetch_logs is an injected
callable, not hardcoded to Fly. See fly_log_fetcher() below for the real
Fly implementation, reusing the exact bounded `fly logs | tail -n 500`
command already validated in holmesgpt-toolset-flyio.

Debounced per alert_rule_id -- this is the fix for the dedup gap flagged
in open_questions.md: the same underlying issue firing every few minutes
in the logs produces at most one finding per debounce window, not one
finding per matching line.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from alerts.channel import Severity
from orchestrator.alert_orchestrator import AlertOrchestrator


@dataclass(frozen=True)
class WatchedSignature:
    alert_rule_id: str
    title: str
    match_substring: str
    severity: Severity = Severity.MEDIUM
    debounce_sec: int = 900  # don't re-fire the same alert type more than once per 15 min by default


class LogPatternObserver:
    def __init__(
        self,
        fetch_logs: Callable[[], str],
        signatures: list[WatchedSignature],
        orchestrator: AlertOrchestrator,
        debounce_state_path: Path,
    ):
        self.fetch_logs = fetch_logs
        self.signatures = signatures
        self.orchestrator = orchestrator
        self.debounce_state_path = debounce_state_path

    def poll(self) -> list[str]:
        """Fetch logs once, check every signature, fire findings for
        whichever match and aren't currently debounced. Returns the
        alert instance ids created this poll (empty if nothing fired)."""
        logs = self.fetch_logs()
        state = self._load_state()
        created: list[str] = []
        now = time.time()

        for sig in self.signatures:
            if sig.match_substring not in logs:
                continue
            last_fired = state.get(sig.alert_rule_id)
            if last_fired is not None and (now - last_fired) < sig.debounce_sec:
                continue

            matched_lines = [line for line in logs.splitlines() if sig.match_substring in line]
            excerpt = "\n".join(matched_lines[:5])
            description = f"Matched {len(matched_lines)} log line(s) in this poll, e.g.:\n{excerpt}"

            alert_id = self.orchestrator.handle_finding(
                sig.alert_rule_id, sig.title, description, sig.severity
            )
            created.append(alert_id)
            state[sig.alert_rule_id] = now

        self._save_state(state)
        return created

    def _load_state(self) -> dict[str, float]:
        if not self.debounce_state_path.exists():
            return {}
        return json.loads(self.debounce_state_path.read_text())

    def _save_state(self, state: dict[str, float]) -> None:
        self.debounce_state_path.write_text(json.dumps(state, indent=2))


def fly_log_fetcher(app: str, lines: int = 500) -> Callable[[], str]:
    """Real Fly.io log source. Reuses the exact bounded command already
    validated in holmesgpt-toolset-flyio's fly_logs_recent tool."""

    def _fetch() -> str:
        # No shell=True / string interpolation, even though `app` is
        # trusted config here -- tail the output in Python instead of
        # piping through a shell.
        result = subprocess.run(
            ["fly", "logs", "-a", app, "--no-tail"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return "\n".join(result.stdout.splitlines()[-lines:])

    return _fetch
