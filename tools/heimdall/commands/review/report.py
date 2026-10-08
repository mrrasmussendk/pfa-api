"""The outputs: a table on stdout and the sticky PR comment. The comment leads with what went
wrong, in sentences, and what to do; the numbers come after, for whoever wants them."""

from __future__ import annotations

from typing import Any

from .policy import Verdict
from .state import shape_line

_BADGE = {"approve": "✅", "comment": "💬", "request_changes": "🛑", "escalate": "🙋"}


def _fmt_answer(key: str, a: dict[str, Any], qs: dict[str, dict[str, Any]]) -> tuple[str, str]:
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


def _violation_item(v: dict[str, Any]) -> str:
    """The Markdown bullet: Eitri's line in code, then the rule doc's fix and a link to the rule."""
    item = f"- `{violation_line(v)}`"
    if v.get("fix"):
        item += f" **Fix:** {v['fix']}"
    if v.get("doc"):
        item += f" ([{v['rule']}]({v['doc']}))"
    return item


def render_table(v: Verdict, qs: dict[str, dict[str, Any]], facts: dict[str, Any] | None = None) -> str:
    """The stdout report. The Stop hook hands the agent its tail, so each reason carries its advice: the
    reason alone ("readability 2.11/3 below the target") names nothing to fix."""
    rows = [f"outcome: {v.outcome}  ({'; '.join(v.reasons)})", ""]
    rows += [f"  {reason}: {why}" for reason, why in zip(v.reasons, v.advice, strict=False)]
    if v.advice:
        rows.append("")
    violations = (facts or {}).get("wall_violations") or []
    if violations:
        rows += [f"  {violation_line(x)}" + (f"  fix: {x['fix']}" if x.get("fix") else "") for x in violations] + [""]
    shape = (facts or {}).get("function_shape") or []
    if shape:
        rows += [f"  over the limits: {shape_line(x)}" for x in shape] + [""]
    rows.append("question".ljust(28) + "answer".ljust(44) + "probability")
    for key in qs:
        a = v.answers.get(key)
        if not isinstance(a, dict):
            continue
        ans, prob = _fmt_answer(key, a, qs)
        rows.append(key.ljust(28) + ans[:43].ljust(44) + prob)
    rows.append("")
    rows.append(f"model: {v.model} · input tokens {v.usage.get('input_tokens', '?')} · output tokens {v.usage.get('output_tokens', '?')}")
    return "\n".join(rows) + "\n"


def _what_went_wrong(v: Verdict, facts: dict[str, Any]) -> list[str]:
    """The part of the comment a human reads first."""
    if v.outcome == "approve":
        return []
    lines = ["### What went wrong" if v.outcome == "request_changes" else "### What the judge wants", ""]
    lines += [f"- **{reason}.** {why}" for reason, why in zip(v.reasons, v.advice, strict=False)]
    if v.outcome == "comment":
        lines.append("- Nothing blocks the merge; the notes above are what a reviewer would raise.")
    violations = facts.get("wall_violations") or []
    if violations:
        lines += ["", "Eitri's findings in the changed files:", ""]
        lines += [_violation_item(x) for x in violations]
    if v.outcome == "request_changes":
        lines += ["", "**Next:** fix what is named above and push; this check reruns on every push."]
    elif v.outcome == "escalate":
        lines += ["", "**Next:** the check passes with a warning and the PR gets the `needs-human-review` label; a reviewer decides."]
    return [*lines, ""]


def _contract_label(name: str, d: dict[str, Any]) -> str:
    return f"{name} (fan-in {d['fan_in']}" + (", frozen)" if d["frozen"] else ")")


def _told_the_judge(facts: dict[str, Any]) -> list[str]:
    """The facts, for whoever wants to see what the judge was given."""
    lines = [
        f"- files: {facts['files_changed']} (+{facts['lines_added']} / −{facts['lines_removed']})",
        f"- slices touched: {', '.join(facts['slices_touched']) or '(none)'}"
        + (" — **cross-slice**" if facts["cross_slice_change"] else ""),
        "- contracts touched: " + (", ".join(_contract_label(k, d) for k, d in facts["contracts_touched"].items()) or "(none)"),
        f"- routes changed: {'yes' if facts['routes_changed'] else 'no'} · tests changed: {len(facts['tests_changed'])} · docs changed: {'yes' if facts['docs_changed'] else 'no'}",
    ]
    shape = facts.get("function_shape") or []
    if shape:
        limits = facts.get("function_limits") or {}
        lines.append(f"- changed functions over {limits.get('lines', '?')} lines or {limits.get('params', '?')} parameters:")
        lines += [f"  - `{shape_line(v)}`" for v in shape]
    lines.append(_size_line(facts))
    if facts["files_truncated"]:
        lines.append(f"- truncated for the judge: {', '.join(facts['files_truncated'])}")
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


def render_markdown(v: Verdict, state: dict[str, Any], qs: dict[str, dict[str, Any]]) -> str:
    facts = state["facts"]
    lines = [
        "<!-- heimdall-review -->",
        f"## {_BADGE.get(v.outcome, '')} Heimdall review: **{v.outcome.replace('_', ' ')}**",
        "",
        f"_{'; '.join(v.reasons)}_",
        "",
        *_what_went_wrong(v, facts),
        "| question | answer | probability |",
        "|---|---|---|",
    ]
    for key in qs:
        a = v.answers.get(key)
        if isinstance(a, dict):
            ans, prob = _fmt_answer(key, a, qs)
            lines.append(f"| `{key}` | {ans} | {prob} |")
    lines += ["", "<details><summary>What Heimdall told the judge</summary>", "", *_told_the_judge(facts), "", "</details>", ""]
    lines.append(
        f"<sub>Jev `{v.model}` via `heimdall review` — typed judgments, policy in code. "
        f"Probabilities are a policy input, not proof; `request changes` fails the check, `escalate` asks for a human.</sub>"
    )
    return "\n".join(lines) + "\n"
