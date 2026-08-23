"""
SlackChannel — NOT IMPLEMENTED. This file exists to show the shape a
second ApprovalChannel takes, so adding Slack later is "implement these
two methods" rather than a redesign.

Real Slack implementation would use Block Kit buttons (the same
"click, don't type" pattern as TelegramChannel's inline keyboard) and the
Events API / interactivity webhook instead of polling -- poll_responses()
would drain a queue fed by that webhook, satisfying the same interface.

Deliberately raising instead of a fake stub that looks like it works:
this project has a standing rule against shipping half-finished
implementations that pretend to be done.
"""

from __future__ import annotations

from .channel import AlertPayload, AlertResponse, ApprovalChannel


class SlackChannel(ApprovalChannel):
    def __init__(self, bot_token: str, channel_id: str):
        raise NotImplementedError(
            "SlackChannel is a documented interface stub, not a working implementation. "
            "See the module docstring for what a real implementation needs."
        )

    def send_alert(self, alert: AlertPayload) -> str:
        raise NotImplementedError

    def poll_responses(self) -> list[AlertResponse]:
        raise NotImplementedError
