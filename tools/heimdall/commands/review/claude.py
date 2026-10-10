"""Claude answers the review: every question about the diff, and clean code for each application file read whole,
thinking before it answers. Each answer carries a reason, and an answer that finds a defect carries findings (a
file, a function and lines quoted from it) which ``verify`` checks before a block stands. The answers are read
into the shapes the policy reads, so the outcome is decided from Claude's; Jev's sit beside them in the report."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from .verify import VerifyError

MODEL = "claude-sonnet-5-5"
KEY_ENVS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
WORKSPACE_ENV = "ANTHROPIC_WORKSPACE_ID"  # required by a key that is not scoped to a workspace
# A refused request is retried server-side on the model Anthropic picks for the refusal's category.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

SYSTEM = """You review one change to a repository for its merge check.

Answer every question in <questions> about the change, and every question in <file_questions> about each file \
listed in <craft_files> (each is in <files>, whole). For each answer give:
- `key`: the question's key.
- `answer`: "yes" or "no" for a yes/no question; for a score, one of its criteria labels exactly; for a choice, \
one of its criteria keys exactly.
- `confidence`: from 0 to 1, how sure you are of this answer.
- `reason`: one or two sentences.
- `findings`: the code that shows a defect.

Findings are what lets an answer block the merge. When an answer says something is wrong (not safe to merge, a \
security concern, tests missing, out of scope, a wall crossed, an error bypassing Problem, internals leaking, a \
function doing several things, a wide signature, correctness below "Probably correct", a file below "Exemplary"), \
give the findings that show it: the file, the function, the line, one to five consecutive lines quoted verbatim \
from the file as evidence (no line numbers, no ellipsis), what is wrong, and the smallest fix. An answer that \
names a defect without a finding cannot block and goes to a person instead; if you cannot point at code, say so \
in the reason.

Judge the changed code, not untouched code. A function that calls one helper per step does one thing, and length \
alone is not a defect. Taste in naming or style is not a defect. `facts` were established deterministically \
(Eitri's import walls, the AST's function shapes): trust them."""

_FINDING: dict[str, Any] = {
    "type": "object",
    "properties": {k: {"type": "integer" if k == "line" else "string"} for k in ("file", "function", "line", "evidence", "problem", "fix")},
    "required": ["file", "function", "line", "evidence", "problem", "fix"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class ReviewInquiry:
    """Everything Claude reads: the task, the architecture and the facts, the questions, the diff, the files whole."""

    task: str
    facts: dict[str, Any]
    architecture: dict[str, Any]
    questions: dict[str, dict[str, Any]]
    file_questions: dict[str, dict[str, Any]]
    craft_paths: list[str]  # the application files whose clean code is asked about
    diff: list[dict[str, Any]]
    sources: dict[str, str]


@dataclass(frozen=True)
class ClaudeReview:
    answers: dict[str, dict[str, Any]]  # by question, in the policy's shapes, each with its reason and findings
    files: dict[str, dict[str, Any]]  # by path: that file's clean-code answers
    model: str
    usage: dict[str, Any]


def _answer_schema(keys: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "key": {"type": "string", "enum": keys},
            "answer": {"type": "string"},
            "confidence": {"type": "number"},
            "reason": {"type": "string"},
            "findings": {"type": "array", "items": _FINDING},
        },
        "required": ["key", "answer", "confidence", "reason", "findings"],
        "additionalProperties": False,
    }


def schema(qs: dict[str, Any], fqs: dict[str, Any]) -> dict[str, Any]:
    """The answer's shape: the diff's answers, and per craft file its own."""
    per_file = {
        "type": "object",
        "properties": {"path": {"type": "string"}, "answers": {"type": "array", "items": _answer_schema(list(fqs))}},
        "required": ["path", "answers"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {"answers": {"type": "array", "items": _answer_schema(list(qs))}, "files": {"type": "array", "items": per_file}},
        "required": ["answers", "files"],
        "additionalProperties": False,
    }


def prompt(inquiry: ReviewInquiry) -> str:
    """The user turn: the task, the architecture and facts, the questions, the diff, and the changed files whole."""
    diff = "\n\n".join(f"--- {f['path']} ({f['status']}, +{f['added']} -{f['removed']})\n{f.get('diff', '')}" for f in inquiry.diff)
    files = "\n\n".join(f'<file path="{path}">\n{text}\n</file>' for path, text in inquiry.sources.items())
    sections = {
        "task": inquiry.task or "(no task statement given)",
        "architecture": json.dumps(inquiry.architecture, indent=2),
        "facts": json.dumps(inquiry.facts, indent=2),
        "questions": json.dumps(inquiry.questions, indent=2),
        "file_questions": json.dumps(inquiry.file_questions, indent=2),
        "craft_files": "\n".join(inquiry.craft_paths) or "(none)",
        "diff": diff,
        "files": files,
    }
    return "\n\n".join(f"<{name}>\n{body}\n</{name}>" for name, body in sections.items())


def _confidence(raw: Any) -> float:
    return min(1.0, max(0.0, float(raw))) if isinstance(raw, (int, float)) else 0.5


def _shape(item: dict[str, Any], q: dict[str, Any]) -> dict[str, Any] | None:
    """One answer in the shape the policy reads; ``None`` when it is not one of the question's answers."""
    answer, conf = str(item.get("answer", "")).strip(), _confidence(item.get("confidence"))
    kept = {"reason": str(item.get("reason", "")), "findings": list(item.get("findings") or [])}
    if q["type"] == "noul" and answer.lower() in ("yes", "no"):
        return {"type": "noul", "noul": conf if answer.lower() == "yes" else 1.0 - conf, **kept}
    if q["type"] == "score" and answer in q["criteria"]:
        level = list(q["criteria"]).index(answer)
        legend = {str(i): label for i, label in enumerate(q["criteria"])}
        return {"type": "score", "score": float(level), "confidence": conf, "legend": legend, "probabilities": {str(level): conf}, **kept}
    if q["type"] == "choice" and answer in q["criteria"]:
        return {"type": "choice", "choice": answer, "probabilities": {answer: conf}, "confidence": conf, **kept}
    return None


def read_answers(items: Any, qs: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Claude's answers by question key, each in the policy's shape; an answer to no question, or not one of its
    question's answers, is left out, and the policy reads that question as unanswered."""
    out: dict[str, dict[str, Any]] = {}
    for item in items if isinstance(items, list) else []:
        q = qs.get(str(item.get("key"))) if isinstance(item, dict) else None
        shaped = _shape(item, q) if q else None
        if shaped is not None:
            out[item["key"]] = shaped
    return out


def make_client() -> Any:
    """The SDK client. A key not scoped to a workspace must name one on every request, and the SDK reads
    ``ANTHROPIC_WORKSPACE_ID`` only for profiles and workload identity, not for an ``ANTHROPIC_API_KEY``."""
    import anthropic

    workspace = os.environ.get(WORKSPACE_ENV)
    return anthropic.Anthropic(default_headers={"anthropic-workspace-id": workspace} if workspace else None)


def claude_review(inquiry: ReviewInquiry, client: Any = None) -> ClaudeReview:
    """Claude's answers to the review, streamed (thinking over the files whole can run long); ``VerifyError`` when
    it does not answer, declines, or answers out of shape."""
    import anthropic

    client = client or make_client()
    output = {"effort": "high", "format": {"type": "json_schema", "schema": schema(inquiry.questions, inquiry.file_questions)}}
    try:
        with client.beta.messages.stream(
            model=MODEL,
            max_tokens=64000,
            betas=[FALLBACK_BETA],
            fallbacks="default",
            system=SYSTEM,
            output_config=output,
            messages=[{"role": "user", "content": prompt(inquiry)}],
        ) as stream:
            message = stream.get_final_message()
    except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
        raise VerifyError(f"Claude did not answer ({type(e).__name__}: {e})") from None
    return _review(message, inquiry)


def _review(message: Any, inquiry: ReviewInquiry) -> ClaudeReview:
    if message.stop_reason == "refusal":
        raise VerifyError("Claude declined to review this change")
    if message.stop_reason == "max_tokens":
        raise VerifyError("Claude's answer was cut at max_tokens")
    text = next((b.text for b in message.content if b.type == "text"), "")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as e:
        raise VerifyError(f"Claude's answer is not JSON ({e.msg})") from None
    if not isinstance(raw, dict):
        raise VerifyError("Claude's answer is not a JSON object")
    craft = set(inquiry.craft_paths)
    files = {
        f["path"]: read_answers(f.get("answers"), inquiry.file_questions)
        for f in raw.get("files") or []
        if isinstance(f, dict) and f.get("path") in craft
    }
    usage = {"input_tokens": message.usage.input_tokens, "output_tokens": message.usage.output_tokens}
    return ClaudeReview(read_answers(raw.get("answers"), inquiry.questions), files, str(message.model), usage)
