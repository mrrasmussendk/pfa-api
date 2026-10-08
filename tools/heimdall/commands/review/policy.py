"""The policy: thresholds in code, not in the model. Facts (Eitri, the AST) come first and no
probability overrides them; then the judge's answers, in the order block → escalate → approve → comment.

Every reason carries advice for the human reading the PR comment: the evidence it rests on (which
answers, which facts) and the one concrete next action. A reason never says "see the other rows";
it names them. Every ``request_changes`` reason names a code change that answers it; a doubt no code change
can answer is never a block — it requires a human (``escalate``). The reason itself is short, for the
table, telemetry and JSON."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .base import EXIT_ESCALATE, EXIT_OK, EXIT_REQUEST_CHANGES
from .state import shape_line

# Thresholds. Probabilities from Jev; policy from us. Tune against your own merge history.
T = {
    "security_block": 0.50,  # has_security_concern >= → request_changes
    "safe_block": 0.35,  # safe_to_merge <= → request_changes
    "walls_block": 0.40,  # respects_slice_walls <= → request_changes
    "architecture_risk_block": 0.80,  # biggest_risk == architecture with p >= → request_changes (overrides a Yes on walls)
    "wide_refactor_escalate": 1.5,  # ... unless change_kind == refactor and expected blast_radius (0..2) >= → escalate: nothing to fix, a human reviews the layout
    "correctness_block": 1.25,  # expected score on 0..3 <= → request_changes
    "purpose_block": 0.40,  # single_purpose <= while facts.function_shape names a function over the line limit → request_changes
    "signatures_block": 0.40,  # lean_signatures <= while facts.function_shape names a signature over the parameter limit → request_changes
    "human_escalate": 0.60,  # needs_human_review >= → escalate
    "uncertain_escalate": 0.15,  # |safe_to_merge - 0.5| < → escalate (the model is on the fence)
    "safe_approve": 0.75,
    "human_approve": 0.35,
    "security_approve": 0.20,
    "tests_approve": 0.50,
    "readability_target": 2.5,  # expected readability on 0..3 below → request_changes when code changed; the agent goes back
    "clean_target": 2.8,  # expected clean_code on 0..3 below → request_changes when code changed
    "purpose_approve": 0.50,  # single_purpose >=
    "signatures_approve": 0.50,  # lean_signatures >=
}

# What answers each "what limits it most" label in code. An unanswered label falls back to the whole
# list, so the advice still names a code change.
_READABILITY_FIXES = {
    "names": "rename what the judge could not follow until each name says what the thing is",
    "nesting": "flatten the nesting with early returns so the happy path reads straight down",
    "length": "split the long function into functions whose names say what they do",
    "mixed_levels": "keep one level of abstraction per function: pull the low-level steps out under their own names",
    "magic_values": "name every literal and replace each behaviour flag with two functions",
    "cleverness": "unfold the clever one-liners into plain statements",
}
_CLEAN_FIXES = {
    "duplication": "extract the logic written twice into one function",
    "dead_code": "delete the unused code, parameters and branches",
    "comments": "replace what-comments with why-comments, and add a why where the reader would ask",
    "style": "match the surrounding code's conventions",
    "error_handling": "handle each error where it arises and never swallow one",
}

# What answers each top-risk label in code. A label the judge chooses is always about the diff,
# so it always has a code path; only "architecture" needs Eitri's facts to say which one.
_RISK_FIXES = {
    "correctness": "walk the new branches with empty input, None, boundaries and the error path; add a failing test for each",
    "security": "find the trust boundary the judge means (input reaching a shell, a path, a log, a client) and close it with a test",
    "tests": "add a test per changed behaviour that fails without the change",
    "readability": "rename what the judge could not follow and split the function it hides in",
    "docs": "update the guide, AGENTS.md note or reference the behaviour change left stale",
}


@dataclass
class Verdict:
    outcome: str
    reasons: list[str]  # short, for the table, telemetry and JSON
    answers: dict[str, Any]
    model: str
    usage: dict[str, Any]
    advice: list[str] = field(default_factory=list)  # one plain sentence per reason

    @property
    def exit_code(self) -> int:
        return {"approve": EXIT_OK, "comment": EXIT_OK, "request_changes": EXIT_REQUEST_CHANGES, "escalate": EXIT_ESCALATE}[self.outcome]


def _noul(answers: dict[str, Any], key: str, default: float = 0.5) -> float:
    a = answers.get(key) or {}
    v = a.get("noul")
    return float(v) if isinstance(v, (int, float)) else default


def _score(answers: dict[str, Any], key: str, levels: int) -> float:
    """The expected level on 0..levels-1; the middle when unanswered."""
    a = answers.get(key) or {}
    v = a.get("score")
    return float(v) if isinstance(v, (int, float)) else (levels - 1) / 2.0


def _choice(answers: dict[str, Any], key: str) -> tuple[str, float]:
    """(chosen label, its probability); ``("?", 0.0)`` when unanswered."""
    a = answers.get(key) or {}
    c = str(a.get("choice") or "?")
    p = (a.get("probabilities") or {}).get(c, 0.0)
    return c, float(p) if isinstance(p, (int, float)) else 0.0


def _limit_fix(answers: dict[str, Any], key: str, fixes: dict[str, str]) -> str:
    """The fix for the judge's most probable *actionable* limit label. ``none`` wins the choice easily under a
    short score (PR #9: none at 0.40 beside a 2.08 readability), and none is not a fix; the runner-up in the
    judge's own probabilities is. Every fix is listed when it gave no actionable label at all."""
    a = answers.get(key) or {}
    probs = a.get("probabilities") or {}
    actionable = {k: float(p) for k, p in probs.items() if k in fixes and isinstance(p, (int, float))}
    chosen = str(a.get("choice") or "")
    if not actionable and chosen in fixes:
        actionable = {chosen: 0.0}
    if not actionable:
        return "the judge named no single limit, so: " + "; ".join(fixes.values())
    label = max(actionable, key=lambda k: actionable[k])
    return f"its most probable limit: {label} ({actionable[label]:.2f}): {fixes[label]}"


@dataclass(frozen=True)
class _Judgment:
    """The judge's answers read once, with the policy's neutral defaults for anything unanswered:
    an unanswered gate is on the fence, an unanswered applicability-gated question is a Yes."""

    safe: float
    human: float
    security: float
    walls: float
    leaks: float
    tests: float
    errors_ok: float
    scope: float
    correctness: float
    clean: float
    clean_fix: str
    readability: float
    readability_fix: str
    purpose: float
    signatures: float
    blast: float
    risk: str
    risk_p: float
    kind: str

    @classmethod
    def read(cls, answers: dict[str, Any]) -> _Judgment:
        risk, risk_p = _choice(answers, "biggest_risk")
        return cls(
            safe=_noul(answers, "safe_to_merge"),
            human=_noul(answers, "needs_human_review"),
            security=_noul(answers, "has_security_concern", 0.0),
            walls=_noul(answers, "respects_slice_walls", 1.0),
            leaks=_noul(answers, "leaks_internals", 0.0),
            tests=_noul(answers, "tests_cover_change", 1.0),
            errors_ok=_noul(answers, "errors_use_problem_details", 1.0),
            scope=_noul(answers, "stays_in_scope", 1.0),
            correctness=_score(answers, "correctness", 4),
            clean=_score(answers, "clean_code", 4),
            clean_fix=_limit_fix(answers, "clean_code_limit", _CLEAN_FIXES),
            readability=_score(answers, "readability", 4),
            readability_fix=_limit_fix(answers, "readability_limit", _READABILITY_FIXES),
            purpose=_noul(answers, "single_purpose", 1.0),
            signatures=_noul(answers, "lean_signatures", 1.0),
            blast=_score(answers, "blast_radius", 3),
            risk=risk,
            risk_p=risk_p,
            kind=_choice(answers, "change_kind")[0],
        )


def _over(facts: dict[str, Any], limit: str) -> str:
    """The changed functions the AST found over one limit (``lines`` or ``params``), named the way the
    table names them; ``""`` when none."""
    return "; ".join(shape_line(v) for v in facts.get("function_shape") or [] if limit in v["over"])


def _start_with(facts: dict[str, Any], limit: str = "lines") -> str:
    """`` — start with path:line name (…)`` when the AST named a function, so advice says where to begin."""
    over = _over(facts, limit)
    return f" — start with {over}" if over else ""


def _craft_shortfalls(j: _Judgment) -> list[tuple[str, float, float, str, str]]:
    """The two craft scores below their target, as ``(name, score, target, what 3 means, fix)``."""
    rubrics = [
        ("readability", j.readability, T["readability_target"], "3 reads like prose", j.readability_fix),
        ("clean code", j.clean, T["clean_target"], "3 is exemplary", j.clean_fix),
    ]
    return [r for r in rubrics if r[1] < r[2]]


def _craft_doubts(j: _Judgment, facts: dict[str, Any]) -> list[tuple[str, bool]]:
    """The clean-code answers below their bar, each with the code change that answers it. The AST facts
    name the function when there is one over the limits; otherwise the advice names the kind of fix."""
    where = _start_with(facts)
    out = [
        (f"it rates {name} {score:.2f}/3 against the target {target}: {fix}{where}", True)
        for name, score, target, _, fix in _craft_shortfalls(j)
    ]
    if j.purpose < T["purpose_approve"]:
        fix = "split each into functions whose names say what they do"
        out.append((f"it doubts every changed function does one thing ({j.purpose:.2f}): {fix}{where}", True))
    if j.signatures < T["signatures_approve"]:
        fix = "group the values that travel together, replace a behaviour flag with two functions, drop what is only passed through"
        out.append((f"it doubts the changed signatures are lean ({j.signatures:.2f}): {fix}{_start_with(facts, 'params')}", True))
    return out


def _fit_doubts(j: _Judgment, facts: dict[str, Any]) -> list[tuple[str, bool]]:
    """Tests, the error path, scope and correctness below their bar: each one a code change, except an
    undecided error-path answer, which a reviewer confirms."""
    out: list[tuple[str, bool]] = []
    if facts.get("source_changed") and j.tests < T["tests_approve"]:
        out.append(
            (f"it doubts the tests cover the change ({j.tests:.2f}): add a test per changed behaviour that fails without the change", True)
        )
    if facts.get("routes_changed") and j.errors_ok <= 0.4:
        fix = (
            "in each changed route raise `Problem` on every error path and assert the `application/problem+json` shape in "
            "tests/api/test_problems.py"
        )
        out.append((f"it doubts every error leaves as a problem document ({j.errors_ok:.2f}): {fix}", True))
    elif facts.get("routes_changed") and j.errors_ok < 0.7:
        check = "a reviewer confirms the changed routes raise `Problem`"
        out.append((f"it is undecided whether every error leaves as a problem document ({j.errors_ok:.2f}): {check}", False))
    if j.scope < 0.6:
        out.append((f"it doubts the change stays in scope ({j.scope:.2f}): move the unrelated part to its own PR", True))
    if j.correctness < 2.0:
        fix = "walk the new branches with empty input, None and boundaries; add a failing test for each"
        out.append((f"it rates correctness only {j.correctness:.2f}/3: {fix}", True))
    return out


def _risk_doubts(j: _Judgment, facts: dict[str, Any]) -> list[tuple[str, bool]]:
    """The top-risk label, a human wanted and a wide blast radius. The architecture label is a code change
    unless Eitri found nothing and the change is a refactor: then it points at the restructure itself."""
    out: list[tuple[str, bool]] = []
    lead = f"it names {j.risk} as the one thing to check ({j.risk_p:.2f}): "
    if j.risk == "architecture" and not facts.get("wall_violations"):
        if j.kind == "refactor":
            out.append((lead + "Eitri found no wall violation, so the restructure itself", False))
        else:
            placement = (
                "Eitri found no wall violation, so check placement (no framework code in a feature, no feature logic in pfa/api) "
                "and that every feature used is declared in feature.json"
            )
            out.append((lead + placement, True))
    elif j.risk in _RISK_FIXES:
        where = _start_with(facts) if j.risk == "readability" else ""
        out.append((lead + _RISK_FIXES[j.risk] + where, True))
    if j.human >= T["human_escalate"]:
        out.append((f"it wants a person to look (needs_human_review {j.human:.2f})", False))
    if j.blast >= T["wide_refactor_escalate"]:
        out.append(("the blast radius is wide (the kernel, the composition root or a frozen contract)", False))
    return out


def _doubts(answers: dict[str, Any], facts: dict[str, Any]) -> list[tuple[str, bool]]:
    """What drives a low ``safe_to_merge``: every other answer below its own bar, as ``(sentence, fixable)``.
    A fixable doubt names the code change that answers it; the rest is for a reviewer to weigh. The
    comment never says "the other rows say what it doubts most"."""
    j = _Judgment.read(answers)
    return _fit_doubts(j, facts) + _craft_doubts(j, facts) + _risk_doubts(j, facts)


Flag = Callable[[str, str], None]


def _architecture_label(j: _Judgment, facts: dict[str, Any]) -> bool:
    """The judge's top-risk label says "architecture" while Eitri and its own walls answer say the imports are clean."""
    violations = facts.get("wall_violations") or []
    return j.risk == "architecture" and j.risk_p >= T["architecture_risk_block"] and not violations and j.walls > T["walls_block"]


def _wide_refactor(j: _Judgment) -> bool:
    return j.kind == "refactor" and j.blast >= T["wide_refactor_escalate"]


def _walls_clean(j: _Judgment, facts: dict[str, Any]) -> str:
    """What Eitri and the walls answer established, for the two reasons that explain an architecture label."""
    return (
        f"Eitri found no wall violation in the {facts.get('files_changed', 0)} changed files and the judge rates the walls "
        f"respected ({j.walls:.2f})"
    )


def _not_safe(j: _Judgment, answers: dict[str, Any], facts: dict[str, Any], flag: Flag) -> str | None:
    """A low ``safe_to_merge`` blocks when a doubt behind it has a code fix (and the advice names it);
    otherwise it returns the sentence for the escalation, since only a person can clear it."""
    if j.safe > T["safe_block"]:
        return None
    doubts = _doubts(answers, facts)
    fixable = [text for text, can_fix in doubts if can_fix]
    weigh = [text for text, can_fix in doubts if not can_fix]
    if fixable:
        flag(
            f"not safe to merge p(safe)={j.safe:.2f}",
            "The judge does not consider the change mergeable as-is. To fix in code: "
            + "; ".join(fixable)
            + "."
            + (" For a reviewer to weigh: " + "; ".join(weigh) + "." if weigh else ""),
        )
        return None
    if weigh:
        return (
            "The judge does not consider the change mergeable as-is, and nothing it doubts is a code change: "
            + "; ".join(weigh)
            + ". A person decides; the check cannot clear this on its own."
        )
    return (
        "The judge does not consider the change mergeable as-is and no other answer explains the doubt. A person decides; ask a reviewer."
    )


def _safety_blocks(j: _Judgment, facts: dict[str, Any], flag: Flag) -> None:
    """Facts first: Eitri decided the walls from the import graph. Then the two answers no fix can argue with."""
    violations = facts.get("wall_violations") or []
    if violations:
        flag(
            f"Eitri found {len(violations)} wall violation(s) in the changed files",
            "The change crosses a slice wall. Each violation is listed below with the file, the line and the fix; "
            "`eitri check --root .` reproduces it locally.",
        )
    if j.security >= T["security_block"]:
        flag(
            f"security concern p={j.security:.2f}",
            "The judge sees a concrete security weakness (injection, leaked secrets, missing validation at a trust "
            "boundary, weakened auth). Find and remove it before merging.",
        )
    if j.leaks >= 0.5:
        flag(
            f"internals would leak to clients p={j.leaks:.2f}",
            "Something internal (a stack trace, a path, raw exception text) would reach an API client. "
            "Route the error through `Problem` with a client-safe `detail`.",
        )


def _architecture_blocks(j: _Judgment, facts: dict[str, Any], flag: Flag) -> None:
    """The walls answer, and the top-risk label when it contradicts a Yes on the walls."""
    if j.walls <= T["walls_block"]:
        flag(
            f"slice walls violated p(respects)={j.walls:.2f}",
            "The judge believes a slice wall or the dependency direction is violated: an import of another slice's "
            "`internal/`, code placed in the wrong package, or a frozen contract changed non-additively.",
        )
    if _architecture_label(j, facts) and not _wide_refactor(j):
        # the judge's own top-risk label contradicts its Yes on the walls; the label wins (a persuasive docstring
        # once talked the walls answer up to 0.62 while the label still said architecture — see the guide)
        flag(
            f"judge names architecture as the biggest risk p={j.risk_p:.2f}",
            f"{_walls_clean(j, facts)}, yet it names a slice-wall or dependency-direction problem as the one thing to check. "
            "The import graph shows no cross-slice import past a contract, so look for what Eitri cannot see: "
            "code in the wrong package (framework code inside a feature, feature logic in pfa/api), a feature used "
            "without being declared in feature.json, or a contract member changed rather than added. "
            "Fix it, or say in the PR body why the placement is right.",
        )


def _behaviour_blocks(j: _Judgment, facts: dict[str, Any], flag: Flag) -> None:
    """Correctness and the error path: a defect the diff shows, with the test that would catch it."""
    if j.correctness <= T["correctness_block"]:
        flag(
            f"correctness expected level {j.correctness:.2f}/3",
            "The judge expects the changed code not to behave as intended, usually an edge case visible in the diff "
            "(empty input, None, boundaries, error paths). Walk the new branches with those inputs and add a test for each.",
        )
    if facts.get("routes_changed") and j.errors_ok <= 0.4:
        flag(
            f"errors bypass Problem Details p(ok)={j.errors_ok:.2f}",
            "A changed route answers an error without the shared `Problem` type. Every error must leave as an "
            "RFC 9457 problem document; see harness/guides/returning-errors.md.",
        )


def _shape_blocks(j: _Judgment, facts: dict[str, Any], flag: Flag) -> None:
    """Shape facts never block alone: a line count is a smell, not a wall. They block when the judge agrees
    and they say which function, so the advice names the code change."""
    limits = facts.get("function_limits") or {}
    long_fns, wide = _over(facts, "lines"), _over(facts, "params")
    if long_fns and j.purpose <= T["purpose_block"]:
        flag(
            f"functions do more than one thing p(single)={j.purpose:.2f}",
            f"The judge does not consider the changed functions single-purpose, and the AST names where: {long_fns}. "
            f"Split each into functions whose names say what they do, each within {limits.get('lines', '?')} lines; a flat "
            "table or literal may stay whole if the PR body says so.",
        )
    if wide and j.signatures <= T["signatures_block"]:
        flag(
            f"signatures too wide p(lean)={j.signatures:.2f}",
            f"The judge does not consider the changed signatures lean, and the AST names where: {wide}. Group the values "
            "that travel together into one object, replace a behaviour flag with two functions, and drop what is only "
            f"passed through, so each takes at most {limits.get('params', '?')} parameters.",
        )


def _craft_blocks(j: _Judgment, facts: dict[str, Any], flag: Flag) -> None:
    """Readability and clean code below their target send the agent back, when code changed at all. The target
    is deliberately above "good enough": the advice names what the judge says limits the score, and the
    functions the AST found over the limits, so the agent knows where to start."""
    if not facts.get("code_changed"):
        return
    where = _start_with(facts)
    cut = facts.get("files_truncated") or []
    if cut:
        where += f". The judge saw a cut diff ({len(cut)} file(s) over the token budget), so the score rates what it saw"
    for name, score, target, top, fix in _craft_shortfalls(j):
        flag(
            f"{name} {score:.2f}/3 below the target {target}",
            f"The judge rates the changed code's {name} {score:.2f} on 0–3 ({top}); the target is {target}. In its answer, {fix}{where}.",
        )


def _blocks(j: _Judgment, answers: dict[str, Any], facts: dict[str, Any], flag: Flag) -> str | None:
    """Every ``request_changes`` reason, facts first. Returns the not-safe sentence that needs a human instead."""
    _safety_blocks(j, facts, flag)
    not_safe_needs_human = _not_safe(j, answers, facts, flag)
    _architecture_blocks(j, facts, flag)
    _behaviour_blocks(j, facts, flag)
    _shape_blocks(j, facts, flag)
    _craft_blocks(j, facts, flag)
    return not_safe_needs_human


def _escalations(j: _Judgment, facts: dict[str, Any], not_safe_needs_human: str | None, flag: Flag) -> None:
    """Every ``escalate`` reason: a doubt only a person can clear."""
    if not_safe_needs_human:
        flag(f"not safe to merge p(safe)={j.safe:.2f}; nothing left to change in code", not_safe_needs_human)
    if _architecture_label(j, facts) and _wide_refactor(j):
        flag(
            f"architecture-wide refactor; judge names architecture as the biggest risk p={j.risk_p:.2f}",
            f"{_walls_clean(j, facts)}. It names architecture because this refactor moves the kernel, the composition root or a "
            "contract (blast radius wide), not because of an import. There is nothing for the check to fix: a person "
            "reviews the new layout against the dependency direction in AGENTS.md and decides.",
        )
    if j.human >= T["human_escalate"]:
        flag(
            f"needs a human p={j.human:.2f}",
            "The judge wants a person to look: judgment calls, trade-offs, surprising behaviour or wide impact. "
            "Ask a reviewer rather than waiting for the check to go green.",
        )
    if abs(j.safe - 0.5) < T["uncertain_escalate"]:
        flag(
            f"model is on the fence p(safe)={j.safe:.2f}",
            "The judge cannot tell whether this is safe. A human decides; tests that demonstrate the changed behaviour usually settle it.",
        )


def _approval(j: _Judgment, facts: dict[str, Any]) -> tuple[str, list[str]]:
    """``approve`` when every bar is met, else ``comment`` with the bars that were not. Readability and clean
    code have no bar here: below their target they already blocked in ``_craft_blocks``."""
    tests_needed = bool(facts.get("source_changed"))
    bars = [
        (j.safe >= T["safe_approve"], ""),
        (j.human <= T["human_approve"], ""),
        (j.security <= T["security_approve"], ""),
        (not tests_needed or j.tests >= T["tests_approve"], f"tests may not cover the change p={j.tests:.2f}"),
        (j.purpose >= T["purpose_approve"], f"functions may not be single-purpose p={j.purpose:.2f}"),
        (j.signatures >= T["signatures_approve"], f"signatures may be wide p={j.signatures:.2f}"),
    ]
    if all(met for met, _ in bars):
        return "approve", [f"p(safe)={j.safe:.2f} p(human)={j.human:.2f} clean={j.clean:.2f}/3 readable={j.readability:.2f}/3"]
    notes = [f"p(safe)={j.safe:.2f}", f"p(human)={j.human:.2f}"] + [note for met, note in bars if not met and note]
    return "comment", notes


def decide(answers: dict[str, Any], facts: dict[str, Any], model: str, usage: dict[str, Any]) -> Verdict:
    j = _Judgment.read(answers)
    reasons: list[str] = []
    advice: list[str] = []

    def flag(reason: str, why: str) -> None:
        reasons.append(reason)
        advice.append(why)

    not_safe_needs_human = _blocks(j, answers, facts, flag)
    if reasons:
        return Verdict("request_changes", reasons, answers, model, usage, advice)
    _escalations(j, facts, not_safe_needs_human, flag)
    if reasons:
        return Verdict("escalate", reasons, answers, model, usage, advice)
    outcome, notes = _approval(j, facts)
    return Verdict(outcome, notes, answers, model, usage)
