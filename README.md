# selfheal-alerts

Three pieces for the selfheal project:
- **`alerts/`** — send an alert with candidate runbook actions to a human,
  get back which action they picked (or a plain acknowledge), over
  whatever chat app they actually use.
- **`engine/`** — run a reasoning turn (plain text or structured JSON)
  against whichever model/auth path is actually answering.
- **`orchestrator/` + `trust/`** — the piece that connects them: draft
  candidate actions for a new finding, decide (per alert type, based on a
  real approval-rate history) whether to ask a human or act
  automatically.

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

## orchestrator/ + trust/ — the connecting piece

`AlertTrustStore` is the graduation mechanism: per alert type, track a
real approval-rate history (a single local JSON file, auditable by
reading it) and use it to decide `MANUAL` vs `AUTO`. Validated against
real 2026 incident-response practice during design — an "Augment ->
Delegate" staged trust model where AI proposes and a human approves every
action until a real track record accumulates, and only then does bounded
autonomy kick in. New alert types start `MANUAL` and need both enough
samples AND a high enough approval rate to graduate — either alone isn't
enough (a few lucky approvals shouldn't graduate an alert type).

`AlertOrchestrator` ties `ReasoningEngine` (drafts candidate actions from
a finding), `ApprovalChannel` (sends to a human, or just notifies once
already trusted), and `AlertTrustStore` (decides which) together.

```python
from alerts.channel import Severity
from engine.claude_cli_engine import ClaudeCliEngine
from orchestrator.action_executor import LoggingExecutor
from orchestrator.alert_orchestrator import AlertOrchestrator
from trust.alert_trust_store import AlertTrustStore
from alerts.telegram_channel import TelegramChannel

orch = AlertOrchestrator(
    engine=ClaudeCliEngine(),
    channel=TelegramChannel(bot_token="...", chat_id="..."),
    trust_store=AlertTrustStore(Path("trust.json")),
    executor=LoggingExecutor(),  # see orchestrator/action_executor.py -- deliberately not a real executor yet
)

orch.handle_finding(
    alert_rule_id="kokoro_tts_timeout",
    title="Kokoro TTS timing out",
    description="120s timeouts on ai-radio-backend, segments failing",
    severity=Severity.HIGH,
)

# in a periodic loop:
orch.process_responses()  # drains human decisions, feeds the trust store
```

Live-tested end to end against the real Kokoro TTS incident from this
project's own history: the real model proposed `restart_kokoro_tts_service`
and `restart_ai_radio_backend` as candidate actions, unprompted, for the
exact real issue.

**`LoggingExecutor` is the only executor today, and that's deliberate** —
see `orchestrator/action_executor.py`'s docstring. Arbitrary remote
command execution needs its own careful, scoped-permission design first
(an allowlist, verification, something like HolmesGPT's opt-in
Kubernetes Remediation MCP's approval-gating + scoped RBAC), not
something bolted on as a side effect of this piece.

See [`open_questions.md`](open_questions.md) for what's genuinely
unsolved: no un-graduation mechanism, no verification of the model's own
action ordering, no dedup for repeated identical findings.
