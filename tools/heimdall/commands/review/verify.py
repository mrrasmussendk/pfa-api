"""The second opinion: Jev triages, Claude verifies. A block that rests on Jev's answers is a doubt, and a doubt
blocks only when the verifier confirms it with evidence: a named file and function, and lines quoted from that
file, which this module finds in the file before it believes them. A refuted doubt falls away; one the verifier
cannot decide, or that it was never asked about (no key, no answer), goes to a person. A fact (Eitri) is never
put to the verifier: it stands.

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
    """The verifier could not rule: no answer, a refusal, or an answer of the wrong shape."""


@dataclass(frozen=True)
class Doubt:
    reason: str  # the block as Jev's policy worded it
    advice: str  # the fix it proposed


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


def parse_rulings(raw: Any, count: int) -> list[Ruling]:
    """The verifier's answer as rulings, one per doubt in order; ``VerifyError`` when it is not that."""
    items = raw.get("rulings") if isinstance(raw, dict) else None
    if not isinstance(items, list) or len(items) != count:
        raise VerifyError(f"expected {count} ruling(s), got {raw!r:.200}")
    try:
        ordered = sorted(items, key=lambda r: int(r["doubt"]))
        rulings = [Ruling(str(r["verdict"]), str(r["reason"]), tuple(Finding(**f) for f in r["findings"])) for r in ordered]
    except (KeyError, TypeError, ValueError) as e:
        raise VerifyError(f"a ruling of the wrong shape ({e})") from None
    if any(r.verdict not in (CONFIRMED, REFUTED, UNDECIDED) for r in rulings):
        raise VerifyError("a verdict other than confirmed, refuted or undecided")
    return rulings


def _rule(check: Check | None, inquiry: Inquiry) -> list[Ruling]:
    """The rulings with every confirmation checked against the code; undecided for all when there is no verifier
    or it could not rule."""
    if check is None:
        return [
            Ruling(UNDECIDED, "Claude did not verify: it runs in the PR pipeline (heimdall review --claude, with ANTHROPIC_API_KEY)")
        ] * len(inquiry.doubts)
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
    return " ".join(f"{f.file}:{f.line} {f.function}: {f.problem.rstrip('.')}. Fix: {f.fix.rstrip('.')}." for f in r.findings)


def _record(doubts: list[Doubt], rulings: list[Ruling]) -> list[dict[str, Any]]:
    return [
        {"doubt": d.reason, "verdict": r.verdict, "reason": r.reason, "findings": [asdict(f) for f in r.findings]}
        for d, r in zip(doubts, rulings, strict=True)
    ]


def _doubts(v: Verdict) -> tuple[list[Doubt], list[Doubt]]:
    """The checks to verify: those that failed (they block unless refuted) and those Jev was unsure of (they
    block only if confirmed). Facts are never among them."""
    failing = [Doubt(v.reasons[i], v.advice[i]) for i in v.judged] if v.outcome == "request_changes" else []
    return failing, [Doubt(reason, advice) for reason, advice in v.unsure]


def _facts_of(v: Verdict) -> list[tuple[str, str]]:
    """The blocks that rest on a fact, kept whatever the verifier says."""
    if v.outcome != "request_changes":
        return []
    return [(r, a) for i, (r, a) in enumerate(zip(v.reasons, v.advice, strict=True)) if i not in v.judged]


def settle(v: Verdict, check: Check | None, evidence: Evidence) -> Verdict:
    """Jev's verdict once the verifier has ruled on each failing and unsure check: facts and confirmed checks
    block, an undecided failing check escalates, and with nothing confirmed the outcome is the one without them."""
    failing, unsure = _doubts(v)
    if not failing and not unsure:
        return v
    doubts = failing + unsure
    rulings = _rule(check, Inquiry(evidence.task, tuple(doubts), evidence.diff, evidence.sources))
    record = _record(doubts, rulings)
    confirmed = [(d.reason, _where(r)) for d, r in zip(doubts, rulings, strict=True) if r.verdict == CONFIRMED]
    blocks = _facts_of(v) + confirmed
    if blocks:
        reasons, advice = zip(*blocks, strict=True)
        return replace(
            v, outcome="request_changes", reasons=list(reasons), advice=list(advice), judged=[], unblocked=None, verification=record
        )
    base = v.unblocked or v
    undecided = [
        (f"unverified: {d.reason}", f"{r.reason}. Proposed fix: {d.advice}")
        for d, r in zip(failing, rulings, strict=False)
        if r.verdict == UNDECIDED
    ]
    if undecided:
        rest = list(zip(base.reasons, base.advice, strict=False)) if base.outcome == "escalate" else []
        reasons, advice = zip(*(undecided + rest), strict=True)
        return replace(v, outcome="escalate", reasons=list(reasons), advice=list(advice), judged=[], unblocked=None, verification=record)
    return replace(base, verification=record)
