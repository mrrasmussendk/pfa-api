"""The entry point: arguments → diff → the changed functions → narrow checks per function, route and file,
asked of Jev in one call, and clean code of each edited application file whole → policy → (PR pipeline only)
Claude verifies each failing or unsure check → table, files, telemetry, exit code."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, NamedTuple, TextIO

from brokkr import estimate

from ...model import Finding, MapModel
from ...pathutil import norm
from ...store import UnreadableMap, append_findings, heimdall_dir, load_map
from .base import EXIT_ERROR, EXIT_OK, ReviewError, Streams
from .claude import KEY_ENVS, claude_check
from .craft import craft_files, craft_state
from .diff import FileChange, git_diff, parse_unified_diff
from .items import Item, changed_functions
from .policy import Judgment, Verdict, decide, evaluate
from .questions import Asked, ask, file_questions
from .report import render_markdown, render_table
from .state import Known, build_state, function_shape, wall_findings
from .transport import API_KEY_ENV, API_URL, API_URL_ENV, DEFAULT_MODEL, Transport, http_transport
from .vendored import is_vendored, vendored_patterns
from .verify import Check, Evidence, read_sources, settle

USAGE = (
    "usage: heimdall review [--base <ref> | --diff <file|->] [--task <text>] [--model jev-latest]\n"
    "                       [--json <out>] [--markdown <out>] [--dry-run] [--allow-missing-key]\n"
    "                       [--session <id>] [--claude]\n"
    "  default diff: `git diff HEAD` (working tree); --base <ref> uses `git diff <ref>...HEAD`\n"
    "  --claude: the PR pipeline; Claude verifies each failing check and only a confirmed one blocks\n"
    "            (needs ANTHROPIC_API_KEY). Without it, the agent pipeline: Jev's failing checks block"
)


class Seams(NamedTuple):
    """What a test replaces: Jev's transport, the environment keys are read from, and the verifier. ``check`` left
    out means Claude when a key is set, and no verifier otherwise."""

    transport: Transport | None = None
    env: Mapping[str, str] | None = None
    check: Check | None = None


_SKIPPED_COMMENT = (
    "<!-- heimdall-review -->\n## Heimdall review: skipped\n\n"
    "_No `TYPESAFE_API_KEY` available to this run (forks do not receive secrets)._\n"
)


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
        "claude": False,
        "session": "review",
    }
    i = 0
    while i < len(args):
        a = args[i]
        nxt = args[i + 1] if i + 1 < len(args) else None
        if a in ("--dry-run", "--allow-missing-key", "--claude"):
            opts[a[2:].replace("-", "_")] = True
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
    f = Finding(
        event="review",
        kind=v.outcome,
        slice=",".join(facts["slices_touched"]) or None,
        feedback="; ".join(v.reasons),
        sensor="jev",
        ts=time.time(),
        session=session,
    )
    append_findings(heimdall_dir(cwd), [f])


def _read_diff(opts: dict[str, Any], stdin: TextIO, cwd: str) -> str:
    if opts["diff"] == "-":
        return stdin.read()
    if opts["diff"]:
        with open(os.path.join(cwd, opts["diff"]), encoding="utf-8", errors="replace") as f:
            return f.read()
    return git_diff(cwd, opts["base"])


def _load_map(cwd: str, stderr: TextIO) -> MapModel | None:
    """The slice map, or ``None`` after saying why: a review without the map is still a review."""
    try:
        return load_map(heimdall_dir(cwd))
    except UnreadableMap as e:
        stderr.write(f"heimdall review: unreadable .heimdall/map.json ({e}); reviewing without the slice map\n")
        return None


def _transport_from_env(opts: dict[str, Any], environ: Mapping[str, str], io: Streams, cwd: str) -> Transport | int:
    """The real HTTP transport, or the exit code when there is no key: 0 and a "skipped" comment
    when the run allows it (forks), 3 otherwise."""
    key = environ.get(API_KEY_ENV, "")
    if key:
        return http_transport(key, environ.get(API_URL_ENV, API_URL))
    msg = f"heimdall review: {API_KEY_ENV} is not set"
    if not opts["allow_missing_key"]:
        io.stderr.write(msg + "\n")
        return EXIT_ERROR
    io.stdout.write(msg + " — skipping the Jev review\n")
    if opts["markdown"]:
        _write(opts["markdown"], cwd, _SKIPPED_COMMENT)
    return EXIT_OK


def _judge(payload: dict[str, Any], transport: Transport, stderr: TextIO) -> dict[str, Any] | None:
    """One call; ``None`` (after writing why) when the response is not a verdict about the code."""
    try:
        response = transport(payload)
    except ReviewError as e:
        stderr.write(f"heimdall review: {e}\n")
        return None
    answers = response.get("answers") if isinstance(response, dict) else None
    if not isinstance(answers, dict):
        stderr.write("heimdall review: malformed response (no `answers`)\n")
        return None
    return response


# Jev reads one request of 64k tokens, the state and every question in it (docs.typesafe.ai/models). Brokkr is
# within about 10% of a real tokenizer, so the margin keeps each batch under the limit.
MAX_REQUEST_TOKENS = 56_000


def _batches(diff: dict[str, Any]) -> list[dict[str, Any]]:
    """The checks call split so that each request, the state and its share of the questions, fits the limit. Jev
    answers each question in isolation, so a batch's answers mean what they would in one call."""
    room = MAX_REQUEST_TOKENS - estimate(json.dumps(diff["state"], ensure_ascii=False))
    batches: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    used = 0
    for key, q in diff["questions"].items():
        cost = estimate(json.dumps({key: q}, ensure_ascii=False))
        if current and used + cost > room:
            batches.append(current)
            current, used = {}, 0
        current[key] = q
        used += cost
    if current:
        batches.append(current)
    return [{**diff, "questions": b} for b in batches]


def _judge_checks(diff: dict[str, Any], transport: Transport, stderr: TextIO) -> dict[str, Any] | None:
    """Every batch of checks asked: one response with all the answers and the usage added up; ``None`` when a
    batch gave no verdict."""
    responses: list[dict[str, Any]] = []
    for payload in _batches(diff):
        response = _judge(payload, transport, stderr)
        if response is None:
            return None
        responses.append(response)
    answers = {key: a for r in responses for key, a in r["answers"].items()}
    return {"model": responses[0].get("model"), "answers": answers, "usage": _usage(responses)}


def _judge_files(payloads: list[dict[str, Any]], transport: Transport, stderr: TextIO) -> list[dict[str, Any]] | None:
    """The craft pass: one call per edited application file, whole. ``None`` when any of them is not a verdict."""
    responses: list[dict[str, Any]] = []
    for payload in payloads:
        response = _judge(payload, transport, stderr)
        if response is None:
            return None
        responses.append(response)
    return responses


def _usage(responses: list[dict[str, Any]]) -> dict[str, Any]:
    """The token counts of every call added up, so the report says what the whole review cost."""
    total: dict[str, Any] = {}
    for r in responses:
        for key, value in (r.get("usage") or {}).items():
            if isinstance(value, (int, float)):
                total[key] = total.get(key, 0) + value
    return total


def _report(opts: dict[str, Any], verdict: Verdict, state: dict[str, Any], io: Streams, cwd: str) -> None:
    """The table on stdout, the JSON and Markdown files when asked, and the telemetry line."""
    io.stdout.write(render_table(verdict, state["facts"]))
    if opts["json"]:
        report = {
            "outcome": verdict.outcome,
            "reasons": verdict.reasons,
            "advice": verdict.advice,
            "answers": verdict.answers,
            "files": verdict.files,
            "facts": state["facts"],
            "model": verdict.model,
            "usage": verdict.usage,
            "verification": verdict.verification,
            "checks": [{"check": r.check, "where": r.where, "p": r.p, "state": r.state} for r in verdict.rows],
        }
        _write(opts["json"], cwd, json.dumps(report, indent=2))
    if opts["markdown"]:
        _write(opts["markdown"], cwd, render_markdown(verdict, state))
    _telemetry(cwd, verdict, state["facts"], opts["session"])


@dataclass(frozen=True)
class Calls:
    """Every call the review makes: ``diff``, the checks about the change; ``files``, one per edited application
    file, whole; and what the policy and the verifier need to read the answers."""

    diff: dict[str, Any]
    files: list[dict[str, Any]]
    sources: list[str]
    asked: list[Asked]

    def printable(self) -> dict[str, Any]:
        return {"diff": self.diff, "files": self.files, "sources": self.sources}


def _function_state(items: list[Item]) -> dict[str, dict[str, Any]]:
    return {i.id: {"path": i.path, "line": i.line, "name": i.name, "route": i.route, "source": i.source} for i in items}


def _changed(opts: dict[str, Any], io: Streams, cwd: str) -> tuple[list[FileChange], int] | int:
    """The changed files the review judges and how many vendored ones it left out, or the exit code."""
    try:
        changed = parse_unified_diff(_read_diff(opts, io.stdin, cwd))
    except (ReviewError, OSError) as e:
        io.stderr.write(f"heimdall review: {e}\n")
        return EXIT_ERROR
    patterns = vendored_patterns(cwd)
    files = [f for f in changed if not is_vendored(f.path, patterns)]
    if not files:
        io.stdout.write("heimdall review: nothing to review (empty diff)\n")
        return EXIT_OK
    return files, len(changed) - len(files)


def _payloads(opts: dict[str, Any], io: Streams, cwd: str) -> Calls | int:
    """Every call the review makes, or the exit code when there is no diff to judge."""
    found = _changed(opts, io, cwd)
    if isinstance(found, int):
        return found
    files, vendored = found
    m = _load_map(cwd, io.stderr)
    shape = function_shape(cwd, files, m)
    items, not_asked = changed_functions(cwd, files, m)
    state = build_state(files, m, opts["task"], Known(wall_findings(cwd, files), shape, _function_state(items)))
    qs, asked = ask(items, [norm(f.path) for f in files])
    crafts = [craft_state(f, opts["task"], shape) for f in craft_files(cwd, files, m)]
    facts = state["facts"]
    facts.update(vendored_files=vendored, functions_asked=len(items), functions_not_asked=not_asked)
    facts.update(
        craft_files=[c["file"]["path"] for c in crafts], craft_truncated=[c["file"]["path"] for c in crafts if c["facts"]["truncated"]]
    )
    diff = {"model": opts["model"], "state": state, "questions": qs}
    craft_calls = [{"model": opts["model"], "state": c, "questions": file_questions()} for c in crafts]
    return Calls(diff, craft_calls, _source_paths(state, files), asked)


def _source_paths(state: dict[str, Any], files: list[FileChange]) -> list[str]:
    """The changed files the verifier reads whole, in the order Jev's budget ranked them; deleted files have none."""
    shown = [e["path"] for e in state["files"] if e["status"] != "deleted"]
    return shown + [norm(f.path) for f in files if f.status != "deleted" and norm(f.path) not in shown]


def _jev(calls: Calls, transport: Transport, opts: dict[str, Any], stderr: TextIO) -> Verdict | None:
    """Jev's verdict on the checks and on each file judged whole; ``None`` when a call gave no verdict."""
    response = _judge_checks(calls.diff, transport, stderr)
    per_file = _judge_files(calls.files, transport, stderr) if response is not None else None
    if response is None or per_file is None:
        return None
    file_answers = {c["state"]["file"]["path"]: r["answers"] for c, r in zip(calls.files, per_file, strict=True)}
    answers = response["answers"]
    model = str(response.get("model", opts["model"]))
    judgment = Judgment(evaluate(answers, calls.asked), file_answers, answers, model, _usage([response, *per_file]))
    return decide(judgment, calls.diff["state"]["facts"])


def _checker(seams: Seams, environ: Mapping[str, str]) -> Check | None:
    """The PR pipeline's verifier: Claude when a key is set; ``None`` (every failing check escalates) without one."""
    if seams.check is not None:
        return seams.check
    return claude_check if any(environ.get(k) for k in KEY_ENVS) else None


def run(args: list[str], io: Streams, cwd: str, seams: Seams | None = None) -> int:
    seams = seams or Seams()
    environ: Mapping[str, str] = os.environ if seams.env is None else seams.env
    try:
        opts = _parse(args)
    except ReviewError as e:
        io.stderr.write(str(e) + "\n")
        return 2
    calls = _payloads(opts, io, cwd)
    if isinstance(calls, int):
        return calls
    if opts["dry_run"]:
        io.stdout.write(json.dumps(calls.printable(), indent=2, ensure_ascii=False) + "\n")
        return EXIT_OK
    transport = seams.transport or _transport_from_env(opts, environ, io, cwd)
    if isinstance(transport, int):
        return transport
    verdict = _jev(calls, transport, opts, io.stderr)
    if verdict is None:
        return EXIT_ERROR
    state = calls.diff["state"]
    if opts["claude"]:
        evidence = Evidence(opts["task"] or "", state["files"], read_sources(cwd, calls.sources))
        verdict = settle(verdict, _checker(seams, environ), evidence)
    _report(opts, verdict, state, io, cwd)
    return verdict.exit_code
