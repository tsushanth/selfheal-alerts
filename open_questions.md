# Open questions — not solved, not hidden

## No un-graduation mechanism

Once an alert type graduates to AUTO, nothing currently downgrades it back
to MANUAL if it starts acting on bad information. AUTO-mode executions
are deliberately NOT fed back into the trust store (see
`AlertOrchestrator`'s docstring) — there's no human decision to record,
and recording a synthetic "approved" outcome for every auto-execution
would inflate the approval rate for free, defeating the whole point of
the metric. But that means a real regression (the underlying issue
changes shape, the top-ranked action stops being the right one) has no
current signal to catch it. Needs an actual answer before AUTO mode is
trusted with anything that matters: outcome-based re-evaluation (did the
alert recur shortly after the auto-action ran?) is the obvious candidate,
but "outcome correlation" was explicitly not the noise signal chosen for
graduation (operator-dismissal was) — using it for de-graduation while
using something else for graduation is a real asymmetry worth thinking
through, not copying blindly.

## Trusting the model's action ordering without verification

`AlertOrchestrator._draft_actions` asks the model to order proposed
actions "safe ones first" and AUTO mode blindly executes `actions[0]`.
Nothing verifies that ordering independently. For now this is bounded by
`ActionExecutor` being `LoggingExecutor` only (nothing real executes) —
this becomes a real safety question the moment a real executor exists.

## What happens on repeated identical findings

`handle_finding` has no dedup — if the same underlying issue produces 50
findings in an hour (e.g. the Kokoro leak firing every few minutes),
today that's 50 separate alert instances, 50 sends, and (in MANUAL mode)
50 separate approval requests. This needs a real answer before deploying
against anything as noisy as what we actually saw in the Kokoro TTS case.
