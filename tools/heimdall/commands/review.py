"""``heimdall review`` — Jev (TypeSafe AI) as the judge of a diff, Heimdall as the deliberation.

Jev does not write review comments. It answers *typed* questions — yes/no (``noul``),
``choice``, ``score`` — with probabilities. This command is the System Two around that call:

1. **bounded state** — the diff, cut per file and capped in size, plus what Heimdall already
   knows: the slice map (the architecture's promise), which slices / contracts / tests the
   change touches, and the task the author stated;
2. **one Jev call** with a fixed set of questions about correctness, clean code, tests,
   security, scope and architecture fit;
3. **policy in code** — thresholds here, not in the model — producing one outcome:
   ``approve`` · ``comment`` · ``request_changes`` · ``escalate``;
4. **outputs**: a table on stdout, ``--json`` / ``--markdown`` files for CI, and a ``review``
   line in ``.heimdall/telemetry.jsonl`` so drift analysis can see what was judged.

Exit codes: 0 approve/comment · 1 request_changes · 2 escalate · 3 could not review
(no diff, no key, API failure). Stdlib only, like the rest of Heimdall. The network call is
``POST https://api.typesafe.ai/v1/systemone`` with ``TYPESAFE_API_KEY``; ``--dry-run`` prints
the state and the questions and makes no call.

Jev's probabilities are a policy input, not proof. The thresholds below are a starting
point: measure them against your own merges before trusting them with the merge button.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, TextIO

from ..model import Finding, MapModel
from ..pathutil import is_contract_path, norm, shared_of, slice_of
from ..sensors.boundary_edits import FAN_IN_FREEZE

USAGE = (
    "usage: heimdall review [--base <ref> | --diff <file|->] [--task <text>] [--model jev-latest]\n"
    "                       [--json <out>] [--markdown <out>] [--dry-run] [--allow-missing-key]\n"
    "                       [--session <id>]\n"
    "  default diff: `git diff HEAD` (working tree); --base <ref> uses `git diff <ref>...HEAD`"
)

API_URL = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-latest"
API_KEY_ENV = "TYPESAFE_API_KEY"
API_URL_ENV = "TYPESAFE_API_URL"

# ---- bounded state: what one Jev call may see
MAX_FILE_CHARS = 12_000  # per file; the rest is summarised as "+N/-M more lines"
MAX_STATE_CHARS = 60_000  # all files together
IGNORED = re.compile(r"(^|/)(\.heimdall/|.*\.egg-info/|.*\.lock$|.*\.min\.(js|css)$|.*\.(png|jpg|gif|ico|woff2?|pdf)$)")

EXIT_OK, EXIT_REQUEST_CHANGES, EXIT_ESCALATE, EXIT_ERROR = 0, 1, 2, 3
OUTCOMES = ("approve", "comment", "request_changes", "escalate")


class ReviewError(Exception):
    """Could not review (exit 3): the review is not an answer about the code."""


# ====================================================================== the diff


@dataclass
class FileChange:
    path: str
    status: str  # added | deleted | modified | renamed
    added: int = 0
    removed: int = 0
    hunks: list[str] = field(default_factory=list)
    binary: bool = False

    @property
    def text(self) -> str:
        return "\n".join(self.hunks)


_DIFF_GIT = re.compile(r"^diff --git a/(.+?) b/(.+)$")


def parse_unified_diff(diff: str) -> list[FileChange]:
    """``git diff`` output -> one ``FileChange`` per file, hunks kept verbatim."""
    files: list[FileChange] = []
    current: FileChange | None = None
    for line in diff.splitlines():
        m = _DIFF_GIT.match(line)
        if m:
            current = FileChange(path=m.group(2), status="modified")
            if m.group(1) != m.group(2):
                current.status = "renamed"
            files.append(current)
            continue
        if current is None:
            continue
        if line.startswith("new file mode"):
            current.status = "added"
        elif line.startswith("deleted file mode"):
            current.status = "deleted"
        elif line.startswith("Binary files"):
            current.binary = True
        elif line.startswith(("+++ ", "--- ")):
            continue
        elif line.startswith(("@@", " ", "+", "-", "\\")):
            if line.startswith("@@"):
                current.hunks.append(line)
            else:
                if line.startswith("+"):
                    current.added += 1
                elif line.startswith("-"):
                    current.removed += 1
                current.hunks.append(line)
    return [f for f in files if not IGNORED.search(norm(f.path))]


def git_diff(cwd: str, base: str | None) -> str:
    spec = [f"{base}...HEAD"] if base else ["HEAD"]
    try:
        out = subprocess.run(
            ["git", "diff", "--no-color", "--unified=3", "--no-ext-diff", *spec],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as e:
        raise ReviewError(f"git not available: {e}") from e
    if out.returncode != 0:
        raise ReviewError(f"git diff failed: {out.stderr.strip()}")
    return out.stdout


# ====================================================================== bounded state


def area_of(path: str, m: MapModel | None) -> str:
    """Where a path sits in the architecture — the Heimdall half of the state."""
    p = norm(path)
    if m is not None:
        s = slice_of(p, m)
        if s is not None:
            return f"contract:{s}" if is_contract_path(p) else f"slice:{s}"
        if m.kernel and f"/{m.kernel}/" in f"/{p}":
            return "kernel"
        sh = shared_of(p, m)
        if sh is not None:
            return f"shared:{sh}"
    if p.startswith("tests/"):
        return "tests"
    if p.startswith("tools/"):
        return "tooling"
    if p.startswith(".github/"):
        return "ci"
    if p.startswith("docs/") or p.endswith(".md"):
        return "docs"
    if p.startswith("src/"):
        return "service"
    return "other"


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    cut = text[:limit]
    cut = cut[: cut.rfind("\n")] if "\n" in cut else cut
    dropped = text.count("\n") - cut.count("\n")
    return cut + f"\n… [{dropped} more diff lines not shown]", True


def build_state(files: list[FileChange], m: MapModel | None, task: str | None) -> dict[str, Any]:
    """The JSON object Jev judges. Facts Heimdall can compute are stated as facts, so the
    model spends its judgment on what only judgment can decide."""
    slices_touched: set[str] = set()
    contracts_touched: dict[str, int] = {}
    routes_changed = False
    tests_touched: set[str] = set()
    source_changed = False
    docs_changed = False
    truncated: list[str] = []
    budget = MAX_STATE_CHARS
    entries: list[dict[str, Any]] = []
    for f in files:
        area = area_of(f.path, m)
        if area.startswith(("slice:", "contract:")):
            name = area.split(":", 1)[1]
            slices_touched.add(name)
            source_changed = True
            if area.startswith("contract:"):
                contracts_touched[name] = m.slices[name].fan_in if m and name in m.slices else 0
            if norm(f.path).endswith("/internal/routes.py"):
                routes_changed = True
        elif area in ("kernel", "service") or area.startswith("shared:"):
            source_changed = True
        elif area == "tests":
            tests_touched.add(norm(f.path))
        elif area == "docs":
            docs_changed = True
        if f.binary:
            text = "[binary]"
        else:
            text, cut = _truncate(f.text, min(MAX_FILE_CHARS, max(budget, 400)))
            if cut:
                truncated.append(f.path)
        budget -= len(text)
        entries.append({"path": norm(f.path), "status": f.status, "area": area, "added": f.added, "removed": f.removed, "diff": text})

    architecture: dict[str, Any] = {}
    if m is not None:
        architecture = {
            "kernel": m.kernel,
            "shared_packages": list(m.shared),
            "slices": {name: {"depends_on": info.depends_on, "contract_fan_in": info.fan_in} for name, info in sorted(m.slices.items())},
            "rules": [
                "a slice imports another slice only through its contract/ package, never internal/",
                "contract/ modules import only the standard library, the kernel and their own contract — no FastAPI, no pydantic",
                "importing slices.<x>.contract requires <x> declared in the slice's slice.json",
                "no dynamic imports, sys.path edits or wildcard imports inside slices",
                "errors leave as RFC 9457 problem documents raised through the shared Problem type, never HTTPException",
                f"a contract with fan-in >= {FAN_IN_FREEZE} is frozen: additive changes only",
                "handlers return Result and never raise for domain reasons; routes decide status codes",
            ],
        }
    facts = {
        "files_changed": len(files),
        "lines_added": sum(f.added for f in files),
        "lines_removed": sum(f.removed for f in files),
        "slices_touched": sorted(slices_touched),
        "cross_slice_change": len(slices_touched) > 1,
        "contracts_touched": {k: {"fan_in": v, "frozen": v >= FAN_IN_FREEZE} for k, v in sorted(contracts_touched.items())},
        "routes_changed": routes_changed,
        "source_changed": source_changed,
        "tests_changed": sorted(tests_touched),
        "docs_changed": docs_changed,
        "files_truncated": truncated,
    }
    state: dict[str, Any] = {"task": task or "(no task statement given)", "architecture": architecture, "facts": facts, "files": entries}
    return state


# ====================================================================== the questions

YES_NO = {"true": "Yes", "false": "No"}


def questions() -> dict[str, dict[str, Any]]:
    """Fixed, typed, greppable. Wording is the whole interface to the model: each question
    names what it judges and the criteria name what each answer means."""
    return {
        # ---- gates
        "safe_to_merge": {
            "type": "noul",
            "instructions": "Considering the whole change in `files` against `task`, `facts` and `architecture.rules`: is it safe to merge as-is, without further edits?",
            "criteria": {
                "true": "No defects, no rule violations, nothing the author must still do",
                "false": "Something must change or be checked before merging",
            },
        },
        "needs_human_review": {
            "type": "noul",
            "instructions": "Does this change need a human reviewer's attention beyond automated checks — judgment calls, trade-offs, surprising behaviour, or wide impact?",
            "criteria": {"true": "A person should look before merge", "false": "Routine; automated checks suffice"},
        },
        "has_security_concern": {
            "type": "noul",
            "instructions": "Does the change introduce a security problem: injection, leaking secrets or internals to clients, unsafe deserialisation, missing validation at a trust boundary, weakened authentication or authorisation?",
            "criteria": {"true": "A concrete security weakness is introduced", "false": "No security weakness introduced"},
        },
        # ---- applicability-gated judgments
        "tests_cover_change": {
            "type": "noul",
            "instructions": "If `facts.source_changed` is true: do the tests in this diff (see `facts.tests_changed` and the test files' hunks) exercise the behaviour the source change introduces or alters? Answer No if behaviour changed and no test demonstrates it.",
            "criteria": {
                "true": "Changed behaviour is demonstrated by changed or added tests",
                "false": "Behaviour changed without a test, or tests do not reach it",
            },
        },
        "stays_in_scope": {
            "type": "noul",
            "instructions": "Does every file change serve the stated `task`? Unrelated refactors, drive-by renames and feature creep count as out of scope. If no task was stated, judge whether the change reads as one coherent intent.",
            "criteria": {"true": "Every change serves the task or one coherent intent", "false": "Contains changes unrelated to the task"},
        },
        "respects_slice_walls": {
            "type": "noul",
            "instructions": "Does the change respect `architecture.rules` and the dependency direction in `architecture.slices`? Look at imports, where new code is placed (contract/ vs internal/ vs kernel), and whether a frozen contract was changed non-additively.",
            "criteria": {"true": "Every rule respected", "false": "At least one rule or the dependency direction is violated"},
        },
        "errors_use_problem_details": {
            "type": "noul",
            "instructions": "If `facts.routes_changed` is true: do the changed routes answer every error through the shared Problem type (RFC 9457 problem documents), with no ad-hoc error dicts, no HTTPException and no sensitive text in `detail`? Answer Yes when no route changed.",
            "criteria": {
                "true": "Errors go through Problem / Problem.domain_rejection only",
                "false": "An error is returned some other way or leaks internals",
            },
        },
        "leaks_internals": {
            "type": "noul",
            "instructions": "Does any change expose stack traces, file paths, secrets, raw exception text or other internals to API clients?",
            "criteria": {"true": "Internals would reach a client", "false": "Nothing internal reaches a client"},
        },
        # ---- rubrics
        "correctness": {
            "type": "score",
            "instructions": "How likely is the changed code to behave as intended, including edge cases visible in the diff (empty input, None, boundaries, error paths)?",
            "criteria": ["Likely broken", "Possibly defective", "Probably correct", "Clearly correct"],
        },
        "clean_code": {
            "type": "score",
            "instructions": "Rate the readability and craft of the changed code: names that say what things are, small single-purpose functions, no duplication, comments that explain why rather than what, consistency with the surrounding style, no dead code.",
            "criteria": ["Hard to follow", "Acceptable", "Clean", "Exemplary"],
        },
        "blast_radius": {
            "type": "score",
            "instructions": "How far could a mistake in this change reach, given `facts` (slices touched, contracts touched and their fan-in, kernel or shared packages touched)?",
            "criteria": [
                "Contained to one slice's internals",
                "Moderate: a contract, a shared package or several slices",
                "Wide: the kernel, the composition root or a frozen contract",
            ],
        },
        "docs_in_sync": {
            "type": "score",
            "instructions": "If `facts.source_changed` is true: do the guides, AGENTS.md notes, README and CHANGELOG in this diff keep up with the behaviour change? Judge 'In sync' when nothing documented changed.",
            "criteria": ["Docs now stale", "Partially updated", "In sync"],
        },
        # ---- labels for the report
        "change_kind": {
            "type": "choice",
            "instructions": "What kind of change is this, predominantly?",
            "criteria": {
                "feature": "New capability for API clients",
                "fix": "Corrects wrong behaviour",
                "refactor": "Same behaviour, better structure",
                "tests": "Mostly tests",
                "docs": "Mostly documentation",
                "tooling": "Mostly the dev tooling or CI",
                "mixed": "Several of the above with no dominant one",
            },
        },
        "biggest_risk": {
            "type": "choice",
            "instructions": "If a reviewer could check only one thing about this change, what should it be?",
            "criteria": {
                "none": "Nothing stands out",
                "correctness": "A logic or edge-case defect",
                "security": "A security weakness",
                "architecture": "A slice wall or dependency-direction violation",
                "tests": "Missing or weak tests",
                "readability": "Code that is hard to follow or maintain",
                "docs": "Documentation that no longer matches",
            },
        },
    }


# ====================================================================== the call

Transport = Callable[[dict[str, Any]], dict[str, Any]]


def http_transport(api_key: str, url: str = API_URL, timeout: float = 90.0, sleep: Callable[[float], None] = time.sleep) -> Transport:
    """POST to the System One API; exponential backoff on 429/529 and transient network errors."""

    def call(payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        delay = 1.0
        last: str = ""
        for attempt in range(4):
            req = urllib.request.Request(
                url,
                data=body,
                method="POST",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "Accept": "application/json"},
            )
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", errors="replace")[:500]
                if e.code in (429, 529) and attempt < 3:
                    last = f"HTTP {e.code}: {detail}"
                elif e.code == 401:
                    raise ReviewError("TypeSafe API rejected the key (401)") from None
                else:
                    raise ReviewError(f"TypeSafe API error HTTP {e.code}: {detail}") from None
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                if attempt == 3:
                    raise ReviewError(f"TypeSafe API unreachable: {e}") from None
                last = str(e)
            sleep(delay)
            delay *= 2
        raise ReviewError(f"TypeSafe API gave up after retries: {last}")

    return call


# ====================================================================== the policy


@dataclass
class Verdict:
    outcome: str
    reasons: list[str]
    answers: dict[str, Any]
    model: str
    usage: dict[str, Any]

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


def _choice(answers: dict[str, Any], key: str) -> str:
    a = answers.get(key) or {}
    return str(a.get("choice") or "?")


# Thresholds. Probabilities from Jev; policy from us. Tune against your own merge history.
T = {
    "security_block": 0.50,  # has_security_concern >= → request_changes
    "safe_block": 0.35,  # safe_to_merge <= → request_changes
    "walls_block": 0.40,  # respects_slice_walls <= → request_changes
    "correctness_block": 1.25,  # expected score on 0..3 <= → request_changes
    "human_escalate": 0.60,  # needs_human_review >= → escalate
    "uncertain_escalate": 0.15,  # |safe_to_merge - 0.5| < → escalate (the model is on the fence)
    "safe_approve": 0.75,
    "human_approve": 0.35,
    "security_approve": 0.20,
    "tests_approve": 0.50,
    "clean_approve": 1.5,  # expected clean_code on 0..3 >=
}


def decide(answers: dict[str, Any], facts: dict[str, Any], model: str, usage: dict[str, Any]) -> Verdict:
    reasons: list[str] = []
    safe = _noul(answers, "safe_to_merge")
    human = _noul(answers, "needs_human_review")
    security = _noul(answers, "has_security_concern", 0.0)
    walls = _noul(answers, "respects_slice_walls", 1.0)
    leaks = _noul(answers, "leaks_internals", 0.0)
    tests = _noul(answers, "tests_cover_change", 1.0)
    errors_ok = _noul(answers, "errors_use_problem_details", 1.0)
    correctness, _ = _score(answers, "correctness", 4)
    clean, _ = _score(answers, "clean_code", 4)

    if security >= T["security_block"]:
        reasons.append(f"security concern p={security:.2f}")
    if leaks >= 0.5:
        reasons.append(f"internals would leak to clients p={leaks:.2f}")
    if safe <= T["safe_block"]:
        reasons.append(f"not safe to merge p(safe)={safe:.2f}")
    if walls <= T["walls_block"]:
        reasons.append(f"slice walls violated p(respects)={walls:.2f}")
    if correctness <= T["correctness_block"]:
        reasons.append(f"correctness expected level {correctness:.2f}/3")
    if facts.get("routes_changed") and errors_ok <= 0.4:
        reasons.append(f"errors bypass Problem Details p(ok)={errors_ok:.2f}")
    if reasons:
        return Verdict("request_changes", reasons, answers, model, usage)

    if human >= T["human_escalate"]:
        reasons.append(f"needs a human p={human:.2f}")
    if abs(safe - 0.5) < T["uncertain_escalate"]:
        reasons.append(f"model is on the fence p(safe)={safe:.2f}")
    if reasons:
        return Verdict("escalate", reasons, answers, model, usage)

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


# ====================================================================== reports


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


def render_table(v: Verdict, qs: dict[str, dict[str, Any]]) -> str:
    rows = [f"outcome: {v.outcome}  ({'; '.join(v.reasons)})", ""]
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


_BADGE = {"approve": "✅", "comment": "💬", "request_changes": "🛑", "escalate": "🙋"}


def _contract_label(name: str, d: dict[str, Any]) -> str:
    return f"{name} (fan-in {d['fan_in']}" + (", frozen)" if d["frozen"] else ")")


def render_markdown(v: Verdict, state: dict[str, Any], qs: dict[str, dict[str, Any]]) -> str:
    facts = state["facts"]
    lines = [
        "<!-- heimdall-review -->",
        f"## {_BADGE.get(v.outcome, '')} Heimdall review: **{v.outcome.replace('_', ' ')}**",
        "",
        f"_{'; '.join(v.reasons)}_",
        "",
        "| question | answer | probability |",
        "|---|---|---|",
    ]
    for key in qs:
        a = v.answers.get(key)
        if isinstance(a, dict):
            ans, prob = _fmt_answer(key, a, qs)
            lines.append(f"| `{key}` | {ans} | {prob} |")
    lines += [
        "",
        "<details><summary>What Heimdall told the judge</summary>",
        "",
        f"- files: {facts['files_changed']} (+{facts['lines_added']} / −{facts['lines_removed']})",
        f"- slices touched: {', '.join(facts['slices_touched']) or '(none)'}"
        + (" — **cross-slice**" if facts["cross_slice_change"] else ""),
        "- contracts touched: " + (", ".join(_contract_label(k, d) for k, d in facts["contracts_touched"].items()) or "(none)"),
        f"- routes changed: {'yes' if facts['routes_changed'] else 'no'} · tests changed: {len(facts['tests_changed'])} · docs changed: {'yes' if facts['docs_changed'] else 'no'}",
    ]
    if facts["files_truncated"]:
        lines.append(f"- truncated for the judge: {', '.join(facts['files_truncated'])}")
    lines += [
        "",
        "</details>",
        "",
        (
            f"<sub>Jev `{v.model}` via `heimdall review` — typed judgments, policy in code. "
            f"Probabilities are a policy input, not proof; `request changes` fails the check, `escalate` asks for a human.</sub>"
        ),
    ]
    return "\n".join(lines) + "\n"


# ====================================================================== entry point


def _parse(args: list[str]) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "base": None,
        "diff": None,
        "task": None,
        "model": DEFAULT_MODEL,
        "json": None,
        "markdown": None,
        "dry_run": False,
        "allow_missing_key": False,
        "session": "review",
    }
    i = 0
    while i < len(args):
        a = args[i]
        nxt = args[i + 1] if i + 1 < len(args) else None
        if a == "--dry-run":
            opts["dry_run"] = True
            i += 1
        elif a == "--allow-missing-key":
            opts["allow_missing_key"] = True
            i += 1
        elif a in ("--base", "--diff", "--task", "--model", "--json", "--markdown", "--session") and nxt is not None:
            opts[a[2:]] = nxt
            i += 2
        else:
            raise ReviewError(USAGE)
    if opts["base"] and opts["diff"]:
        raise ReviewError("use --base or --diff, not both")
    return opts


def _write(path: str, cwd: str, text: str) -> None:
    full = os.path.abspath(os.path.join(cwd, path))
    os.makedirs(os.path.dirname(full) or ".", exist_ok=True)
    with open(full, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _telemetry(cwd: str, v: Verdict, facts: dict[str, Any], session: str) -> None:
    heimdall_dir = os.path.join(cwd, ".heimdall")
    os.makedirs(heimdall_dir, exist_ok=True)
    f = Finding(
        event="review",
        kind=v.outcome,
        slice=",".join(facts["slices_touched"]) or None,
        feedback="; ".join(v.reasons),
        sensor="jev",
        ts=time.time(),
        session=session,
    )
    with open(os.path.join(heimdall_dir, "telemetry.jsonl"), "a", encoding="utf-8", newline="\n") as out:
        out.write(f.to_line() + "\n")


def run(
    args: list[str],
    stdin: TextIO,
    stdout: TextIO,
    stderr: TextIO,
    cwd: str,
    transport: Transport | None = None,
    env: Mapping[str, str] | None = None,
) -> int:
    environ: Mapping[str, str] = os.environ if env is None else env
    try:
        opts = _parse(args)
    except ReviewError as e:
        stderr.write(str(e) + "\n")
        return 2

    try:
        if opts["diff"] == "-":
            diff = stdin.read()
        elif opts["diff"]:
            with open(os.path.join(cwd, opts["diff"]), encoding="utf-8", errors="replace") as f:
                diff = f.read()
        else:
            diff = git_diff(cwd, opts["base"])
    except (ReviewError, OSError) as e:
        stderr.write(f"heimdall review: {e}\n")
        return EXIT_ERROR

    files = parse_unified_diff(diff)
    if not files:
        stdout.write("heimdall review: nothing to review (empty diff)\n")
        return EXIT_OK

    map_path = os.path.join(cwd, ".heimdall", "map.json")
    m: MapModel | None = None
    if os.path.isfile(map_path):
        try:
            m = MapModel.load(map_path)
        except (OSError, ValueError, TypeError) as e:
            stderr.write(f"heimdall review: unreadable .heimdall/map.json ({e}); reviewing without the slice map\n")

    state = build_state(files, m, opts["task"])
    qs = questions()
    payload = {"model": opts["model"], "state": state, "questions": qs}

    if opts["dry_run"]:
        stdout.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        return EXIT_OK

    if transport is None:
        key = environ.get(API_KEY_ENV, "")
        if not key:
            msg = f"heimdall review: {API_KEY_ENV} is not set"
            if opts["allow_missing_key"]:
                stdout.write(msg + " — skipping the Jev review\n")
                if opts["markdown"]:
                    _write(
                        opts["markdown"],
                        cwd,
                        "<!-- heimdall-review -->\n## Heimdall review: skipped\n\n_No `TYPESAFE_API_KEY` available to this run (forks do not receive secrets)._\n",
                    )
                return EXIT_OK
            stderr.write(msg + "\n")
            return EXIT_ERROR
        transport = http_transport(key, environ.get(API_URL_ENV, API_URL))

    try:
        response = transport(payload)
    except ReviewError as e:
        stderr.write(f"heimdall review: {e}\n")
        return EXIT_ERROR
    answers = response.get("answers") if isinstance(response, dict) else None
    if not isinstance(answers, dict):
        stderr.write("heimdall review: malformed response (no `answers`)\n")
        return EXIT_ERROR

    verdict = decide(answers, state["facts"], str(response.get("model", opts["model"])), response.get("usage") or {})
    stdout.write(render_table(verdict, qs))
    if opts["json"]:
        _write(
            opts["json"],
            cwd,
            json.dumps(
                {
                    "outcome": verdict.outcome,
                    "reasons": verdict.reasons,
                    "answers": answers,
                    "facts": state["facts"],
                    "model": verdict.model,
                    "usage": verdict.usage,
                },
                indent=2,
            ),
        )
    if opts["markdown"]:
        _write(opts["markdown"], cwd, render_markdown(verdict, state, qs))
    _telemetry(cwd, verdict, state["facts"], opts["session"])
    return verdict.exit_code
