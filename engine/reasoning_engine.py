"""
ReasoningEngine — the pluggable interface between "something needs a
model to reason about it" and whichever model/auth path actually answers.

Same shape as ApprovalChannel: one interface, swap the implementation.
ClaudeCliEngine (built) runs on Claude Code's OAuth session -- no API
key, uses subscription quota, matches the rest of this portfolio's
convention for anything that isn't a metered cron/server workload.

A LiteLLM-backed or Ollama-backed engine could implement this same
interface later for open-source models -- see the module docstring in
open_source_engine.py for what that actually requires and doesn't get
you for free.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class EngineResult:
    text: str
    # Populated only when the caller passed output_schema and the engine
    # successfully parsed a matching JSON object out of the response.
    structured: dict | None = None


class ReasoningEngine(ABC):
    @abstractmethod
    def run(self, prompt: str, *, output_schema: dict | None = None) -> EngineResult:
        """Run one reasoning turn. If output_schema is given (a JSON
        Schema-shaped dict, informal -- not validated against the JSON
        Schema spec, just used to instruct the model), the engine should
        instruct the model to respond with matching JSON and populate
        EngineResult.structured on success. structured stays None if
        parsing fails -- callers must handle that, not assume it worked."""


def extract_json_object(text: str) -> dict | None:
    """Best-effort extraction of a JSON object from model output that may
    be wrapped in prose or a markdown code fence. Returns None rather than
    raising -- a caller asking for structured output must be prepared for
    the model not to have produced valid JSON."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
