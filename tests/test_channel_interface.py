import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from alerts.channel import ApprovalChannel
from alerts.slack_channel import SlackChannel


def test_approval_channel_is_abstract():
    with pytest.raises(TypeError):
        ApprovalChannel()  # type: ignore[abstract]


def test_slack_channel_is_a_documented_stub_not_a_silent_fake():
    with pytest.raises(NotImplementedError):
        SlackChannel(bot_token="x", channel_id="y")
