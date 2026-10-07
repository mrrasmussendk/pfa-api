"""The entry point: arguments → diff → state → one call → policy → table, files, telemetry, exit code."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping
from typing import Any, TextIO

from ...model import Finding, MapModel
from .base import EXIT_ERROR, EXIT_OK, ReviewError
from .diff import git_diff, parse_unified_diff
from .policy import Verdict, decide
from .questions import questions
from .report import render_markdown, render_table
from .state import build_state, wall_findings
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


def _read_diff(opts: dict[str, Any], stdin: TextIO, cwd: str) -> str:
    if opts["diff"] == "-":
        return stdin.read()
    if opts["diff"]:
        with open(os.path.join(cwd, opts["diff"]), encoding="utf-8", errors="replace") as f:
            return f.read()
    return git_diff(cwd, opts["base"])


def _load_map(cwd: str, stderr: TextIO) -> MapModel | None:
    map_path = os.path.join(cwd, ".heimdall", "map.json")
    if not os.path.isfile(map_path):
        return None
    try:
        return MapModel.load(map_path)
    except (OSError, ValueError, TypeError) as e:
        stderr.write(f"heimdall review: unreadable .heimdall/map.json ({e}); reviewing without the slice map\n")
        return None


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
        diff = _read_diff(opts, stdin, cwd)
    except (ReviewError, OSError) as e:
        stderr.write(f"heimdall review: {e}\n")
        return EXIT_ERROR

    files = parse_unified_diff(diff)
    if not files:
        stdout.write("heimdall review: nothing to review (empty diff)\n")
        return EXIT_OK

    state = build_state(files, _load_map(cwd, stderr), opts["task"], wall_findings(cwd, files))
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
                    _write(opts["markdown"], cwd, _SKIPPED_COMMENT)
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
    stdout.write(render_table(verdict, qs, state["facts"]))
    if opts["json"]:
        report = {
            "outcome": verdict.outcome,
            "reasons": verdict.reasons,
            "advice": verdict.advice,
            "answers": answers,
            "facts": state["facts"],
            "model": verdict.model,
            "usage": verdict.usage,
        }
        _write(opts["json"], cwd, json.dumps(report, indent=2))
    if opts["markdown"]:
        _write(opts["markdown"], cwd, render_markdown(verdict, state, qs))
    _telemetry(cwd, verdict, state["facts"], opts["session"])
    return verdict.exit_code
