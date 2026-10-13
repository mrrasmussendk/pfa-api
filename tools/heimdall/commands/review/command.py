"""The entry point: arguments → diff → state → one call about the diff and one per edited application
file, whole → policy → table, files, telemetry, exit code."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping
from typing import Any, TextIO

from ...model import Finding, MapModel
from ...store import UnreadableMap, append_findings, heimdall_dir, load_map
from .base import EXIT_ERROR, EXIT_OK, ReviewError, Streams
from .craft import craft_files, craft_state
from .diff import git_diff, parse_unified_diff
from .policy import Verdict, decide
from .questions import file_questions, questions
from .report import render_markdown, render_table
from .state import build_state, function_shape, wall_findings
from .transport import API_KEY_ENV, API_URL, API_URL_ENV, DEFAULT_MODEL, Transport, http_transport

USAGE = (
    "usage: heimdall review [--base <ref> | --diff <file|->] [--task <text>] [--model jev-latest]\n"
    "                       [--json <out>] [--markdown <out>] [--dry-run] [--allow-missing-key]\n"
    "                       [--session <id>]\n"
    "  default diff: `git diff HEAD` (working tree); --base <ref> uses `git diff <ref>...HEAD`"
)

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
        }
        _write(opts["json"], cwd, json.dumps(report, indent=2))
    if opts["markdown"]:
        _write(opts["markdown"], cwd, render_markdown(verdict, state, qs))
    _telemetry(cwd, verdict, state["facts"], opts["session"])


def _payloads(opts: dict[str, Any], io: Streams, cwd: str) -> dict[str, Any] | int:
    """Every call the review makes — ``diff``: the one about the change; ``files``: one per edited application
    file, whole — or the exit code when there is no diff to judge. The diff's facts name the files judged whole."""
    try:
        files = parse_unified_diff(_read_diff(opts, io.stdin, cwd))
    except (ReviewError, OSError) as e:
        io.stderr.write(f"heimdall review: {e}\n")
        return EXIT_ERROR
    if not files:
        io.stdout.write("heimdall review: nothing to review (empty diff)\n")
        return EXIT_OK
    m = _load_map(cwd, io.stderr)
    shape = function_shape(cwd, files, m)
    state = build_state(files, m, opts["task"], wall_findings(cwd, files), shape)
    crafts = [craft_state(f, opts["task"], shape) for f in craft_files(cwd, files, m)]
    state["facts"]["craft_files"] = [c["file"]["path"] for c in crafts]
    state["facts"]["craft_truncated"] = [c["file"]["path"] for c in crafts if c["facts"]["truncated"]]
    return {
        "diff": {"model": opts["model"], "state": state, "questions": questions()},
        "files": [{"model": opts["model"], "state": c, "questions": file_questions()} for c in crafts],
    }


def run(args: list[str], io: Streams, cwd: str, transport: Transport | None = None, env: Mapping[str, str] | None = None) -> int:
    environ: Mapping[str, str] = os.environ if env is None else env
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
    if transport is None:
        found = _transport_from_env(opts, environ, io, cwd)
        if isinstance(found, int):
            return found
        transport = found
    response = _judge(payloads["diff"], transport, io.stderr)
    if response is None:
        return EXIT_ERROR
    per_file = _judge_files(payloads["files"], transport, io.stderr)
    if per_file is None:
        return EXIT_ERROR
    state = payloads["diff"]["state"]
    file_answers = {c["state"]["file"]["path"]: r["answers"] for c, r in zip(payloads["files"], per_file, strict=True)}
    verdict = decide(
        response["answers"], state["facts"], str(response.get("model", opts["model"])), _usage([response, *per_file]), file_answers
    )
    _report(opts, verdict, state, io, cwd)
    return verdict.exit_code
