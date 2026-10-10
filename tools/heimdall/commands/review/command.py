"""The entry point: arguments → diff → state → one call about the diff and one per edited application
file, whole (Jev) → the same questions answered by Claude, with evidence → policy on Claude's answers, each
judged block standing only on its evidence → table (Claude beside Jev), files, telemetry, exit code."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Mapping
from typing import Any, NamedTuple, TextIO

from ...model import Finding, MapModel
from ...pathutil import norm
from ...store import UnreadableMap, append_findings, heimdall_dir, load_map
from .base import EXIT_ERROR, EXIT_OK, ReviewError, Streams
from .claude import KEY_ENVS, ClaudeReview, ReviewInquiry, claude_review
from .craft import craft_files, craft_state
from .diff import FileChange, git_diff, parse_unified_diff
from .policy import Verdict, decide
from .questions import file_questions, questions
from .report import render_markdown, render_table
from .state import build_state, function_shape, wall_findings
from .transport import API_KEY_ENV, API_URL, API_URL_ENV, DEFAULT_MODEL, Transport, http_transport
from .vendored import is_vendored, vendored_patterns
from .verify import Check, Evidence, VerifyError, anchored, read_sources, settle

USAGE = (
    "usage: heimdall review [--base <ref> | --diff <file|->] [--task <text>] [--model jev-latest]\n"
    "                       [--json <out>] [--markdown <out>] [--dry-run] [--allow-missing-key]\n"
    "                       [--session <id>] [--claude]\n"
    "  default diff: `git diff HEAD` (working tree); --base <ref> uses `git diff <ref>...HEAD`\n"
    "  --claude: Claude answers the review and decides (CI only; needs ANTHROPIC_API_KEY). Without it,\n"
    "            Jev's verdict stands and each of its judged blocks escalates as unverified"
)


Reviewer = Callable[[ReviewInquiry], ClaudeReview]


class Seams(NamedTuple):
    """What a test replaces: Jev's transport, the environment keys are read from, and Claude. ``claude`` left out
    means Claude when a key is set, and Jev's verdict alone, every judged block unverified, otherwise."""

    transport: Transport | None = None
    env: Mapping[str, str] | None = None
    claude: Reviewer | None = None


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
    qs = questions()
    io.stdout.write(render_table(verdict, qs, state["facts"]))
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
            "jev": {"outcome": verdict.jev.outcome, "answers": verdict.jev.answers, "files": verdict.jev.files} if verdict.jev else None,
        }
        _write(opts["json"], cwd, json.dumps(report, indent=2))
    if opts["markdown"]:
        _write(opts["markdown"], cwd, render_markdown(verdict, state, qs))
    _telemetry(cwd, verdict, state["facts"], opts["session"])


def _payloads(opts: dict[str, Any], io: Streams, cwd: str) -> dict[str, Any] | int:
    """Every call the review makes — ``diff``: the one about the change; ``files``: one per edited application
    file, whole — or the exit code when there is no diff to judge. The diff's facts name the files judged whole."""
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
    m = _load_map(cwd, io.stderr)
    shape = function_shape(cwd, files, m)
    state = build_state(files, m, opts["task"], wall_findings(cwd, files), shape)
    crafts = [craft_state(f, opts["task"], shape) for f in craft_files(cwd, files, m)]
    state["facts"]["vendored_files"] = len(changed) - len(files)
    state["facts"]["craft_files"] = [c["file"]["path"] for c in crafts]
    state["facts"]["craft_truncated"] = [c["file"]["path"] for c in crafts if c["facts"]["truncated"]]
    return {
        "diff": {"model": opts["model"], "state": state, "questions": questions()},
        "files": [{"model": opts["model"], "state": c, "questions": file_questions()} for c in crafts],
        "sources": _source_paths(state, files),
    }


def _source_paths(state: dict[str, Any], files: list[FileChange]) -> list[str]:
    """The changed files the verifier reads whole, in the order Jev's budget ranked them; deleted files have none."""
    shown = [e["path"] for e in state["files"] if e["status"] != "deleted"]
    return shown + [norm(f.path) for f in files if f.status != "deleted" and norm(f.path) not in shown]


def _jev(payloads: dict[str, Any], transport: Transport, opts: dict[str, Any], stderr: TextIO) -> Verdict | None:
    """Jev's verdict on the diff and on each file judged whole; ``None`` when a call gave no verdict."""
    response = _judge(payloads["diff"], transport, stderr)
    per_file = _judge_files(payloads["files"], transport, stderr) if response is not None else None
    if response is None or per_file is None:
        return None
    state = payloads["diff"]["state"]
    file_answers = {c["state"]["file"]["path"]: r["answers"] for c, r in zip(payloads["files"], per_file, strict=True)}
    model = str(response.get("model", opts["model"]))
    return decide(response["answers"], state["facts"], model, _usage([response, *per_file]), file_answers)


def _reviewer(opts: dict[str, Any], seams: Seams, environ: Mapping[str, str]) -> Reviewer | None:
    """Claude, only when the run asks for it with ``--claude``: the PR workflow does, a local run and the Stop hook
    do not, so a key in a developer's environment never makes a paid call on its own."""
    if not opts["claude"]:
        return None
    if seams.claude is not None:
        return seams.claude
    return claude_review if any(environ.get(k) for k in KEY_ENVS) else None


def _inquiry(opts: dict[str, Any], payloads: dict[str, Any], sources: dict[str, str]) -> ReviewInquiry:
    state = payloads["diff"]["state"]
    craft = list(state["facts"].get("craft_files") or [])
    return ReviewInquiry(
        opts["task"] or "", state["facts"], state["architecture"], questions(), file_questions(), craft, state["files"], sources
    )


def _failed(error: VerifyError) -> Check:
    def check(_: Any) -> Any:
        raise error

    return check


def _claude(jev: Verdict, review: Reviewer | None, inquiry: ReviewInquiry) -> Verdict:
    """Claude's verdict with Jev's beside it; Jev's alone, each judged block unverified, when Claude gave none."""
    evidence = Evidence(inquiry.task, inquiry.diff, inquiry.sources)
    if review is None:
        return settle(jev, None, evidence)
    try:
        c = review(inquiry)
    except VerifyError as e:
        return settle(jev, _failed(e), evidence)
    verdict = settle(decide(c.answers, inquiry.facts, c.model, c.usage, c.files), anchored(c.answers, c.files), evidence)
    verdict.jev = jev
    return verdict


def run(args: list[str], io: Streams, cwd: str, seams: Seams | None = None) -> int:
    seams = seams or Seams()
    environ: Mapping[str, str] = os.environ if seams.env is None else seams.env
    try:
        opts = _parse(args)
    except ReviewError as e:
        io.stderr.write(str(e) + "\n")
        return 2
    payloads = _payloads(opts, io, cwd)
    if isinstance(payloads, int):
        return payloads
    if opts["dry_run"]:
        io.stdout.write(json.dumps(payloads, indent=2, ensure_ascii=False) + "\n")
        return EXIT_OK
    transport = seams.transport or _transport_from_env(opts, environ, io, cwd)
    if isinstance(transport, int):
        return transport
    verdict = _jev(payloads, transport, opts, io.stderr)
    if verdict is None:
        return EXIT_ERROR
    inquiry = _inquiry(opts, payloads, read_sources(cwd, payloads["sources"]))
    verdict = _claude(verdict, _reviewer(opts, seams, environ), inquiry)
    _report(opts, verdict, payloads["diff"]["state"], io, cwd)
    return verdict.exit_code
