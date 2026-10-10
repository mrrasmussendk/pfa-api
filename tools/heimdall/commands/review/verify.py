"""Evidence before a block. A block that rests on the reviewer's answers is a doubt, and it blocks only when it is
anchored: a named file and function, and lines quoted from that file, which this module finds in the file before
it believes them. Claude answers the review and gives its findings with each answer (``anchored``); a doubt with
no evidence, or with no review at all (no key, no answer), goes to a person. A fact (Eitri) is never put to
this test: it stands.

Why: Jev answers in probabilities and never names a place, so a block resting on it alone told the agent to
"split each function" without saying which; the agent could only guess, and a guess does not move the score."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from brokkr import estimate

from .policy import Verdict

CONFIRMED, REFUTED, UNDECIDED = "confirmed", "refuted", "undecided"
MAX_SOURCE_TOKENS = 120_000  # the changed files sent whole; the verifier reads code, not hunks


class VerifyError(Exception):
    """Claude could not answer: no response, a refusal, or an answer of the wrong shape."""


@dataclass(frozen=True)
class Doubt:
    reason: str  # the block as the policy worded it
    advice: str  # the fix it proposed
    key: str = ""  # the answer it rests on: a question, or ``file:<path>`` for one file's clean code


@dataclass(frozen=True)
class Finding:
    file: str
    function: str
    line: int
    evidence: str  # one to a few consecutive lines, verbatim from ``file``
    problem: str
    fix: str


@dataclass(frozen=True)
class Ruling:
    verdict: str  # CONFIRMED | REFUTED | UNDECIDED
    reason: str
    findings: tuple[Finding, ...] = ()


@dataclass(frozen=True)
class Evidence:
    """What the verifier reads besides the doubts: the task, the diff as Jev saw it, the changed files whole."""

    task: str
    diff: list[dict[str, Any]]
    sources: dict[str, str]


@dataclass(frozen=True)
class Inquiry:
    task: str
    doubts: tuple[Doubt, ...]
    diff: list[dict[str, Any]]
    sources: dict[str, str]


Check = Callable[[Inquiry], list[Ruling]]  # one ruling per doubt, in order; raises VerifyError when it cannot rule


def read_sources(cwd: str, paths: list[str], budget: int = MAX_SOURCE_TOKENS) -> dict[str, str]:
    """The files at ``paths``, whole, in that order, while they fit ``budget``; a missing or binary file is skipped."""
    sources: dict[str, str] = {}
    for path in paths:
        try:
            text = Path(cwd, path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        cost = estimate(text)
        if cost <= budget:
            sources[path] = text
            budget -= cost
    return sources


def grounded(finding: Finding, sources: dict[str, str]) -> Finding | None:
    """The finding with ``line`` set to where its evidence starts in the file, or ``None`` when the evidence is
    empty or not in the file: a confirmation the code does not bear out is not believed."""
    quoted = [line.strip() for line in finding.evidence.splitlines() if line.strip()]
    text = sources.get(finding.file)
    if not quoted or text is None:
        return None
    lines = [line.strip() for line in text.splitlines()]
    for start in range(len(lines)):
        if _quoted_at(lines, start, quoted):
            return replace(finding, line=start + 1)
    return None


def _quoted_at(lines: list[str], start: int, quoted: list[str]) -> bool:
    """Whether ``quoted`` are the non-blank lines of the file from ``start`` on."""
    rest = [line for line in lines[start:] if line][: len(quoted)]
    return bool(lines[start]) and rest == quoted


def _findings(raw: Any) -> tuple[Finding, ...]:
    """The findings an answer carries, skipping any of the wrong shape."""
    found = []
    for item in raw if isinstance(raw, list) else []:
        try:
            found.append(Finding(**item))
        except TypeError:
            continue
    return tuple(found)


def anchored(answers: dict[str, Any], files: dict[str, dict[str, Any]]) -> Check:
    """Rulings from Claude's own answers: a block stands on the findings of the answer it rests on. Claude gives
    its evidence with the answer, so no second call is made; a block whose answer gave none goes to a person."""

    def answer_for(key: str) -> dict[str, Any]:
        if key.startswith("file:"):
            return (files.get(key.removeprefix("file:")) or {}).get("clean_code") or {}
        return answers.get(key) or {}

    def rule(doubt: Doubt) -> Ruling:
        a = answer_for(doubt.key)
        found, reason = _findings(a.get("findings")), str(a.get("reason") or "no reason given")
        return Ruling(CONFIRMED, reason, found) if found else Ruling(UNDECIDED, f"no evidence given: {reason}")

    return lambda inquiry: [rule(d) for d in inquiry.doubts]


def _rule(check: Check | None, inquiry: Inquiry) -> list[Ruling]:
    """The rulings with every confirmation checked against the code; undecided for all when there is no verifier
    or it could not rule."""
    if check is None:
        return [Ruling(UNDECIDED, "Claude did not review: set ANTHROPIC_API_KEY so Claude answers the review")] * len(inquiry.doubts)
    try:
        rulings = check(inquiry)
    except VerifyError as e:
        return [Ruling(UNDECIDED, f"the verifier could not rule: {e}")] * len(inquiry.doubts)
    return [_believed(r, inquiry.sources) for r in rulings]


def _believed(ruling: Ruling, sources: dict[str, str]) -> Ruling:
    if ruling.verdict != CONFIRMED:
        return ruling
    kept = tuple(f for f in (grounded(f, sources) for f in ruling.findings) if f is not None)
    if not kept:
        return Ruling(UNDECIDED, f"Claude confirmed it, but its evidence is not in the files: {ruling.reason}")
    return replace(ruling, findings=kept)


def _where(r: Ruling) -> str:
    return " ".join(f"{f.file}:{f.line} {f.function}: {f.problem}. Fix: {f.fix}." for f in r.findings)


def _record(doubts: list[Doubt], rulings: list[Ruling]) -> list[dict[str, Any]]:
    return [
        {"doubt": d.reason, "verdict": r.verdict, "reason": r.reason, "findings": [asdict(f) for f in r.findings]}
        for d, r in zip(doubts, rulings, strict=True)
    ]


def settle(v: Verdict, check: Check | None, evidence: Evidence) -> Verdict:
    """Jev's verdict once the verifier has ruled on each of its judged blocks: facts and confirmed doubts block,
    undecided doubts escalate, and with every doubt refuted the outcome is the one Jev gives without them."""
    if v.outcome != "request_changes" or not v.judged or v.unblocked is None:
        return v
    doubts = [Doubt(v.reasons[i], v.advice[i], v.keys[i]) for i in v.judged]
    rulings = _rule(check, Inquiry(evidence.task, tuple(doubts), evidence.diff, evidence.sources))
    record = _record(doubts, rulings)
    facts = [(r, a) for i, (r, a) in enumerate(zip(v.reasons, v.advice, strict=True)) if i not in v.judged]
    confirmed = [(d.reason, _where(r)) for d, r in zip(doubts, rulings, strict=True) if r.verdict == CONFIRMED]
    if facts or confirmed:
        reasons, advice = zip(*(facts + confirmed), strict=True)
        return replace(v, reasons=list(reasons), advice=list(advice), judged=[], unblocked=None, verification=record)
    undecided = [
        (f"unverified: {d.reason}", f"{r.reason}. Proposed fix: {d.advice}")
        for d, r in zip(doubts, rulings, strict=True)
        if r.verdict == UNDECIDED
    ]
    if undecided:
        rest = list(zip(v.unblocked.reasons, v.unblocked.advice, strict=False)) if v.unblocked.outcome == "escalate" else []
        reasons, advice = zip(*(undecided + rest), strict=True)
        return replace(v, outcome="escalate", reasons=list(reasons), advice=list(advice), judged=[], unblocked=None, verification=record)
    return replace(v.unblocked, verification=record)
