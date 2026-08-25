"""
OpenSourceEngine — NOT IMPLEMENTED. Interface stub showing what a second
ReasoningEngine looks like, for swapping in an open-source model later.

Real feasibility notes (not just "sure, trivial"):

- `claude -p` is Claude Code-specific; nothing here is portable to a
  different model by config. A real implementation would go through
  something like LiteLLM (self-hosted model server, or a hosted
  open-model API -- Together/Fireworks/Groq/DeepInfra) and construct its
  own prompt/response handling.
- Real, load-bearing caveat: open-source models (even strong ones --
  Llama 3.3 70B, Qwen2.5-72B, DeepSeek-V3) reliably lag Claude
  specifically on multi-step structured tool-calling and judgment-heavy
  tasks. Most of what this system does (deciding a real alert threshold,
  telling noise from signal) is exactly that kind of task. Swapping the
  engine is a bounded task BECAUSE this interface exists; swapping in a
  weaker model and expecting the same calibration quality is a separate,
  harder problem this interface cannot solve for you.
- Hosting: either GPU rental (proven workable earlier in this portfolio
  via Runpod) or a hosted open-model API -- both are API-key-billed, same
  cost shape as the Anthropic API this design deliberately avoided, not
  free like the Claude Code OAuth session.
"""

from __future__ import annotations

from .reasoning_engine import EngineResult, ReasoningEngine


class OpenSourceEngine(ReasoningEngine):
    def __init__(self, *, provider: str, model: str, api_key: str):
        raise NotImplementedError(
            "OpenSourceEngine is a documented interface stub, not a working implementation. "
            "See the module docstring for what a real implementation needs and its real "
            "quality/cost trade-offs versus ClaudeCliEngine."
        )

    def run(self, prompt: str, *, output_schema: dict | None = None) -> EngineResult:
        raise NotImplementedError
