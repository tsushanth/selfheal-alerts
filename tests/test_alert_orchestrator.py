import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alerts.channel import AlertPayload, AlertResponse, ApprovalChannel, Severity
from engine.reasoning_engine import EngineResult, ReasoningEngine
from orchestrator.action_executor import ActionExecutor, ExecutionResult, LoggingExecutor
from orchestrator.alert_orchestrator import AlertOrchestrator
from trust.alert_trust_store import AlertOutcome, AlertTrustStore, Decision


class FailingExecutor(ActionExecutor):
    def execute(self, action_id: str) -> ExecutionResult:
        raise RuntimeError("ssh connection refused")


class FakeEngine(ReasoningEngine):
    def __init__(self, structured_response: dict | None):
        self.structured_response = structured_response
        self.last_prompt: str | None = None

    def run(self, prompt: str, *, output_schema=None) -> EngineResult:
        self.last_prompt = prompt
        return EngineResult(text="fake", structured=self.structured_response)


class FakeChannel(ApprovalChannel):
    def __init__(self):
        self.sent: list[AlertPayload] = []
        self.queued_responses: list[AlertResponse] = []

    def send_alert(self, alert: AlertPayload) -> str:
        self.sent.append(alert)
        return alert.alert_id

    def poll_responses(self) -> list[AlertResponse]:
        responses = self.queued_responses
        self.queued_responses = []
        return responses


def _orchestrator(tmp_path, structured_response, min_samples=10, approval_rate_threshold=0.85):
    engine = FakeEngine(structured_response)
    channel = FakeChannel()
    trust_store = AlertTrustStore(
        tmp_path / "trust.json", min_samples=min_samples, approval_rate_threshold=approval_rate_threshold
    )
    executor = LoggingExecutor()
    orch = AlertOrchestrator(engine, channel, trust_store, executor)
    return orch, engine, channel, trust_store, executor


def test_new_alert_type_sends_for_manual_approval(tmp_path):
    orch, engine, channel, trust_store, executor = _orchestrator(
        tmp_path, {"actions": [{"id": "restart_machine", "label": "Restart stopped machine"}]}
    )

    alert_id = orch.handle_finding("kokoro_tts_timeout", "TTS timing out", "120s timeouts", Severity.HIGH)

    assert len(channel.sent) == 1
    assert channel.sent[0].actions[0].id == "restart_machine"
    assert orch._pending[alert_id] == "kokoro_tts_timeout"
    assert executor.log == []  # nothing executed in manual mode


def test_approved_response_records_approved_outcome(tmp_path):
    orch, engine, channel, trust_store, executor = _orchestrator(
        tmp_path, {"actions": [{"id": "restart_machine", "label": "Restart"}]}
    )
    alert_id = orch.handle_finding("kokoro_tts_timeout", "t", "d", Severity.HIGH)

    channel.queued_responses = [
        AlertResponse(alert_id=alert_id, action_id="restart_machine", responded_by="sushanth")
    ]
    processed = orch.process_responses()

    assert processed == 1
    assert trust_store.history_for("kokoro_tts_timeout")[0].decision == Decision.APPROVED
    assert alert_id not in orch._pending


def test_dismissed_response_records_dismissed_outcome(tmp_path):
    orch, engine, channel, trust_store, executor = _orchestrator(
        tmp_path, {"actions": [{"id": "restart_machine", "label": "Restart"}]}
    )
    alert_id = orch.handle_finding("noisy_alert", "t", "d", Severity.LOW)

    channel.queued_responses = [AlertResponse(alert_id=alert_id, action_id=None, responded_by="sushanth")]
    orch.process_responses()

    assert trust_store.history_for("noisy_alert")[0].decision == Decision.DISMISSED


def test_response_to_unknown_alert_id_is_ignored(tmp_path):
    orch, *_ = _orchestrator(tmp_path, {"actions": []})
    orch.channel.queued_responses = [
        AlertResponse(alert_id="not-a-real-alert", action_id="x", responded_by="sushanth")
    ]
    assert orch.process_responses() == 0


def test_graduated_alert_type_executes_automatically(tmp_path):
    orch, engine, channel, trust_store, executor = _orchestrator(
        tmp_path,
        {"actions": [{"id": "restart_machine", "label": "Restart"}]},
        min_samples=1,
        approval_rate_threshold=0.5,
    )
    trust_store.record_outcome(
        AlertOutcome(alert_rule_id="kokoro_tts_timeout", decision=Decision.APPROVED, at="2026-08-01T00:00:00Z")
    )

    alert_id = orch.handle_finding("kokoro_tts_timeout", "t", "d", Severity.HIGH)

    assert executor.log == ["restart_machine"]
    assert len(channel.sent) == 1
    assert channel.sent[0].actions == []  # notification only, nothing left to approve
    assert "[auto-executed]" in channel.sent[0].title
    assert alert_id not in orch._pending  # no response expected for an auto-executed alert


def test_graduated_alert_type_with_no_safe_actions_falls_back_to_manual(tmp_path):
    """If the engine can't propose anything safe, AUTO mode should not
    silently do nothing -- it must still reach a human."""
    orch, engine, channel, trust_store, executor = _orchestrator(
        tmp_path, {"actions": []}, min_samples=1, approval_rate_threshold=0.5
    )
    trust_store.record_outcome(
        AlertOutcome(alert_rule_id="weird_alert", decision=Decision.APPROVED, at="2026-08-01T00:00:00Z")
    )

    alert_id = orch.handle_finding("weird_alert", "t", "d", Severity.CRITICAL)

    assert executor.log == []
    assert len(channel.sent) == 1
    assert alert_id in orch._pending  # still waiting on a human


def test_failed_auto_execution_still_reaches_a_human(tmp_path):
    """A failure in AUTO mode must not be swallowed -- it needs to reach
    someone, and the alert must go back to pending since the action never
    actually happened."""
    engine = FakeEngine({"actions": [{"id": "restart_machine", "label": "Restart"}]})
    channel = FakeChannel()
    trust_store = AlertTrustStore(tmp_path / "trust.json", min_samples=1, approval_rate_threshold=0.5)
    trust_store.record_outcome(
        AlertOutcome(alert_rule_id="kokoro_tts_timeout", decision=Decision.APPROVED, at="2026-08-01T00:00:00Z")
    )
    orch = AlertOrchestrator(engine, channel, trust_store, FailingExecutor())

    alert_id = orch.handle_finding("kokoro_tts_timeout", "t", "d", Severity.HIGH)

    assert "FAILED" in channel.sent[0].title
    assert "ssh connection refused" in channel.sent[0].description
    assert channel.sent[0].actions != []  # still actionable -- the action never ran
    assert alert_id in orch._pending  # a human still needs to decide


def test_engine_failure_to_produce_structured_output_yields_no_actions(tmp_path):
    orch, engine, channel, trust_store, executor = _orchestrator(tmp_path, None)  # simulates parse failure
    orch.handle_finding("some_alert", "t", "d", Severity.MEDIUM)
    assert channel.sent[0].actions == []
