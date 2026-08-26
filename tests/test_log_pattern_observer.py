import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alerts.channel import Severity
from observe.log_pattern_observer import LogPatternObserver, WatchedSignature
from orchestrator.alert_orchestrator import AlertOrchestrator
from orchestrator.action_executor import LoggingExecutor
from runbook.registry import RunbookRegistry
from trust.alert_trust_store import AlertTrustStore
from tests.test_alert_orchestrator import FakeChannel, FakeEngine

KOKORO_LOGS = """\
2026-08-26T10:00:00Z info Synthesizing segment 3/12
2026-08-26T10:00:01Z info TTS attempt 3 failed: Kokoro TTS request timed out after 120 seconds
2026-08-26T10:00:02Z info Segment 3 failed after retries, skipping
"""

CLEAN_LOGS = """\
2026-08-26T10:00:00Z info Synthesizing segment 3/12
2026-08-26T10:00:05Z info Segment 3 rendered ok
"""

SIG = WatchedSignature(
    alert_rule_id="kokoro_tts_timeout",
    title="Kokoro TTS timing out",
    match_substring="Kokoro TTS request timed out",
    severity=Severity.HIGH,
    debounce_sec=900,
)


def _observer(tmp_path, logs_text: str, signatures=None):
    engine = FakeEngine({"selected_action_ids": []})
    channel = FakeChannel()
    trust_store = AlertTrustStore(tmp_path / "trust.json")
    orch = AlertOrchestrator(engine, channel, trust_store, LoggingExecutor(), RunbookRegistry())
    observer = LogPatternObserver(
        fetch_logs=lambda: logs_text,
        signatures=signatures if signatures is not None else [SIG],
        orchestrator=orch,
        debounce_state_path=tmp_path / "debounce.json",
    )
    return observer, orch, channel


def test_matching_pattern_creates_a_finding(tmp_path):
    observer, orch, channel = _observer(tmp_path, KOKORO_LOGS)
    created = observer.poll()
    assert len(created) == 1
    assert channel.sent[0].title == "Kokoro TTS timing out"
    assert "Kokoro TTS request timed out" in channel.sent[0].description


def test_no_match_creates_nothing(tmp_path):
    observer, orch, channel = _observer(tmp_path, CLEAN_LOGS)
    created = observer.poll()
    assert created == []
    assert channel.sent == []


def test_repeated_matches_within_debounce_window_fire_only_once(tmp_path):
    """The actual dedup fix -- 500 lines with 50 matching occurrences of
    the same known issue should not produce 50 findings."""
    observer, orch, channel = _observer(tmp_path, KOKORO_LOGS)
    observer.poll()
    observer.poll()
    observer.poll()
    assert len(channel.sent) == 1


def test_debounce_is_per_alert_rule_not_global(tmp_path):
    other_sig = WatchedSignature(
        alert_rule_id="reddit_403", title="Reddit API failing", match_substring="Reddit API", severity=Severity.LOW
    )
    logs = KOKORO_LOGS + "\n2026-08-26T10:00:03Z info Reddit API also failing (403 error)"
    observer, orch, channel = _observer(tmp_path, logs, signatures=[SIG, other_sig])

    observer.poll()

    fired_rules = {s.alert_rule_id for s in [SIG, other_sig]}
    titles = {a.title for a in channel.sent}
    assert titles == {"Kokoro TTS timing out", "Reddit API failing"}


def test_debounce_expires_after_window(tmp_path, monkeypatch):
    import observe.log_pattern_observer as mod

    fake_now = [1000.0]
    monkeypatch.setattr(mod.time, "time", lambda: fake_now[0])

    short_sig = WatchedSignature(
        alert_rule_id="kokoro_tts_timeout",
        title="Kokoro TTS timing out",
        match_substring="Kokoro TTS request timed out",
        debounce_sec=10,
    )
    observer, orch, channel = _observer(tmp_path, KOKORO_LOGS, signatures=[short_sig])

    observer.poll()
    fake_now[0] += 5
    observer.poll()  # still within debounce
    assert len(channel.sent) == 1

    fake_now[0] += 10
    observer.poll()  # debounce window has passed
    assert len(channel.sent) == 2
