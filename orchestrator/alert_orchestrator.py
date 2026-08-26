"""
AlertOrchestrator — the piece that actually connects the layers:
RunbookRegistry defines what's ALLOWED to run for each alert type (human-
curated, ahead of time). ReasoningEngine's only job is to rank/select
among those pre-registered actions for a specific finding -- it never
gets to invent a new one. AlertTrustStore decides whether this alert type
is trusted enough to act automatically. ApprovalChannel is how a human
sees it (always) or decides it (when not yet trusted).

The model's selection is validated against the registry BEFORE anything
happens -- see _select_actions. Anything the model returns that isn't a
registered action_id for this alert_rule_id is silently dropped, not
executed and not even shown as an option. This is the actual safety
boundary; ShellActionExecutor never sees anything the model invented.

Two entry points, deliberately separate because ApprovalChannel is
poll-based, not blocking:
  - handle_finding(...): something worth alerting on just happened.
  - process_responses(...): call this periodically to drain human
    decisions and feed them back into the trust store.

AUTO-mode executions are NOT fed back into the trust store as outcomes --
there's no human decision to record. See open_questions.md for why this
is still an open gap (no un-graduation mechanism).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from alerts.channel import AlertAction, AlertPayload, ApprovalChannel, Severity
from engine.reasoning_engine import ReasoningEngine
from orchestrator.action_executor import ActionExecutor
from runbook.registry import RegisteredAction, RunbookRegistry
from trust.alert_trust_store import AlertOutcome, AlertTrustStore, AutonomyMode, Decision

SELECT_ACTIONS_PROMPT_TEMPLATE = """A production alert just fired:

Title: {title}
Description: {description}
Severity: {severity}

Here are the ONLY remediation actions available for this alert type \
(pre-approved by a human, you cannot use anything else):

{registered_actions}

Return the subset that's actually relevant here, ordered by how likely \
each is to help, safest first. If none of them apply, return an empty list."""

SELECT_ACTIONS_SCHEMA = {"selected_action_ids": ["one of the registered action ids above, in order"]}


class AlertOrchestrator:
    def __init__(
        self,
        engine: ReasoningEngine,
        channel: ApprovalChannel,
        trust_store: AlertTrustStore,
        executor: ActionExecutor,
        registry: RunbookRegistry,
    ):
        self.engine = engine
        self.channel = channel
        self.trust_store = trust_store
        self.executor = executor
        self.registry = registry
        # alert instance id -> alert_rule_id, so a later response (keyed on
        # instance) can be traced back to which rule's trust score to update.
        self._pending: dict[str, str] = {}

    def handle_finding(
        self, alert_rule_id: str, title: str, description: str, severity: Severity
    ) -> str:
        """Returns the alert instance id."""
        selected = self._select_actions(alert_rule_id, title, description, severity)
        alert_id = str(uuid.uuid4())
        payload = AlertPayload(
            alert_id=alert_id,
            title=title,
            description=description,
            severity=severity,
            actions=[AlertAction(id=a.action_id, label=a.label) for a in selected],
        )

        mode = self.trust_store.get_autonomy_mode(alert_rule_id)
        if mode == AutonomyMode.AUTO and selected:
            top = selected[0]
            try:
                result_detail = self.executor.execute(top).detail
                exec_title = f"[auto-executed] {title}"
            except Exception as e:
                # An execution failure in AUTO mode must still reach a
                # human -- silently swallowing it here would be worse than
                # manual mode, not better.
                payload = AlertPayload(
                    alert_id=alert_id,
                    title=f"[auto-execution FAILED] {title}",
                    description=f"{description}\n\nAUTO-mode execution of '{top.action_id}' FAILED: {e}",
                    severity=severity,
                    actions=payload.actions,
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
                continue
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

    def _select_actions(
        self, alert_rule_id: str, title: str, description: str, severity: Severity
    ) -> list[RegisteredAction]:
        registered = self.registry.get_actions(alert_rule_id)
        if not registered:
            return []  # nothing pre-approved for this alert type yet -- always falls to manual/no-action

        registered_list = "\n".join(f"- {a.action_id}: {a.label}" for a in registered)
        prompt = SELECT_ACTIONS_PROMPT_TEMPLATE.format(
            title=title,
            description=description,
            severity=severity.value,
            registered_actions=registered_list,
        )
        result = self.engine.run(prompt, output_schema=SELECT_ACTIONS_SCHEMA)
        if result.structured is None or "selected_action_ids" not in result.structured:
            return []

        by_id = {a.action_id: a for a in registered}
        selected: list[RegisteredAction] = []
        for action_id in result.structured["selected_action_ids"]:
            # The actual safety boundary: anything the model returns that
            # isn't a pre-registered id for THIS alert type is dropped
            # here, silently. It never reaches the channel or the executor.
            action = by_id.get(action_id)
            if action is not None:
                selected.append(action)
        return selected
