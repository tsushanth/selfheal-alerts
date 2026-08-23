import json
import sys
import io
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alerts.channel import AlertAction, AlertPayload, Severity
from alerts.telegram_channel import TelegramChannel


def _fake_response(payload: dict):
    return io.BytesIO(json.dumps(payload).encode())


def test_send_alert_builds_inline_keyboard_with_actions_and_ack():
    channel = TelegramChannel(bot_token="fake-token", chat_id="12345")
    alert = AlertPayload(
        alert_id="alert-1",
        title="Kokoro TTS timing out",
        description="120s timeouts on ai-radio-backend",
        severity=Severity.HIGH,
        actions=[AlertAction(id="restart_machine", label="Restart stopped machine")],
    )

    with patch("urllib.request.urlopen") as mock_open:
        mock_open.return_value.__enter__.return_value = _fake_response(
            {"ok": True, "result": {"message_id": 42}}
        )
        message_ref = channel.send_alert(alert)

    assert message_ref == "42"
    assert channel._sent_messages["alert-1"] == 42

    sent_body = mock_open.call_args[0][0].data.decode()
    assert "chat_id=12345" in sent_body
    assert "Restart+stopped+machine" in sent_body or "Restart%20stopped%20machine" in sent_body
    assert "Acknowledge" in sent_body


def test_poll_responses_parses_action_choice():
    channel = TelegramChannel(bot_token="fake-token", chat_id="12345")

    updates_payload = {
        "ok": True,
        "result": [
            {
                "update_id": 100,
                "callback_query": {
                    "id": "cb-1",
                    "from": {"id": 999, "username": "sushanth"},
                    "data": "alert-1:restart_machine",
                },
            }
        ],
    }
    ack_payload = {"ok": True, "result": True}

    with patch("urllib.request.urlopen") as mock_open:
        mock_open.return_value.__enter__.side_effect = [
            _fake_response(updates_payload),
            _fake_response(ack_payload),
        ]
        responses = channel.poll_responses()

    assert len(responses) == 1
    assert responses[0].alert_id == "alert-1"
    assert responses[0].action_id == "restart_machine"
    assert responses[0].responded_by == "sushanth"
    assert channel._last_update_id == 100


def test_poll_responses_ack_maps_to_none_action():
    channel = TelegramChannel(bot_token="fake-token", chat_id="12345")

    updates_payload = {
        "ok": True,
        "result": [
            {
                "update_id": 101,
                "callback_query": {
                    "id": "cb-2",
                    "from": {"id": 999, "username": "sushanth"},
                    "data": "alert-1:__ack__",
                },
            }
        ],
    }
    ack_payload = {"ok": True, "result": True}

    with patch("urllib.request.urlopen") as mock_open:
        mock_open.return_value.__enter__.side_effect = [
            _fake_response(updates_payload),
            _fake_response(ack_payload),
        ]
        responses = channel.poll_responses()

    assert responses[0].action_id is None


def test_poll_responses_ignores_non_callback_updates():
    channel = TelegramChannel(bot_token="fake-token", chat_id="12345")

    updates_payload = {
        "ok": True,
        "result": [{"update_id": 200, "message": {"text": "unrelated chat message"}}],
    }

    with patch("urllib.request.urlopen") as mock_open:
        mock_open.return_value.__enter__.return_value = _fake_response(updates_payload)
        responses = channel.poll_responses()

    assert responses == []
    assert channel._last_update_id == 200


def test_api_error_raises():
    channel = TelegramChannel(bot_token="fake-token", chat_id="12345")
    alert = AlertPayload(
        alert_id="alert-1", title="x", description="y", severity=Severity.LOW
    )

    with patch("urllib.request.urlopen") as mock_open:
        mock_open.return_value.__enter__.return_value = _fake_response(
            {"ok": False, "description": "chat not found"}
        )
        try:
            channel.send_alert(alert)
            assert False, "expected RuntimeError"
        except RuntimeError as e:
            assert "chat not found" in str(e)
