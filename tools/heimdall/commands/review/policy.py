"""The policy: thresholds in code, not in the model. Every question is a narrow fault check about one place
(a changed function, a file, or the change), so each answer becomes a row: the probability of the fault,
where it is, and whether it failed. A failing row blocks with the code change that answers it; an unsure one
is a note. Facts (Eitri) come first and no probability overrides them. Blast radius is computed from the
facts, not asked. Nothing here says a change is safe to merge: a person always decides that, so a review
with nothing to change ends in ``comment``, never in an approval.

A block resting on a fact stands as it is. A block resting on the judge's answers is a doubt: the verdict
names it in ``judged`` and carries in ``unblocked`` the outcome without it. In the agent pipeline it blocks
as it is; in the PR pipeline Claude verifies it, and each unsure row too (``verify.settle``)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from .base import EXIT_ESCALATE, EXIT_OK, EXIT_REQUEST_CHANGES
from .questions import Asked

FAIL, UNSURE, PASS = "fail", "unsure", "pass"

# Thresholds on the probability of a fault. Answers from the judge; policy from us. Tune against your own merges.
T = {
    "fail": 0.60,  # a yes/no check at or above → the row fails
    "unsure": 0.40,  # at or above, below "fail" → unsure: a note, and a doubt for the PR pipeline's verifier
    # The style checks are asked of every function and Jev leans yes on them: on the first live run (2026-10-10,
    # 110 functions) single purpose failed 26 small helpers at 0.62–0.85 while every fault check stayed quiet. So
    # they fail only when Jev is sure; until then they are notes, and the PR pipeline's verifier rules on them.
    "style_fail": 0.90,
    "style_unsure": 0.75,
    "choice_fail": 0.50,  # a which-file check naming a file at or above
    "choice_unsure": 0.30,
    "wide_escalate": 2,  # blast radius level (0 contained, 1 moderate, 2 wide) at or above → escalate
    # 2.7 of 3 is 90%: the floor the author set for the craft of the application's code, and it is not to be lowered.
    # It is judged file by file, each edited application file whole, so a shortfall names the file.
    "clean_target": 2.7,
}

# Per check: what a failing answer found and what an unsure one suspects, each said after the place, and the
# code change that answers it.
LABELS = {
    "mixes_concerns": ("does more than one thing", "may do more than one thing", "Split it into functions whose names say what each does."),
    "mode_parameter": (
        "has a parameter that switches its behaviour",
        "may have a parameter that switches its behaviour",
        "Replace the flag with one function per behaviour.",
    ),
    "mishandles_edge": (
        "mishandles an edge input",
        "may mishandle an edge input",
        "Find the empty, None or boundary input it gets wrong; fix it and add a failing test first.",
    ),
    "swallows_error": ("swallows an error", "may swallow an error", "Handle the exception where it arises, or let it propagate."),
    "untested": ("is changed without a test", "may be changed without a test", "Add a test that fails without this change."),
    "unsafe_input": (
        "passes unvalidated input to a shell, path, query or log",
        "may pass unvalidated input to a shell, path, query or log",
        "Validate the input at the boundary before it reaches the sink.",
    ),
    "error_not_problem": (
        "answers an error without Problem",
        "may answer an error without Problem",
        "Raise `Problem` on every error path; see harness/guides/returning-errors.md.",
    ),
    "leaks_internals": (
        "can leak internals to a client",
        "may leak internals to a client",
        "Route the error through `Problem` with a client-safe `detail`.",
    ),
    "out_of_scope": ("does not serve the task", "may not serve the task", "Move it to its own PR."),
    "misplaced": (
        "puts code where the architecture rules say it does not belong",
        "may put code where the architecture rules say it does not belong",
        "Move the code to the package the rules name for it.",
    ),
    "docs_stale": (
        "changes behaviour with no doc updated",
        "may change behaviour with no doc updated",
        "Update the guide, AGENTS.md note or README that describes the behaviour.",
    ),
}

# What answers each "what limits it most" label of a file's clean code.
_CLEAN_FIXES = {
    "duplication": "extract the logic written twice into one function",
    "dead_code": "delete the unused code, parameters and branches",
    "comments": "replace what-comments with why-comments, and add a why where the reader would ask",
    "style": "match the surrounding code's conventions",
    "error_handling": "handle each error where it arises and never swallow one",
}


@dataclass(frozen=True)
class Row:
    """One check's answer: its question key, the check, where it looked, the probability of the fault, the state."""

    key: str
    check: str
    where: str
    p: float
    state: str


@dataclass
class Verdict:
    outcome: str
    reasons: list[str]  # short, for the table, telemetry and JSON
    answers: dict[str, Any]
    model: str
    usage: dict[str, Any]
    advice: list[str] = field(default_factory=list)  # one plain sentence per reason
    files: dict[str, dict[str, Any]] = field(default_factory=dict)  # the clean-code answers per file judged whole, by path
    judged: list[int] = field(default_factory=list)  # the reasons (by index) that rest on the judge's answers, not on a fact
    unblocked: Verdict | None = None  # on request_changes: the outcome were every judged reason refuted
    verification: list[dict[str, Any]] = field(default_factory=list)  # the verifier's ruling on each doubt
    rows: list[Row] = field(default_factory=list)  # every check's answer, for the report
    unsure: list[tuple[str, str]] = field(default_factory=list)  # (reason, advice) of each unsure row: doubts, not blocks

    @property
    def exit_code(self) -> int:
        return {"comment": EXIT_OK, "request_changes": EXIT_REQUEST_CHANGES, "escalate": EXIT_ESCALATE}[self.outcome]


@dataclass(frozen=True)
class Judgment:
    """What the policy decides from: each check's row, each file's clean-code answers, and the call's record."""

    rows: list[Row]
    files: dict[str, dict[str, Any]]
    answers: dict[str, Any]
    model: str
    usage: dict[str, Any]


def _state(p: float, fail: float, unsure: float) -> str:
    return FAIL if p >= fail else UNSURE if p >= unsure else PASS


_STYLE = frozenset({"mixes_concerns", "mode_parameter"})


def _thresholds(check: str) -> tuple[float, float]:
    return (T["style_fail"], T["style_unsure"]) if check in _STYLE else (T["fail"], T["unsure"])


def _row(a: Asked, answer: dict[str, Any]) -> Row:
    """One answer as a row: a yes/no's probability of the fault at its function, or a choice's most probable file."""
    where = a.item.where if a.item else "the change"
    if answer.get("type") == "choice":
        probs = {k: float(v) for k, v in (answer.get("probabilities") or {}).items() if k != "none" and isinstance(v, (int, float))}
        if not probs:
            return Row(a.key, a.check, where, 0.0, PASS)
        named = max(probs, key=lambda k: probs[k])
        return Row(a.key, a.check, named, probs[named], _state(probs[named], T["choice_fail"], T["choice_unsure"]))
    p = answer.get("noul")
    p = float(p) if isinstance(p, (int, float)) else 0.0
    return Row(a.key, a.check, where, p, _state(p, *_thresholds(a.check)))


def evaluate(answers: dict[str, Any], asked: list[Asked]) -> list[Row]:
    """A row per question asked, in order; an unanswered one reads as a pass: a fault is what the judge found."""
    return [_row(a, answers.get(a.key) or {}) for a in asked]


def blast_radius(facts: dict[str, Any]) -> tuple[int, str]:
    """How far a mistake in this change could reach, from the facts: (0 contained · 1 moderate · 2 wide, why)."""
    contracts = facts.get("contracts_touched") or {}
    if facts.get("kernel_changed"):
        return 2, "the kernel changed, and every feature imports it"
    frozen = [name for name, c in contracts.items() if c.get("frozen")]
    if frozen:
        return 2, f"a frozen contract changed ({', '.join(frozen)})"
    if contracts or facts.get("shared_changed") or len(facts.get("slices_touched") or []) > 1:
        return 1, "a contract, the application outside the features, a shared package or several features changed"
    return 0, "contained to one feature's internals"


def _limit(answers: dict[str, Any]) -> tuple[str, float] | None:
    """A file's most probable *actionable* limit label and its probability; ``none`` is not a fix, so its runner-up is."""
    a = answers.get("clean_code_limit") or {}
    probs = {k: float(p) for k, p in (a.get("probabilities") or {}).items() if k in _CLEAN_FIXES and isinstance(p, (int, float))}
    if not probs:
        return None
    label = max(probs, key=lambda k: probs[k])
    return label, probs[label]


def clean_fix(answers: dict[str, Any]) -> str:
    """The code change that answers a file's clean-code shortfall: its limit's fix, or every fix when it named none."""
    limit = _limit(answers)
    return _CLEAN_FIXES[limit[0]] if limit else "; ".join(_CLEAN_FIXES.values())


def _limit_fix(answers: dict[str, Any]) -> str:
    limit = _limit(answers)
    if limit is None:
        return "the judge named no single limit, so: " + clean_fix(answers)
    return f"its most probable limit: {limit[0]} ({limit[1]:.2f}): {clean_fix(answers)}"


def _clean(answers: dict[str, Any]) -> float | None:
    v = (answers.get("clean_code") or {}).get("score")
    return float(v) if isinstance(v, (int, float)) else None


class Flag(Protocol):
    def __call__(self, reason: str, why: str, *, fact: bool = False) -> None: ...


def _fact_blocks(facts: dict[str, Any], flag: Flag) -> None:
    violations = facts.get("wall_violations") or []
    if violations:
        flag(
            f"Eitri found {len(violations)} wall violation(s) in the changed files",
            "The change crosses a slice wall. Each violation is listed below with the file, the line and the fix; "
            "`eitri check --root .` reproduces it locally.",
            fact=True,
        )


def fail_reason(r: Row) -> str:
    """How a failing check is named: the place, what was found there, and how sure Jev was."""
    return f"{r.where} {LABELS[r.check][0]} p={r.p:.2f}"


def unsure_reason(r: Row) -> str:
    """How an unsure check is named: the place, what it may be, and how sure Jev was."""
    return f"{r.where} {LABELS[r.check][1]} p={r.p:.2f}"


def _check_blocks(rows: list[Row], flag: Flag) -> None:
    for r in rows:
        if r.state == FAIL:
            flag(fail_reason(r), LABELS[r.check][2])


def _craft_blocks(files: dict[str, dict[str, Any]], facts: dict[str, Any], flag: Flag) -> None:
    """A file whose clean code is below the target sends the author back to that file, with what limits it."""
    target, cut = T["clean_target"], facts.get("craft_truncated") or []
    for path, answers in sorted(files.items(), key=lambda kv: _clean(kv[1]) or 3.0):
        score = _clean(answers)
        if score is None or score >= target:
            continue
        seen = ". The judge saw the file cut at the token budget, so the score rates what it saw" if path in cut else ""
        flag(
            f"clean code {score:.2f}/3 in {path} below the target {target}",
            f"The judge rates the whole file {path} {score:.2f} on 0–3 (3 is exemplary); the target is {target}. "
            f"In its answer, {_limit_fix(answers)}{seen}.",
        )


@dataclass
class _Flags:
    """The reasons flagged, their advice, and which of them rest on the judge's answers rather than a fact."""

    reasons: list[str] = field(default_factory=list)
    advice: list[str] = field(default_factory=list)
    judged: list[int] = field(default_factory=list)

    def flag(self, reason: str, why: str, *, fact: bool = False) -> None:
        if not fact:
            self.judged.append(len(self.reasons))
        self.reasons.append(reason)
        self.advice.append(why)


def _unsure(rows: list[Row]) -> list[tuple[str, str]]:
    """``(reason, advice)`` for each unsure row: what it may be, at the place, and the fix if it is."""
    return [(unsure_reason(r), LABELS[r.check][2]) for r in rows if r.state == UNSURE]


def _without_blocks(j: Judgment, facts: dict[str, Any]) -> Verdict:
    """The outcome when no check fails: ``escalate`` when a mistake would reach wide, ``comment`` otherwise, with
    what was checked and the unsure rows as notes. Either way a person merges."""
    level, why = blast_radius(facts)
    unsure = _unsure(j.rows)
    if level >= T["wide_escalate"]:
        reasons, advice = [f"wide blast radius: {why}"], ["No check failed; a person reviews the reach before merging."]
        return Verdict("escalate", reasons, j.answers, j.model, j.usage, advice, j.files, rows=j.rows, unsure=unsure)
    functions = len({r.where for r in j.rows if r.key.startswith("f")})
    counted = f"{len(j.rows)} check{'s' * (len(j.rows) != 1)} on {functions} function{'s' * (functions != 1)}"
    notes = [f"no check failed ({counted})"] + ([f"{len(unsure)} unsure, listed with the checks below"] if unsure else [])
    return Verdict("comment", notes, j.answers, j.model, j.usage, files=j.files, rows=j.rows, unsure=unsure)


def decide(j: Judgment, facts: dict[str, Any]) -> Verdict:
    """The outcome: facts and failing checks block; without them, a wide reach escalates; otherwise a comment."""
    blocks = _Flags()
    _fact_blocks(facts, blocks.flag)
    _check_blocks(j.rows, blocks.flag)
    _craft_blocks(j.files, facts, blocks.flag)
    unblocked = _without_blocks(j, facts)
    if not blocks.reasons:
        return unblocked
    return Verdict(
        "request_changes",
        blocks.reasons,
        j.answers,
        j.model,
        j.usage,
        blocks.advice,
        j.files,
        blocks.judged,
        unblocked,
        rows=j.rows,
        unsure=unblocked.unsure,
    )
