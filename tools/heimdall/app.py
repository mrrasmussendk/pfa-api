"""Testable entry point: ``main`` passes the real console streams and CWD."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from typing import TextIO

USAGE = (
    "usage: heimdall <hook|map|drift|estimate|review|e2e> [args]\n"
    "  hook                      read a PostToolUse event on stdin, run sensors, exit 2 on feedback\n"
    "  map --root <dir> [--budget 15000] [--kernel <name>]\n"
    "  drift                     telemetry vs map: per-session table, then per-slice aggregate\n"
    "  estimate <file-or-dir>    token estimate (same estimator Eitri checks with)\n"
    "  review [--base <ref>|--diff <file|->] [--task ..] [--json ..] [--markdown ..] [--dry-run]\n"
    "                            Jev (TypeSafe AI) judges the diff; policy in code; exit 0/1/2/3\n"
    "  e2e [--base <ref>|--diff <file>] [--task ..] [--force]\n"
    "                            rerun the BDD scenarios the change reaches (Jev decides); exit 0/1/2/3\n"
    "  e2e --all                 run every scenario, whatever changed (what CI runs)\n"
    "  e2e new <feature>/<name> --method .. --given .. --when .. --then .. [--tool ..]\n"
    "                            scaffold a scenario and a failing test per tool"
)


# Each command's module is imported only when it runs: the hook fires on every tool call and must stay fast.
IO = tuple[TextIO, TextIO, TextIO]  # stdin, stdout, stderr


def _estimate(rest: list[str], io: IO, root: str) -> int:
    from .commands.estimate import run

    return run(rest, io[1], io[2], root)


def _map(rest: list[str], io: IO, root: str) -> int:
    from .commands.map import run

    return run(rest, io[1], io[2], root)


def _hook(rest: list[str], io: IO, root: str) -> int:
    from .commands.hook import run

    return run(io[0], io[2], root)


def _drift(rest: list[str], io: IO, root: str) -> int:
    from .commands.drift import run

    return run(io[1], io[2], root)


def _review(rest: list[str], io: IO, root: str) -> int:
    from .commands.review import Streams, run

    return run(rest, Streams(*io), root)


def _e2e(rest: list[str], io: IO, root: str) -> int:
    from .commands.e2e import Streams, run

    return run(rest, Streams(*io), root)


COMMANDS: dict[str, Callable[[list[str], IO, str], int]] = {
    "estimate": _estimate,
    "map": _map,
    "hook": _hook,
    "drift": _drift,
    "review": _review,
    "e2e": _e2e,
}


def run(args: list[str], stdin: TextIO, stdout: TextIO, stderr: TextIO, root: str) -> int:
    command = COMMANDS.get(args[0]) if args else None
    if command is None:
        stderr.write(USAGE + "\n")
        return 2
    return command(args[1:], (stdin, stdout, stderr), root)


def main(argv: list[str] | None = None) -> int:
    # Byte-stable I/O regardless of console code page: stdin/stdout/stderr are UTF-8.
    # (Feedback text contains em dashes; Windows OEM code pages would mangle them.)
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass
    return run(sys.argv[1:] if argv is None else argv, sys.stdin, sys.stdout, sys.stderr, os.getcwd())
