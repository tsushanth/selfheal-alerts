import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trust.alert_trust_store import AlertOutcome, AlertTrustStore, AutonomyMode, Decision


def _outcome(rule_id: str, decision: Decision, i: int) -> AlertOutcome:
    return AlertOutcome(alert_rule_id=rule_id, decision=decision, at=f"2026-08-{i:02d}T00:00:00Z")


def test_new_alert_type_stays_manual_with_no_history(tmp_path):
    store = AlertTrustStore(tmp_path / "trust.json")
    assert store.get_autonomy_mode("kokoro_tts_timeout") == AutonomyMode.MANUAL


def test_few_approvals_stay_manual_even_at_100_percent(tmp_path):
    """Luck shouldn't graduate an alert type -- min_samples matters even
    when every sample so far was approved."""
    store = AlertTrustStore(tmp_path / "trust.json", min_samples=10)
    for i in range(3):
        store.record_outcome(_outcome("kokoro_tts_timeout", Decision.APPROVED, i))
    assert store.get_autonomy_mode("kokoro_tts_timeout") == AutonomyMode.MANUAL


def test_graduates_to_auto_once_threshold_and_samples_both_clear(tmp_path):
    store = AlertTrustStore(tmp_path / "trust.json", min_samples=10, approval_rate_threshold=0.85)
    for i in range(9):
        store.record_outcome(_outcome("kokoro_tts_timeout", Decision.APPROVED, i))
    store.record_outcome(_outcome("kokoro_tts_timeout", Decision.DISMISSED, 9))
    # 9/10 = 90% approval, 10 samples -- both conditions clear
    assert store.approval_rate("kokoro_tts_timeout") == 0.9
    assert store.get_autonomy_mode("kokoro_tts_timeout") == AutonomyMode.AUTO


def test_stays_manual_when_samples_clear_but_rate_does_not(tmp_path):
    store = AlertTrustStore(tmp_path / "trust.json", min_samples=10, approval_rate_threshold=0.85)
    for i in range(6):
        store.record_outcome(_outcome("noisy_disk_alert", Decision.APPROVED, i))
    for i in range(4):
        store.record_outcome(_outcome("noisy_disk_alert", Decision.DISMISSED, i + 6))
    # 6/10 = 60% -- below threshold, this is exactly the "non-actionable, prune it" case
    assert store.get_autonomy_mode("noisy_disk_alert") == AutonomyMode.MANUAL


def test_different_alert_types_are_tracked_independently(tmp_path):
    store = AlertTrustStore(tmp_path / "trust.json", min_samples=2, approval_rate_threshold=0.5)
    store.record_outcome(_outcome("alert_a", Decision.APPROVED, 1))
    store.record_outcome(_outcome("alert_a", Decision.APPROVED, 2))
    store.record_outcome(_outcome("alert_b", Decision.DISMISSED, 1))
    store.record_outcome(_outcome("alert_b", Decision.DISMISSED, 2))

    assert store.get_autonomy_mode("alert_a") == AutonomyMode.AUTO
    assert store.get_autonomy_mode("alert_b") == AutonomyMode.MANUAL


def test_persists_across_new_store_instance(tmp_path):
    path = tmp_path / "trust.json"
    store1 = AlertTrustStore(path, min_samples=1, approval_rate_threshold=0.5)
    store1.record_outcome(_outcome("alert_a", Decision.APPROVED, 1))

    store2 = AlertTrustStore(path, min_samples=1, approval_rate_threshold=0.5)
    assert store2.get_autonomy_mode("alert_a") == AutonomyMode.AUTO
    assert len(store2.history_for("alert_a")) == 1
