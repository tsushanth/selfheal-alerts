"""
TelegramChannel — real ApprovalChannel implementation over the Telegram
Bot API. Uses inline keyboards (one button per candidate action, plus a
plain Acknowledge) instead of free-text reply parsing -- same "click a
button" pattern Slack's Block Kit approvals use, just on Telegram.

Free, no per-app integration cap (unlike Slack's free tier), reuses the
same bot-token pattern already proven in this portfolio's OSS pipeline.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

from .channel import AlertPayload, AlertResponse, ApprovalChannel, Severity

SEVERITY_EMOJI = {
    Severity.LOW: "🔵",
    Severity.MEDIUM: "🟡",
    Severity.HIGH: "🟠",
    Severity.CRITICAL: "🚨",
}

ACK_ACTION_ID = "__ack__"


class TelegramChannel(ApprovalChannel):
    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self._api = f"https://api.telegram.org/bot{bot_token}"
        self._last_update_id = 0
        # alert_id -> chat message_id, so an incoming callback (which only
        # carries the Telegram message_id) can be mapped back to the alert.
        self._sent_messages: dict[str, int] = {}

    def send_alert(self, alert: AlertPayload) -> str:
        emoji = SEVERITY_EMOJI.get(alert.severity, "⚪")
        text = f"{emoji} <b>{alert.severity.value}</b>\n<b>{alert.title}</b>\n{alert.description}"

        keyboard = [
            [{"text": action.label, "callback_data": f"{alert.alert_id}:{action.id}"}]
            for action in alert.actions
        ]
        keyboard.append(
            [{"text": "Acknowledge (no action)", "callback_data": f"{alert.alert_id}:{ACK_ACTION_ID}"}]
        )

        body = urllib.parse.urlencode(
            {
                "chat_id": self.chat_id,
                "parse_mode": "HTML",
                "text": text,
                "reply_markup": json.dumps({"inline_keyboard": keyboard}),
            }
        ).encode()
        result = self._call("sendMessage", body)
        message_id = result["result"]["message_id"]
        self._sent_messages[alert.alert_id] = message_id
        return str(message_id)

    def poll_responses(self) -> list[AlertResponse]:
        body = urllib.parse.urlencode(
            {"offset": self._last_update_id + 1, "timeout": 0}
        ).encode()
        result = self._call("getUpdates", body)

        responses: list[AlertResponse] = []
        for update in result["result"]:
            self._last_update_id = max(self._last_update_id, update["update_id"])
            callback = update.get("callback_query")
            if not callback:
                continue

            data = callback.get("data", "")
            if ":" not in data:
                continue
            alert_id, action_id = data.split(":", 1)
            responded_by = callback["from"].get("username") or str(callback["from"]["id"])
            responses.append(
                AlertResponse(
                    alert_id=alert_id,
                    action_id=None if action_id == ACK_ACTION_ID else action_id,
                    responded_by=responded_by,
                )
            )
            self._answer_callback(callback["id"])

        return responses

    def _answer_callback(self, callback_query_id: str) -> None:
        """Stop the button showing a loading spinner in the Telegram client."""
        body = urllib.parse.urlencode({"callback_query_id": callback_query_id}).encode()
        self._call("answerCallbackQuery", body)

    def _call(self, method: str, body: bytes) -> dict:
        req = urllib.request.Request(f"{self._api}/{method}", data=body)
        with urllib.request.urlopen(req) as resp:
            result = json.load(resp)
        if not result.get("ok"):
            raise RuntimeError(f"Telegram API {method} failed: {result}")
        return result
