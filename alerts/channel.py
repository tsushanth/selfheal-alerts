"""
ApprovalChannel — the pluggable interface between "an alert needs a human
decision" and whatever chat app that human actually lives in.

Deliberately narrow: one method to send an alert with candidate actions,
one method to poll for a human's response to it. Everything channel-
specific (Telegram inline keyboards, Slack Block Kit buttons, whatever
comes next) lives behind this interface so switching channels later is a
matter of writing one new file, not touching the alerting/runbook logic
that calls it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(frozen=True)
class AlertAction:
    """One candidate remediation a human can pick, e.g. from a runbook."""

    id: str
    label: str


@dataclass(frozen=True)
class AlertPayload:
    """What gets sent to a human when an alert needs a decision."""

    alert_id: str
    title: str
    description: str
    severity: Severity
    actions: list[AlertAction] = field(default_factory=list)
    # If empty, the channel should still offer a plain acknowledge option --
    # not every alert has a safe automated action attached.


@dataclass(frozen=True)
class AlertResponse:
    """What a human decided, normalized across channels."""

    alert_id: str
    action_id: str | None  # None means "acknowledged, no action taken"
    responded_by: str  # channel-specific identity (username, user id, email)


class ApprovalChannel(ABC):
    """One channel a human can receive alerts on and respond through.

    Implementations: TelegramChannel (built), SlackChannel (interface only,
    see slack_channel.py) -- adding a new channel means implementing these
    two methods, nothing else in the system needs to change.
    """

    @abstractmethod
    def send_alert(self, alert: AlertPayload) -> str:
        """Deliver an alert with its candidate actions. Returns a
        channel-specific message reference so a later response can be
        correlated back to this exact alert (e.g. a Telegram message_id)."""

    @abstractmethod
    def poll_responses(self) -> list[AlertResponse]:
        """Return any new human responses since the last poll. Polling
        (not a required webhook) is the lowest-common-denominator contract
        every channel can support -- a webhook-based implementation can
        still satisfy this by draining an internal queue."""
