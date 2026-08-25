# selfheal-alerts

Two pluggable layers for the selfheal project:
- **`alerts/`** — send an alert with candidate runbook actions to a human,
  get back which action they picked (or a plain acknowledge), over
  whatever chat app they actually use.
- **`engine/`** — run a reasoning turn (plain text or structured JSON)
  against whichever model/auth path is actually answering.

Not built on HolmesGPT. That project (a separate, existing-systems
investigation — see `holmesgpt-toolset-flyio`) uses `litellm`, which is
API-key-only. This project deliberately does not depend on it or on
`litellm` — see `engine/` below.

## Why a pluggable interface

Started with Telegram (free, no per-app integration cap unlike Slack's
free tier, and reuses infra already proven in this portfolio's OSS
pipeline) but needs to support Slack later without a rewrite. The whole
system talks to `ApprovalChannel`, not to Telegram or Slack directly —
adding a channel means implementing two methods (`send_alert`,
`poll_responses`), nothing else changes.

```
alerts/
  channel.py           — the interface + shared types (AlertPayload, AlertAction, AlertResponse)
  telegram_channel.py   — real, tested implementation
  slack_channel.py       — documented stub, NotImplementedError (not a fake that pretends to work)
```

## How it works

- `send_alert(alert)` delivers the alert with one button per candidate
  action plus a plain "Acknowledge" button (not every alert has a safe
  automated action attached) — Telegram inline keyboards, same
  click-don't-type pattern Slack's Block Kit buttons use.
- `poll_responses()` returns any new human decisions since the last poll,
  normalized to `AlertResponse(alert_id, action_id | None, responded_by)`.
  Polling is the lowest-common-denominator contract; a webhook-based
  implementation (Slack's Events API) can still satisfy it by draining an
  internal queue fed by the webhook.

## Status

- `TelegramChannel`: built, unit-tested (7 tests, all against a mocked
  Telegram API — no live send has been run yet, that's the next step).
- `SlackChannel`: interface stub only. Real implementation needs Block
  Kit buttons + the Events API/interactivity webhook.

## Usage — alerts

```python
from alerts.channel import AlertPayload, AlertAction, Severity
from alerts.telegram_channel import TelegramChannel

channel = TelegramChannel(bot_token="...", chat_id="...")
channel.send_alert(AlertPayload(
    alert_id="alert-1",
    title="Kokoro TTS timing out",
    description="120s timeouts on ai-radio-backend",
    severity=Severity.HIGH,
    actions=[AlertAction(id="restart_machine", label="Restart stopped machine")],
))

# later, in a poll loop:
for response in channel.poll_responses():
    ...  # look up response.alert_id's runbook, execute response.action_id
```

## engine/ — pluggable reasoning, Claude OAuth first

Same pattern as `alerts/`: one `ReasoningEngine` interface
(`run(prompt, output_schema=None) -> EngineResult`), swap the
implementation without touching callers.

```
engine/
  reasoning_engine.py    — the interface + EngineResult + JSON extraction helper
  claude_cli_engine.py     — real implementation: `claude -p`, OAuth session, no API key
  open_source_engine.py     — documented stub (NotImplementedError) for a future LiteLLM/Ollama backend
```

`ClaudeCliEngine` runs on the local Claude Code OAuth/subscription
session — no `ANTHROPIC_API_KEY` anywhere in this file, matches this
portfolio's standing convention for unattended workloads (as opposed to
the metered-API pattern in `holmesgpt-toolset-flyio/digest.py`).

```python
from engine.claude_cli_engine import ClaudeCliEngine

engine = ClaudeCliEngine()
result = engine.run(
    "A Kokoro TTS service has been timing out at 120s for the last hour.",
    output_schema={"severity": "low|medium|high|critical", "one_line_summary": "string"},
)
result.structured  # {'severity': 'high', 'one_line_summary': '...'} -- or None if parsing failed
```

Live-tested against the real OAuth session (not mocked) — see commit
history. `OpenSourceEngine` is an interface stub only; its docstring
covers the real cost/quality trade-offs of swapping in an open-source
model later, not just "yes, trivially."

### Status

- `ClaudeCliEngine`: built, unit-tested (mocked subprocess) AND
  live-tested (real `claude -p` call, real structured JSON parsed back).
- `OpenSourceEngine`: interface stub only.
