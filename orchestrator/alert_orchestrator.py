"""
AlertOrchestrator — the piece that actually connects the three layers:
ReasoningEngine drafts candidate actions for a new finding, AlertTrustStore
decides whether this alert type is trusted enough to act automatically,
and ApprovalChannel is how a human sees it (always) or decides it (when
not yet trusted).

Two entry points, deliberately separate because ApprovalChannel is
poll-based, not blocking:
  - handle_finding(...): something worth alerting on just happened. Drafts
    actions, decides mode, sends and/or executes.
  - process_responses(...): call this periodically (e.g. every minute,
    same loop shape as the earlier host-memory watchdog) to drain any
    human decisions and feed them back into the trust store.

AUTO-mode executions are NOT fed back into the trust store as outcomes --
there's no human decision to record. That's a real, open gap (see
open_questions.md in this repo): a graduated alert type currently has no
mechanism to un-graduate if it starts acting on bad information. Recording
a synthetic "approved" outcome for every auto-execution would inflate the
approval rate for free and defeat the whole point of the metric.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from alerts.channel import AlertAction, AlertPayload, ApprovalChannel, Severity
from engine.reasoning_engine import ReasoningEngine
from orchestrator.action_executor import ActionExecutor
from trust.alert_trust_store import AlertOutcome, AlertTrustStore, AutonomyMode, Decision

DRAFT_ACTIONS_SCHEMA = {
    "actions": [
        {"id": "short_snake_case_id", "label": "human-readable label, e.g. 'Restart stopped machine'"}
    ]
}

DRAFT_ACTIONS_PROMPT_TEMPLATE = """A production alert just fired:

Title: {title}
Description: {description}
Severity: {severity}

Propose at most 3 candidate remediation actions, ordered by how likely \
each is to actually resolve this, safe ones first. If nothing safe and \
automatable applies, return an empty actions list -- do not invent a \
risky action just to fill the list."""


class AlertOrchestrator:
    def __init__(
        self,
        engine: ReasoningEngine,
        channel: ApprovalChannel,
        trust_store: AlertTrustStore,
        executor: ActionExecutor,
    ):
        self.engine = engine
        self.channel = channel
        self.trust_store = trust_store
        self.executor = executor
        # alert instance id -> alert_rule_id, so a later response (keyed on
        # instance) can be traced back to which rule's trust score to update.
        self._pending: dict[str, str] = {}

    def handle_finding(
        self, alert_rule_id: str, title: str, description: str, severity: Severity
    ) -> str:
        """Returns the alert instance id."""
        actions = self._draft_actions(title, description, severity)
        alert_id = str(uuid.uuid4())
        payload = AlertPayload(
            alert_id=alert_id,
            title=title,
            description=description,
            severity=severity,
            actions=actions,
        )

        mode = self.trust_store.get_autonomy_mode(alert_rule_id)
        if mode == AutonomyMode.AUTO and actions:
            top = actions[0]
            try:
                result_detail = self.executor.execute(top.id).detail
                exec_title = f"[auto-executed] {title}"
            except Exception as e:
                # An execution failure in AUTO mode must still reach a
                # human -- silently swallowing it here would be worse than
                # manual mode, not better. Falls through to a normal
                # (pending) alert rather than a bare notification, since a
                # failed auto-action still needs someone to actually look.
                result_detail = f"AUTO-mode execution of '{top.id}' FAILED: {e}"
                exec_title = f"[auto-execution FAILED] {title}"
                payload = AlertPayload(
                    alert_id=alert_id,
                    title=exec_title,
                    description=f"{description}\n\n{result_detail}",
                    severity=severity,
                    actions=actions,
                )
                self.channel.send_alert(payload)
                self._pending[alert_id] = alert_rule_id
                return alert_id

            payload = AlertPayload(
                alert_id=alert_id,
                title=exec_title,
                description=f"{description}\n\n{result_detail}",
                severity=severity,
                actions=[],  # already acted -- notification only, nothing left to approve
            )
            self.channel.send_alert(payload)
        else:
            self.channel.send_alert(payload)
            self._pending[alert_id] = alert_rule_id

        return alert_id

    def process_responses(self) -> int:
        """Drain any new human decisions, feed them into the trust store.
        Returns how many were processed."""
        responses = self.channel.poll_responses()
        count = 0
        for response in responses:
            alert_rule_id = self._pending.pop(response.alert_id, None)
            if alert_rule_id is None:
                continue  # response to something this orchestrator instance didn't send (or already processed)
            decision = Decision.APPROVED if response.action_id is not None else Decision.DISMISSED
            self.trust_store.record_outcome(
                AlertOutcome(
                    alert_rule_id=alert_rule_id,
                    decision=decision,
                    at=datetime.now(timezone.utc).isoformat(),
                )
            )
            count += 1
        return count

    def _draft_actions(self, title: str, description: str, severity: Severity) -> list[AlertAction]:
        prompt = DRAFT_ACTIONS_PROMPT_TEMPLATE.format(
            title=title, description=description, severity=severity.value
        )
        result = self.engine.run(prompt, output_schema=DRAFT_ACTIONS_SCHEMA)
        if result.structured is None or "actions" not in result.structured:
            return []
        return [
            AlertAction(id=a["id"], label=a["label"])
            for a in result.structured["actions"][:3]
            if "id" in a and "label" in a
        ]
