"""
ClaudeCliEngine — real ReasoningEngine implementation over the Claude
Code CLI (`claude -p`). Uses the local OAuth/subscription session, not an
API key: no ANTHROPIC_API_KEY anywhere in this file. Matches this
portfolio's standing convention (`claude -p` / the OAuth broker) for
unattended workloads, instead of the metered-API pattern used in the
earlier holmesgpt-toolset-flyio/digest.py.
"""

from __future__ import annotations

import subprocess

from .reasoning_engine import EngineResult, ReasoningEngine, extract_json_object

SCHEMA_INSTRUCTION_TEMPLATE = """Respond with ONLY a single JSON object matching this shape, no prose before or after, no markdown code fence:

{schema}
"""


class ClaudeCliEngine(ReasoningEngine):
    def __init__(self, *, timeout_sec: int = 120, model: str | None = None):
        self.timeout_sec = timeout_sec
        self.model = model

    def run(self, prompt: str, *, output_schema: dict | None = None) -> EngineResult:
        full_prompt = prompt
        if output_schema is not None:
            full_prompt = f"{prompt}\n\n{SCHEMA_INSTRUCTION_TEMPLATE.format(schema=output_schema)}"

        argv = ["claude", "-p", full_prompt, "--output-format", "text"]
        if self.model:
            argv += ["--model", self.model]

        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=self.timeout_sec,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"claude -p failed (exit {result.returncode}): {result.stderr.strip()}")

        text = result.stdout.strip()
        structured = extract_json_object(text) if output_schema is not None else None
        return EngineResult(text=text, structured=structured)
