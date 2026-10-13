"""The table on stdout, which the Stop hook hands the agent, and the pieces the PR comment (``comment``) shares
with it: the per-file clean-code rows, the line on what was checked, and what the judge was told."""

from __future__ import annotations

from typing import Any

from .policy import FAIL, UNSURE, Verdict, blast_radius
from .questions import file_questions
from .state import shape_line


def _fmt_answer(key: str, a: dict[str, Any], qs: dict[str, dict[str, Any]]) -> tuple[str, str]:
    """A file's clean-code answer as ``(label, numbers)`` for the per-file table."""
    t = a.get("type")
    if t == "noul":
        p = float(a.get("noul", 0.0))
        return ("yes" if p >= 0.5 else "no"), f"{p:.2f}"
    if t == "score":
        legend = a.get("legend") or {}
        probs = a.get("probabilities") or {}
        top = max(probs, key=lambda k: probs[k]) if probs else None
        label = legend.get(str(top), str(top)) if top is not None else "?"
        return label, f"{float(a.get('score', 0.0)):.2f}/{len(qs[key]['criteria']) - 1} · conf {float(a.get('confidence', 0.0)):.2f}"
    if t == "choice":
        c = str(a.get("choice", "?"))
        return c, f"{float((a.get('probabilities') or {}).get(c, 0.0)):.2f} · conf {float(a.get('confidence', 0.0)):.2f}"
    return "?", "?"


def violation_line(v: dict[str, Any]) -> str:
    """``path:line — EIT001: message`` — the same line Eitri prints."""
    loc = v["path"] + (f":{v['line']}" if v.get("line") else "")
    return f"{loc} — {v['rule']}: {v['message']}"


def file_rows(v: Verdict) -> list[tuple[str, str, str]]:
    """One ``(path, clean code, limit)`` row per file judged whole, lowest score first."""
    fq = file_questions()
    rows: list[tuple[float, str, str, str]] = []
    for path, answers in v.files.items():
        clean, limit = answers.get("clean_code"), answers.get("clean_code_limit")
        score = _fmt_answer("clean_code", clean, fq) if isinstance(clean, dict) else ("?", "?")
        label = _fmt_answer("clean_code_limit", limit, fq) if isinstance(limit, dict) else ("?", "?")
        rows.append((float((clean or {}).get("score", 0.0)), path, f"{score[0]} · {score[1]}", f"{label[0]} · {label[1]}"))
    return [(path, score, label) for _, path, score, label in sorted(rows)]


def _verification_lines(v: Verdict) -> list[str]:
    """How the verifier ruled on each of Jev's doubts: what was confirmed, refuted or left to a person, and why."""
    return [f"Claude {r['verdict']}: {r['doubt']} — {r['reason']}" for r in v.verification]


def _flagged(v: Verdict) -> list[tuple[str, str, str]]:
    """``(check, where, p)`` for each check that failed or was unsure, failures first."""
    flagged = sorted((r for r in v.rows if r.state in (FAIL, UNSURE)), key=lambda r: (r.state != FAIL, -r.p))
    return [(f"{r.check} ({r.state})", r.where, f"{r.p:.2f}") for r in flagged]


def checked(v: Verdict) -> str:
    """One line on what was checked: how many checks, on how many functions, and how they came out."""
    functions = len({r.where for r in v.rows if r.key.startswith("f")})
    fails, unsure = sum(r.state == FAIL for r in v.rows), sum(r.state == UNSURE for r in v.rows)
    checks = f"{len(v.rows)} check{'s' * (len(v.rows) != 1)}"
    return f"{checks} on {functions} changed function{'s' * (functions != 1)} and the change: {fails} failed, {unsure} unsure"


def render_table(v: Verdict, facts: dict[str, Any] | None = None) -> str:
    """The stdout report. The Stop hook hands the agent its tail, so each reason carries its advice: the
    reason alone ("clean code 2.28/3 below the target") names nothing to fix."""
    rows = [f"outcome: {v.outcome}  ({'; '.join(v.reasons)})", ""]
    rows += [f"  {reason}: {why}" for reason, why in zip(v.reasons, v.advice, strict=False)]
    if v.advice:
        rows.append("")
    if v.verification:
        rows += [f"  {line}" for line in _verification_lines(v)] + [""]
    violations = (facts or {}).get("wall_violations") or []
    if violations:
        rows += [f"  {violation_line(x)}" + (f"  fix: {x['fix']}" if x.get("fix") else "") for x in violations] + [""]
    shape = (facts or {}).get("function_shape") or []
    if shape:
        rows += [f"  over the limits: {shape_line(x)}" for x in shape] + [""]
    rows.append(checked(v))
    flagged = _flagged(v)
    if flagged:
        rows.append("check".ljust(30) + "where".ljust(70) + "p")
        rows += [check.ljust(30) + where[:69].ljust(70) + p for check, where, p in flagged]
    rows.append("")
    files = file_rows(v)
    if files:
        rows.append("file (judged whole)".ljust(56) + "clean code".ljust(32) + "limit")
        rows += [path[:55].ljust(56) + score.ljust(32) + label for path, score, label in files]
        rows.append("")
    rows.append(f"model: {v.model} · input tokens {v.usage.get('input_tokens', '?')} · output tokens {v.usage.get('output_tokens', '?')}")
    return "\n".join(rows) + "\n"


def _contract_label(name: str, d: dict[str, Any]) -> str:
    return f"{name} (fan-in {d['fan_in']}" + (", frozen)" if d["frozen"] else ")")


def told_the_judge(facts: dict[str, Any]) -> list[str]:
    """The facts, for whoever wants to see what the judge was given."""
    lines = [
        f"- files: {facts['files_changed']} (+{facts['lines_added']} / −{facts['lines_removed']})",
        f"- slices touched: {', '.join(facts['slices_touched']) or '(none)'}"
        + (" — **cross-slice**" if facts["cross_slice_change"] else ""),
        "- contracts touched: " + (", ".join(_contract_label(k, d) for k, d in facts["contracts_touched"].items()) or "(none)"),
        f"- routes changed: {'yes' if facts['routes_changed'] else 'no'} · tests changed: {len(facts['tests_changed'])} · docs changed: {'yes' if facts['docs_changed'] else 'no'}",
        "- blast radius (from the facts): {1} ({0}/2)".format(*blast_radius(facts)),
        f"- changed functions asked about: {facts.get('functions_asked', 0)}"
        + (f"; {facts['functions_not_asked']} more did not fit the budget" if facts.get("functions_not_asked") else ""),
    ]
    shape = facts.get("function_shape") or []
    if shape:
        limits = facts.get("function_limits") or {}
        lines.append(f"- changed functions over {limits.get('lines', '?')} lines or {limits.get('params', '?')} parameters:")
        lines += [f"  - `{shape_line(v)}`" for v in shape]
    lines.append(_size_line(facts))
    if facts["files_truncated"]:
        lines.append(f"- truncated for the judge: {', '.join(facts['files_truncated'])}")
    whole = facts.get("craft_files") or []
    if whole:
        lines.append(f"- judged whole for clean code, one call each: {', '.join(whole)}")
    cut = facts.get("craft_truncated") or []
    if cut:
        lines.append(f"- cut at the token budget when judged whole: {', '.join(cut)}")
    return lines


def _size_line(facts: dict[str, Any]) -> str:
    """How much of the diff the judge saw, in Brokkr tokens against the budget and Jev's window."""
    seen, whole = facts.get("state_tokens", 0), facts.get("diff_tokens", 0)
    budget, window = facts.get("state_budget_tokens", 0), facts.get("judge_window_tokens", 0)
    line = f"- size: ~{seen:,} tokens sent of a {budget:,} budget (Jev reads {window:,} with the questions)"
    if facts.get("files_truncated"):
        return (
            line
            + f"; the whole diff is ~{whole:,} tokens, so {len(facts['files_truncated'])} file(s) were cut and the craft scores rate what the judge saw"
        )
    return line + f"; the whole diff (~{whole:,} tokens) was seen"
