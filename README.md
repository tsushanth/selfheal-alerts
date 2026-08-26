# selfheal-alerts

Five pieces for the selfheal project:
- **`alerts/`** — send an alert with candidate runbook actions to a human,
  get back which action they picked (or a plain acknowledge), over
  whatever chat app they actually use.
- **`engine/`** — run a reasoning turn (plain text or structured JSON)
  against whichever model/auth path is actually answering.
- **`runbook/`** — the safety boundary: a human-curated allowlist of
  actions per alert type. The model can only rank/select among these, it
  can never invent a new one that runs.
- **`orchestrator/` + `trust/`** — connects the above: draft a selection
  from the registry for a new finding, decide (per alert type, based on a
  real approval-rate history) whether to ask a human or act
  automatically, and actually execute it (`ShellActionExecutor`, with
  per-action cooldowns and post-run verification).
- **`observe/`** — the "Observe" phase: watches a log source and turns a
  known-bad pattern into a finding automatically, instead of a human
  calling the orchestrator by hand. Debounced per alert type.

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

## runbook/ — the safety boundary

`RunbookRegistry` is a human-curated allowlist: for each `alert_rule_id`,
a fixed set of `RegisteredAction`s (id, label, fixed `argv`, cooldown, an
optional post-run HTTP health check). The model's role at alert time is
narrowed to *ranking/selecting among these* — it never gets to invent a
new action_id that runs. Anything it returns that isn't in the registry
for that alert type is silently dropped in `AlertOrchestrator` before it
ever reaches an executor. Same shape as HolmesGPT's opt-in Kubernetes
Remediation MCP: an allowlist of specific, reviewed operations, not "run
this string an LLM produced."

```python
from runbook.registry import RegisteredAction, RunbookRegistry, VerifySpec

registry = RunbookRegistry()
registry.register("kokoro_tts_timeout", RegisteredAction(
    action_id="restart_kokoro_tts_service",
    label="Restart the Kokoro TTS container on audexa-radio",
    argv=["ssh", "-i", "~/.ssh/google_compute_engine", "root@178.156.192.31",
          "cd /opt/audexa-radio && docker compose restart tts-service"],
    cooldown_sec=600,
    verify=VerifySpec(url="http://178.156.192.31:8080/health", wait_sec=15),
))
```

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

`AlertOrchestrator` ties `ReasoningEngine` (selects among registered
actions for a finding), `RunbookRegistry` (defines what's allowed),
`ApprovalChannel` (sends to a human, or just notifies once already
trusted), and `AlertTrustStore` (decides which) together, and calls
`ActionExecutor` when it's time to actually act.

```python
from pathlib import Path
from alerts.channel import Severity
from alerts.telegram_channel import TelegramChannel
from engine.claude_cli_engine import ClaudeCliEngine
from orchestrator.action_executor import ShellActionExecutor
from orchestrator.alert_orchestrator import AlertOrchestrator
from runbook.registry import RunbookRegistry
from trust.alert_trust_store import AlertTrustStore

orch = AlertOrchestrator(
    engine=ClaudeCliEngine(),
    channel=TelegramChannel(bot_token="...", chat_id="..."),
    trust_store=AlertTrustStore(Path("trust.json")),
    executor=ShellActionExecutor(cooldown_state_path=Path("cooldown.json")),
    registry=registry,  # from runbook/ above
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

`ShellActionExecutor` is the first real executor: fixed `argv` (never
built from model output), per-action cooldown enforced via a local JSON
file, a timeout, and an optional HTTP verify after running. An execution
failure in AUTO mode is never swallowed — it routes back to a human
instead, same as if the alert had never graduated.

Live-tested end to end, twice: `ClaudeCliEngine` correctly selecting the
real registered `restart_kokoro_tts_service` action for the real Kokoro
incident, and `ShellActionExecutor` running a real read-only SSH command
against the actual Hetzner host and getting real output back.

## observe/ — watching instead of waiting to be called

`LogPatternObserver` is the "Observe" phase: fetch a log source, check it
against a list of `WatchedSignature`s (substring match -> alert type),
fire a finding through the orchestrator for whatever matches — debounced
per alert type so one ongoing issue doesn't become dozens of findings.
Same shape as this portfolio's own heal-daemon `known_patterns`
(log-contains-signature matching), reusing a validated pattern rather
than inventing a new one. `fetch_logs` is injected, not hardcoded to Fly
— `fly_log_fetcher()` is the real Fly implementation, reusing the exact
bounded `fly logs | tail -n 500` already validated in
`holmesgpt-toolset-flyio`.

```python
from observe.log_pattern_observer import LogPatternObserver, WatchedSignature, fly_log_fetcher

observer = LogPatternObserver(
    fetch_logs=fly_log_fetcher("ai-radio-backend"),
    signatures=[WatchedSignature(
        alert_rule_id="kokoro_tts_timeout",
        title="Kokoro TTS timing out",
        match_substring="Kokoro TTS request timed out",
        severity=Severity.HIGH,
    )],
    orchestrator=orch,
    debounce_state_path=Path("debounce.json"),
)

observer.poll()  # call this periodically, e.g. every minute via cron
```

Live-tested against the real, currently-ongoing `ai-radio-backend`
issue — 80 matching log lines in one poll produced exactly one finding,
through the entire real chain (real Fly logs -> real Claude selection ->
real registered action -> sent alert), not a mock anywhere in the path.

See [`open_questions.md`](open_questions.md) for what's still genuinely
unsolved, including what's resolved since it was first written.
