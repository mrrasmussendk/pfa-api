"""The policy: thresholds in code, not in the model. Facts (Eitri) come first and no probability
overrides them; then the judge's answers, in the order block → escalate → approve → comment.

Every reason carries advice for the human reading the PR comment: the evidence it rests on (which
answers, which facts) and the one concrete next action. A reason never says "see the other rows";
it names them. The reason itself is short, for the table, telemetry and JSON."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .base import EXIT_ESCALATE, EXIT_OK, EXIT_REQUEST_CHANGES

# Thresholds. Probabilities from Jev; policy from us. Tune against your own merge history.
T = {
    "security_block": 0.50,  # has_security_concern >= → request_changes
    "safe_block": 0.35,  # safe_to_merge <= → request_changes
    "walls_block": 0.40,  # respects_slice_walls <= → request_changes
    "architecture_risk_block": 0.80,  # biggest_risk == architecture with p >= → request_changes (overrides a Yes on walls)
    "wide_refactor_escalate": 1.5,  # ... unless change_kind == refactor and expected blast_radius (0..2) >= → escalate: nothing to fix, a human reviews the layout
    "correctness_block": 1.25,  # expected score on 0..3 <= → request_changes
    "human_escalate": 0.60,  # needs_human_review >= → escalate
    "uncertain_escalate": 0.15,  # |safe_to_merge - 0.5| < → escalate (the model is on the fence)
    "safe_approve": 0.75,
    "human_approve": 0.35,
    "security_approve": 0.20,
    "tests_approve": 0.50,
    "clean_approve": 1.5,  # expected clean_code on 0..3 >=
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


def _score(answers: dict[str, Any], key: str, levels: int) -> tuple[float, float]:
    """(expected level 0..levels-1, confidence); falls back to the middle at zero confidence."""
    a = answers.get(key) or {}
    v = a.get("score")
    if not isinstance(v, (int, float)):
        return (levels - 1) / 2.0, 0.0
    conf = a.get("confidence")
    return float(v), float(conf) if isinstance(conf, (int, float)) else 0.0


def _choice(answers: dict[str, Any], key: str) -> tuple[str, float]:
    """(chosen label, its probability); ``("?", 0.0)`` when unanswered."""
    a = answers.get(key) or {}
    c = str(a.get("choice") or "?")
    p = (a.get("probabilities") or {}).get(c, 0.0)
    return c, float(p) if isinstance(p, (int, float)) else 0.0


def _doubts(answers: dict[str, Any], facts: dict[str, Any]) -> list[str]:
    """What drives a low ``safe_to_merge``: every other answer that is below its own bar, in words,
    so the comment never says "the other rows say what it doubts most"."""
    out: list[str] = []
    human = _noul(answers, "needs_human_review")
    tests = _noul(answers, "tests_cover_change", 1.0)
    errors_ok = _noul(answers, "errors_use_problem_details", 1.0)
    scope = _noul(answers, "stays_in_scope", 1.0)
    correctness, _ = _score(answers, "correctness", 4)
    blast, _ = _score(answers, "blast_radius", 3)
    risk, risk_p = _choice(answers, "biggest_risk")
    if human >= T["human_escalate"]:
        out.append(f"it wants a person to look (needs_human_review {human:.2f})")
    if facts.get("source_changed") and tests < T["tests_approve"]:
        out.append(f"it doubts the tests cover the change ({tests:.2f})")
    if facts.get("routes_changed") and errors_ok < 0.7:
        out.append(f"it is unsure every error leaves as a problem document ({errors_ok:.2f})")
    if scope < 0.6:
        out.append(f"it doubts the change stays in scope ({scope:.2f})")
    if correctness < 2.0:
        out.append(f"it rates correctness only {correctness:.2f}/3")
    if blast >= T["wide_refactor_escalate"]:
        out.append("the blast radius is wide (the kernel, the composition root or a frozen contract)")
    if risk == "architecture" and not facts.get("wall_violations"):
        kind, _ = _choice(answers, "change_kind")
        what = "the restructure itself" if kind == "refactor" else "placement or an undeclared dependency"
        out.append(f"it names architecture as the one thing to check ({risk_p:.2f}) — Eitri found no wall violation, so {what}")
    elif risk not in ("none", "?"):
        out.append(f"it names {risk} as the one thing to check ({risk_p:.2f})")
    return out


def decide(answers: dict[str, Any], facts: dict[str, Any], model: str, usage: dict[str, Any]) -> Verdict:
    reasons: list[str] = []
    advice: list[str] = []
    safe = _noul(answers, "safe_to_merge")
    human = _noul(answers, "needs_human_review")
    security = _noul(answers, "has_security_concern", 0.0)
    walls = _noul(answers, "respects_slice_walls", 1.0)
    leaks = _noul(answers, "leaks_internals", 0.0)
    tests = _noul(answers, "tests_cover_change", 1.0)
    errors_ok = _noul(answers, "errors_use_problem_details", 1.0)
    correctness, _ = _score(answers, "correctness", 4)
    clean, _ = _score(answers, "clean_code", 4)
    risk, risk_p = _choice(answers, "biggest_risk")
    blast, _ = _score(answers, "blast_radius", 3)
    kind, _ = _choice(answers, "change_kind")
    violations = facts.get("wall_violations") or []
    files_changed = facts.get("files_changed", 0)
    # the judge's top-risk label says "architecture" while Eitri and its own walls answer say the imports are clean
    architecture_label = risk == "architecture" and risk_p >= T["architecture_risk_block"] and not violations and walls > T["walls_block"]
    wide_refactor = kind == "refactor" and blast >= T["wide_refactor_escalate"]

    def flag(reason: str, why: str) -> None:
        reasons.append(reason)
        advice.append(why)

    # Facts first: Eitri decided these from the import graph.
    if violations:
        flag(
            f"Eitri found {len(violations)} wall violation(s) in the changed files",
            "The change crosses a slice wall. Each violation is listed below with the file, the line and the fix; "
            "`eitri check --root .` reproduces it locally.",
        )
    if security >= T["security_block"]:
        flag(
            f"security concern p={security:.2f}",
            "The judge sees a concrete security weakness (injection, leaked secrets, missing validation at a trust "
            "boundary, weakened auth). Find and remove it before merging.",
        )
    if leaks >= 0.5:
        flag(
            f"internals would leak to clients p={leaks:.2f}",
            "Something internal (a stack trace, a path, raw exception text) would reach an API client. "
            "Route the error through `Problem` with a client-safe `detail`.",
        )
    if safe <= T["safe_block"]:
        doubts = _doubts(answers, facts)
        flag(
            f"not safe to merge p(safe)={safe:.2f}",
            "The judge does not consider the change mergeable as-is. "
            + (
                "What drives it: " + "; ".join(doubts) + ". Address those and the verdict moves."
                if doubts
                else "No other answer explains the doubt, so treat it as a request for a human reviewer and ask one."
            ),
        )
    if walls <= T["walls_block"]:
        flag(
            f"slice walls violated p(respects)={walls:.2f}",
            "The judge believes a slice wall or the dependency direction is violated: an import of another slice's "
            "`internal/`, code placed in the wrong package, or a frozen contract changed non-additively.",
        )
    if architecture_label and not wide_refactor:
        # the judge's own top-risk label contradicts its Yes on the walls; the label wins (a persuasive docstring
        # once talked the walls answer up to 0.62 while the label still said architecture — see the guide)
        flag(
            f"judge names architecture as the biggest risk p={risk_p:.2f}",
            f"Eitri found no wall violation in the {files_changed} changed files and the judge rates the walls respected "
            f"({walls:.2f}), yet it names a slice-wall or dependency-direction problem as the one thing to check. "
            "The import graph shows no cross-slice import past a contract, so look for what Eitri cannot see: "
            "code in the wrong package (framework code inside a feature, feature logic in pfa/api), a feature used "
            "without being declared in feature.json, or a contract member changed rather than added. "
            "Fix it, or say in the PR body why the placement is right.",
        )
    if correctness <= T["correctness_block"]:
        flag(
            f"correctness expected level {correctness:.2f}/3",
            "The judge expects the changed code not to behave as intended, usually an edge case visible in the diff "
            "(empty input, None, boundaries, error paths). Walk the new branches with those inputs and add a test for each.",
        )
    if facts.get("routes_changed") and errors_ok <= 0.4:
        flag(
            f"errors bypass Problem Details p(ok)={errors_ok:.2f}",
            "A changed route answers an error without the shared `Problem` type. Every error must leave as an "
            "RFC 9457 problem document; see harness/guides/returning-errors.md.",
        )
    if reasons:
        return Verdict("request_changes", reasons, answers, model, usage, advice)

    if architecture_label and wide_refactor:
        flag(
            f"architecture-wide refactor; judge names architecture as the biggest risk p={risk_p:.2f}",
            f"Eitri found no wall violation in the {files_changed} changed files and the judge rates the walls respected "
            f"({walls:.2f}). It names architecture because this refactor moves the kernel, the composition root or a "
            "contract (blast radius wide), not because of an import. There is nothing for the check to fix: a person "
            "reviews the new layout against the dependency direction in AGENTS.md and decides.",
        )
    if human >= T["human_escalate"]:
        flag(
            f"needs a human p={human:.2f}",
            "The judge wants a person to look: judgment calls, trade-offs, surprising behaviour or wide impact. "
            "Ask a reviewer rather than waiting for the check to go green.",
        )
    if abs(safe - 0.5) < T["uncertain_escalate"]:
        flag(
            f"model is on the fence p(safe)={safe:.2f}",
            "The judge cannot tell whether this is safe. A human decides; tests that demonstrate the changed behaviour usually settle it.",
        )
    if reasons:
        return Verdict("escalate", reasons, answers, model, usage, advice)

    approve = (
        safe >= T["safe_approve"]
        and human <= T["human_approve"]
        and security <= T["security_approve"]
        and clean >= T["clean_approve"]
        and (not facts.get("source_changed") or tests >= T["tests_approve"])
    )
    if approve:
        return Verdict("approve", [f"p(safe)={safe:.2f} p(human)={human:.2f} clean={clean:.2f}/3"], answers, model, usage)
    notes = [f"p(safe)={safe:.2f}", f"p(human)={human:.2f}"]
    if facts.get("source_changed") and tests < T["tests_approve"]:
        notes.append(f"tests may not cover the change p={tests:.2f}")
    if clean < T["clean_approve"]:
        notes.append(f"clean code {clean:.2f}/3")
    return Verdict("comment", notes, answers, model, usage)
