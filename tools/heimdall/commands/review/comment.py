"""The PR comment. A person reads it top-down, so it leads with what to fix — each one a place, what is wrong
there and the fix — and folds the evidence below: what Claude cleared, the unsure notes, the per-file scores,
and what the judge was told. Probabilities are evidence, not instructions, so they stay out of the action."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .policy import FAIL, LABELS, UNSURE, Row, Verdict, clean_fix, fail_reason, unsure_reason
from .report import checked, file_rows, told_the_judge
from .verify import CONFIRMED, REFUTED

_BADGE = {"comment": "💬", "request_changes": "🛑", "escalate": "🙋"}
_CLEAN = re.compile(r"^clean code (?P<score>[\d.]+)/3 in (?P<path>\S+) below the target (?P<target>[\d.]+)$")


@dataclass(frozen=True)
class _Fix:
    place: str  # Markdown: `path:line` · `function`, a file, or empty
    problem: str
    fix: str


def _sentence(text: str) -> str:
    return text.strip().rstrip(".")


def _place(where: str) -> str:
    """`path:line` · `name` for a function, the file for a file, and nothing for the change as a whole."""
    path, _, name = where.partition(" ")
    if name and ":" in path:
        return f"`{path}` · `{name}`"
    return "" if where == "the change" else f"`{where}`"


def _from_rows(v: Verdict) -> dict[str, Row]:
    """Each failing or unsure row, by the reason the policy gave it."""
    return {**{fail_reason(r): r for r in v.rows if r.state == FAIL}, **{unsure_reason(r): r for r in v.rows if r.state == UNSURE}}


def _fixes_for(reason: str, advice: str, v: Verdict, facts: dict[str, Any]) -> list[_Fix]:
    """One block as the fixes it asks for: Claude's findings, a failing check, Eitri's lines, or a file's clean code."""
    confirmed = {r["doubt"]: r for r in v.verification if r["verdict"] == CONFIRMED}
    if reason in confirmed:
        return [
            _Fix(f"`{f['file']}:{f['line']}` · `{f['function']}`", _sentence(f["problem"]), _sentence(f["fix"]))
            for f in confirmed[reason]["findings"]
        ]
    row = _from_rows(v).get(reason)
    if row is not None:
        return [_Fix(_place(row.where), LABELS[row.check][0], _sentence(LABELS[row.check][2]))]
    if reason.startswith("Eitri found"):
        walls = facts.get("wall_violations") or []
        return [
            _Fix(f"`{x['path']}:{x['line']}`", f"{x['rule']}: {x['message']}", _sentence(x.get("fix") or "see the rule's page"))
            for x in walls
        ]
    clean = _CLEAN.match(reason)
    if clean:
        fix = clean_fix(v.files.get(clean["path"]) or {})
        return [_Fix(f"`{clean['path']}`", f"clean code {clean['score']}/3, below the target {clean['target']}", _sentence(fix))]
    return [_Fix("", _sentence(reason), _sentence(advice))]


def _fixes(v: Verdict, facts: dict[str, Any]) -> list[_Fix]:
    if v.outcome != "request_changes":
        return []
    return [fix for reason, advice in zip(v.reasons, v.advice, strict=False) for fix in _fixes_for(reason, advice, v, facts)]


def _headline(v: Verdict, fixes: list[_Fix]) -> str:
    said = {"request_changes": f"{len(fixes)} to fix before merging", "escalate": "a person should look", "comment": "nothing to fix"}
    return f"## {_BADGE.get(v.outcome, '')} Heimdall review: {said.get(v.outcome, v.outcome)}"


def _summary(v: Verdict) -> str:
    line = checked(v)
    if v.verification:
        confirmed = sum(r["verdict"] == CONFIRMED for r in v.verification)
        line += f" · Claude confirmed {confirmed}, cleared {len(v.verification) - confirmed}"
    return line


def _action(v: Verdict, fixes: list[_Fix]) -> list[str]:
    """What the reader does next: the fixes, a person's decision, or nothing."""
    if v.outcome == "request_changes":
        items = [f"{i}. " + (f"**{f.place}** — " if f.place else "") + f"{f.problem}\n   **Fix:** {f.fix}" for i, f in enumerate(fixes, 1)]
        return ["### Fix before merging", "", *items, "", "Push the fix; this check reruns on every push."]
    if v.outcome == "escalate":
        items = [f"- **{reason}** — {_sentence(advice)}" for reason, advice in zip(v.reasons, v.advice, strict=False)]
        return ["### For a person to decide", "", *items, "", "The check passes with a warning and the `needs-human-review` label."]
    return ["No check failed. A person still decides the merge."]


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _fold(summary: str, rows: list[str]) -> list[str]:
    return ["", f"<details><summary>{summary}</summary>", "", *rows, "", "</details>"] if rows else []


def _cell_place(r: Row) -> str:
    """A row's place for a table cell, where an empty one would read as missing."""
    return _place(r.where) or "the change"


def _what_jev_raised(doubt: str, rows: dict[str, Row]) -> tuple[str, str]:
    row = rows.get(doubt)
    return (_cell_place(row), LABELS[row.check][1]) if row else ("", doubt)


def _cleared(v: Verdict) -> list[str]:
    """The checks Claude did not confirm, with why: the evidence that they were looked at and let go."""
    rows = _from_rows(v)
    cleared = [r for r in v.verification if r["verdict"] != CONFIRMED]
    body = ["| where | Jev raised | why it is fine |", "|---|---|---|"]
    for r in cleared:
        where, raised = _what_jev_raised(r["doubt"], rows)
        why = r["reason"] if r["verdict"] == REFUTED else f"undecided: {r['reason']}"
        body.append(f"| {where} | {_cell(raised)} | {_cell(why)} |")
    return _fold(f"{len(cleared)} check{'s' * (len(cleared) != 1)} Claude cleared", body if cleared else [])


def _unsure(v: Verdict) -> list[str]:
    """The unsure checks no one verified (the agent pipeline): notes for a reviewer."""
    if v.verification:
        return []
    unsure = sorted((r for r in v.rows if r.state == UNSURE), key=lambda r: -r.p)
    body = ["| where | Jev suspects | p |", "|---|---|---|", *(f"| {_cell_place(r)} | {LABELS[r.check][1]} | {r.p:.2f} |" for r in unsure)]
    return _fold(f"{len(unsure)} unsure check{'s' * (len(unsure) != 1)}", body if unsure else [])


def _clean_code(v: Verdict) -> list[str]:
    rows = file_rows(v)
    body = ["| file | clean code | limit |", "|---|---|---|", *(f"| `{path}` | {score} | {label} |" for path, score, label in rows)]
    return _fold("Clean code, file by file", body if rows else [])


def render_comment(v: Verdict, state: dict[str, Any]) -> str:
    facts = state["facts"]
    fixes = _fixes(v, facts)
    lines = ["<!-- heimdall-review -->", _headline(v, fixes), "", f"_{_summary(v)}._", "", *_action(v, fixes)]
    lines += _cleared(v) + _unsure(v) + _clean_code(v)
    lines += _fold("What Heimdall told the judge", told_the_judge(facts))
    lines += [
        "",
        f"<sub>Jev `{v.model}` checks each changed function; on the PR, Claude verifies what it flags. A person always decides the merge.</sub>",
    ]
    return "\n".join(lines) + "\n"
