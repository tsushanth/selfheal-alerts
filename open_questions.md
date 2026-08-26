# Open questions — not solved, not hidden

## Resolved since first written

**"Trusting the model's action ordering without verification"** — resolved
by `RunbookRegistry`: the model can only ever select among human-curated,
pre-registered actions for a given alert type; anything it returns that
isn't registered is silently dropped before it ever reaches an executor.
`ShellActionExecutor` never sees anything the model invented. Still true
that AUTO mode blindly executes the model's *first* selection among the
registered options — that's a narrower, more acceptable risk than
executing an invented action, but worth naming: the model still picks
which registered action goes first.

**"What happens on repeated identical findings"** — resolved for the
Observer path by `LogPatternObserver`'s per-`alert_rule_id` debounce
(default 15 min): live-tested against `ai-radio-backend`'s real, ongoing
Kokoro TTS issue — 80 matching log lines in one poll produced exactly one
finding, not 80. Not resolved for `handle_finding` called directly by
something other than `LogPatternObserver` — any other caller still has
no built-in dedup of its own.

## Still open

### No un-graduation mechanism

Once an alert type graduates to AUTO, nothing currently downgrades it
back to MANUAL if it starts acting on bad information. AUTO-mode
executions are deliberately NOT fed back into the trust store — there's
no human decision to record, and recording a synthetic "approved" outcome
for every auto-execution would inflate the approval rate for free,
defeating the whole point of the metric. But that means a real regression
(the underlying issue changes shape, the top-ranked registered action
stops being the right one) has no current signal to catch it.
Outcome-based re-evaluation (did the alert recur shortly after the
auto-action ran?) is the obvious candidate, but "outcome correlation" was
explicitly not the noise signal chosen for graduation (operator-dismissal
was) — using it for de-graduation while using something else for
graduation is a real asymmetry worth thinking through, not copying
blindly.

### The registry itself has to be trustworthy

`RunbookRegistry` is the entire safety boundary — it's only as safe as
what a human actually registers into it. Nothing in this codebase
prevents someone from registering a genuinely destructive `argv` for a
"safe-sounding" `action_id`. That's a process problem, not a code
problem, but worth stating plainly rather than implying the registry
itself makes bad actions impossible.

### Observer coverage is one signal source, substring-only

`LogPatternObserver` does plain substring matching against one log
source at a time. No regex, no cross-source correlation (the kind of
"stopped machine + Reddit 403 in the same window" pattern a real Holmes
investigation found but this Observer can't), no metrics- or
traces-based triggers at all yet — those were explicitly the target
scope ("everything: logs+metrics+traces") from the original design
conversation and this only covers the logs slice of it.
