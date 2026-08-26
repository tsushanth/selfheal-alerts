import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from orchestrator.action_executor import CooldownActiveError, ShellActionExecutor
from runbook.registry import RegisteredAction, VerifySpec


def test_runs_a_real_command_and_reports_success(tmp_path):
    executor = ShellActionExecutor(cooldown_state_path=tmp_path / "cooldown.json")
    action = RegisteredAction(action_id="echo_test", label="Echo test", argv=["echo", "hello"])

    result = executor.execute(action)

    assert result.executed is True
    assert "hello" in result.detail


def test_nonzero_exit_raises_with_stderr(tmp_path):
    executor = ShellActionExecutor(cooldown_state_path=tmp_path / "cooldown.json")
    action = RegisteredAction(
        action_id="fail_test", label="Fail test", argv=["sh", "-c", "echo boom >&2; exit 1"]
    )

    with pytest.raises(RuntimeError, match="boom"):
        executor.execute(action)


def test_cooldown_blocks_immediate_rerun(tmp_path):
    executor = ShellActionExecutor(cooldown_state_path=tmp_path / "cooldown.json")
    action = RegisteredAction(
        action_id="restart_x", label="Restart X", argv=["true"], cooldown_sec=600
    )

    executor.execute(action)
    with pytest.raises(CooldownActiveError, match="restart_x"):
        executor.execute(action)


def test_cooldown_is_per_action_not_global(tmp_path):
    executor = ShellActionExecutor(cooldown_state_path=tmp_path / "cooldown.json")
    action_a = RegisteredAction(action_id="a", label="A", argv=["true"], cooldown_sec=600)
    action_b = RegisteredAction(action_id="b", label="B", argv=["true"], cooldown_sec=600)

    executor.execute(action_a)
    executor.execute(action_b)  # must not be blocked by action_a's cooldown


def test_cooldown_state_persists_across_executor_instances(tmp_path):
    path = tmp_path / "cooldown.json"
    action = RegisteredAction(action_id="restart_x", label="Restart X", argv=["true"], cooldown_sec=600)

    ShellActionExecutor(cooldown_state_path=path).execute(action)

    with pytest.raises(CooldownActiveError):
        ShellActionExecutor(cooldown_state_path=path).execute(action)


def test_verify_failure_raises_even_though_command_succeeded(tmp_path):
    executor = ShellActionExecutor(cooldown_state_path=tmp_path / "cooldown.json")
    action = RegisteredAction(
        action_id="restart_and_check",
        label="Restart and check",
        argv=["true"],
        verify=VerifySpec(url="http://127.0.0.1:1/definitely-not-listening", wait_sec=0),
    )

    with pytest.raises(RuntimeError, match="verification failed"):
        executor.execute(action)
