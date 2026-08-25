import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from engine.claude_cli_engine import ClaudeCliEngine
from engine.open_source_engine import OpenSourceEngine
from engine.reasoning_engine import ReasoningEngine, extract_json_object


def test_reasoning_engine_is_abstract():
    with pytest.raises(TypeError):
        ReasoningEngine()  # type: ignore[abstract]


def test_open_source_engine_is_a_documented_stub():
    with pytest.raises(NotImplementedError):
        OpenSourceEngine(provider="together", model="llama-3.3-70b", api_key="x")


def test_extract_json_object_plain():
    assert extract_json_object('{"a": 1}') == {"a": 1}


def test_extract_json_object_fenced():
    text = '```json\n{"a": 1}\n```'
    assert extract_json_object(text) == {"a": 1}


def test_extract_json_object_with_surrounding_prose():
    text = 'Sure, here is the result:\n{"a": 1}\nHope that helps!'
    assert extract_json_object(text) == {"a": 1}


def test_extract_json_object_invalid_returns_none():
    assert extract_json_object("not json at all") is None


def test_claude_cli_engine_builds_correct_argv():
    engine = ClaudeCliEngine(model="claude-sonnet-4-5-20250929")
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="hello", stderr="")
        result = engine.run("say hi")

    argv = mock_run.call_args[0][0]
    assert argv[:2] == ["claude", "-p"]
    assert "say hi" in argv[2]
    assert "--model" in argv
    assert result.text == "hello"
    assert result.structured is None


def test_claude_cli_engine_appends_schema_instruction():
    engine = ClaudeCliEngine()
    schema = {"severity": "string"}
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout='{"severity": "high"}', stderr="")
        result = engine.run("classify this", output_schema=schema)

    argv = mock_run.call_args[0][0]
    assert "severity" in argv[2]  # schema shape got into the prompt
    assert result.structured == {"severity": "high"}


def test_claude_cli_engine_raises_on_nonzero_exit():
    engine = ClaudeCliEngine()
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="auth error")
        with pytest.raises(RuntimeError, match="auth error"):
            engine.run("anything")
