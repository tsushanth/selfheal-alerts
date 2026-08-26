"""
AlertTrustStore — the graduation mechanism: per alert type, track a real
approval-rate history and use it to decide whether that alert type acts
automatically or still needs a human every time.

This is the mechanism validated against real 2026 incident-response
practice during design: an "Augment -> Delegate" staged trust model,
where AI proposes and a human approves every action until a real track
record accumulates (industry reference point: 90 days, >85% approval
rate, working rollbacks), and only then does bounded autonomy kick in.
This is a scrappier version of the same idea, with the same shape.

Persistence is a single local JSON file, on purpose -- auditable by
reading it, no bespoke database, consistent with the rest of this
project's "the local file IS the source of truth" choices (the flyio
toolset's read-only design, the digest formatter's plain-file config).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path


class AutonomyMode(str, Enum):
    MANUAL = "manual"  # ask a human every time
    AUTO = "auto"  # act immediately, notify after


class Decision(str, Enum):
    APPROVED = "approved"  # human picked one of the proposed actions
    DISMISSED = "dismissed"  # human acknowledged with no action -- treated as a noise signal


@dataclass(frozen=True)
class AlertOutcome:
    alert_rule_id: str
    decision: Decision
    at: str  # ISO 8601 timestamp, caller-supplied (this module never calls the clock itself)


class AlertTrustStore:
    def __init__(
        self,
        path: Path,
        *,
        min_samples: int = 10,
        approval_rate_threshold: float = 0.85,
    ):
        self.path = path
        self.min_samples = min_samples
        self.approval_rate_threshold = approval_rate_threshold
        self._outcomes: list[AlertOutcome] = self._load()

    def _load(self) -> list[AlertOutcome]:
        if not self.path.exists():
            return []
        raw = json.loads(self.path.read_text())
        return [AlertOutcome(**o) for o in raw]

    def _save(self) -> None:
        self.path.write_text(json.dumps([asdict(o) for o in self._outcomes], indent=2))

    def record_outcome(self, outcome: AlertOutcome) -> None:
        self._outcomes.append(outcome)
        self._save()

    def history_for(self, alert_rule_id: str) -> list[AlertOutcome]:
        return [o for o in self._outcomes if o.alert_rule_id == alert_rule_id]

    def approval_rate(self, alert_rule_id: str) -> float | None:
        history = self.history_for(alert_rule_id)
        if not history:
            return None
        approved = sum(1 for o in history if o.decision == Decision.APPROVED)
        return approved / len(history)

    def get_autonomy_mode(self, alert_rule_id: str) -> AutonomyMode:
        """MANUAL until there's enough history AND that history clears the
        approval-rate bar. Both conditions are deliberate: few samples
        clearing the bar by luck shouldn't graduate an alert type, and a
        long history that's still noisy shouldn't either."""
        history = self.history_for(alert_rule_id)
        if len(history) < self.min_samples:
            return AutonomyMode.MANUAL
        rate = self.approval_rate(alert_rule_id)
        if rate is not None and rate >= self.approval_rate_threshold:
            return AutonomyMode.AUTO
        return AutonomyMode.MANUAL
