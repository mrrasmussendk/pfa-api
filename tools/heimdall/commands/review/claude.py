"""The verifier behind ``verify.Check``: Claude (Sonnet 5.5) reads the changed files whole and rules on each of
Jev's doubts with a typed answer. Asked to refute first: a doubt it confirms must come with a place and lines
quoted from the file, which ``verify`` checks before believing it."""

from __future__ import annotations

import json
import os
from typing import Any

from .verify import Inquiry, Ruling, VerifyError, parse_rulings

MODEL = "claude-sonnet-5-5"
KEY_ENVS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
WORKSPACE_ENV = "ANTHROPIC_WORKSPACE_ID"  # required by a key that is not scoped to a workspace
# A refused request is retried server-side on the model Anthropic picks for the refusal's category.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

SYSTEM = """You verify a code reviewer's doubts about one change to a repository.

The reviewer, Jev, is a fast classifier. It answers in probabilities and never names a place, and on a large \
change it often saw only part of the diff. Each doubt below is a block it raised. A block stands only if you \
confirm it, so a wrong confirmation sends an engineer to fix code that is fine, and a wrong refutation lets a \
defect through.

For each doubt, first try to refute it from the code. Then rule:
- confirmed: you can point at changed code that has the problem. Give the file, the function, the line, and \
quote one to five consecutive lines verbatim from the file as evidence (no line numbers, no ellipsis). Say \
what is wrong and the smallest fix.
- refuted: the code does not have the problem. Say why in one or two sentences.
- undecided: the files you have are not enough to tell. Say what is missing.

How to judge the common doubts:
- "does one thing": confirm only for a function that mixes separable responsibilities (for example parsing, \
I/O and formatting in one body). An orchestrator that calls one helper per step does one thing. Length alone \
is not a defect.
- correctness: confirm only a concrete input that produces wrong behaviour, and name that input.
- tests: confirm only a changed behaviour no test exercises, and name the behaviour.
Judge the changed code, not untouched code. Taste in naming or style is not a defect. Answer one ruling per \
doubt, numbered as the doubts are."""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "rulings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "doubt": {"type": "integer"},
                    "verdict": {"type": "string", "enum": ["confirmed", "refuted", "undecided"]},
                    "reason": {"type": "string"},
                    "findings": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                k: {"type": "integer" if k == "line" else "string"}
                                for k in ("file", "function", "line", "evidence", "problem", "fix")
                            },
                            "required": ["file", "function", "line", "evidence", "problem", "fix"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["doubt", "verdict", "reason", "findings"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["rulings"],
    "additionalProperties": False,
}


def prompt(inquiry: Inquiry) -> str:
    """The user turn: the task, the doubts numbered from 1, the diff as Jev saw it, and the changed files whole."""
    doubts = "\n".join(f"{i}. {d.reason}\n   Jev's proposed fix: {d.advice}" for i, d in enumerate(inquiry.doubts, 1))
    diff = "\n\n".join(f"--- {f['path']} ({f['status']}, +{f['added']} -{f['removed']})\n{f.get('diff', '')}" for f in inquiry.diff)
    files = "\n\n".join(f'<file path="{path}">\n{text}\n</file>' for path, text in inquiry.sources.items())
    return f"<task>\n{inquiry.task or '(no task statement given)'}\n</task>\n\n<doubts>\n{doubts}\n</doubts>\n\n<diff>\n{diff}\n</diff>\n\n<files>\n{files}\n</files>"


def make_client() -> Any:
    """The SDK client. A key not scoped to a workspace must name one on every request, and the SDK reads
    ``ANTHROPIC_WORKSPACE_ID`` only for profiles and workload identity, not for an ``ANTHROPIC_API_KEY``."""
    import anthropic

    workspace = os.environ.get(WORKSPACE_ENV)
    return anthropic.Anthropic(default_headers={"anthropic-workspace-id": workspace} if workspace else None)


def claude_check(inquiry: Inquiry, client: Any = None) -> list[Ruling]:
    """One ruling per doubt from Claude; ``VerifyError`` when it does not answer, declines, or answers out of shape."""
    import anthropic

    client = client or make_client()
    try:
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            betas=[FALLBACK_BETA],
            fallbacks="default",
            system=SYSTEM,
            output_config={"effort": "high", "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": prompt(inquiry)}],
        )
    except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
        raise VerifyError(f"Claude did not answer ({type(e).__name__}: {e})") from None
    return _rulings(response, len(inquiry.doubts))


def _rulings(response: Any, count: int) -> list[Ruling]:
    if response.stop_reason == "refusal":
        raise VerifyError("Claude declined to review this change")
    if response.stop_reason == "max_tokens":
        raise VerifyError("Claude's answer was cut at max_tokens")
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        return parse_rulings(json.loads(text), count)
    except json.JSONDecodeError as e:
        raise VerifyError(f"Claude's answer is not JSON ({e.msg})") from None
