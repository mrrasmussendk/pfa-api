"""Testable entry point: ``main`` passes the real console streams and CWD."""

from __future__ import annotations

import os
import sys
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
    "  e2e new <feature>/<name> --method .. --given .. --when .. --then .. [--tool ..]\n"
    "                            scaffold a scenario and a failing test per tool"
)


def run(args: list[str], stdin: TextIO, stdout: TextIO, stderr: TextIO, root: str) -> int:
    if not args:
        stderr.write(USAGE + "\n")
        return 2
    cmd, rest = args[0], args[1:]
    if cmd == "estimate":
        from .commands.estimate import run as estimate

        return estimate(rest, stdout, stderr, root)
    if cmd == "map":
        from .commands.map import run as map_

        return map_(rest, stdout, stderr, root)
    if cmd == "hook":
        from .commands.hook import run as hook

        return hook(stdin, stderr, root)
    if cmd == "drift":
        from .commands.drift import run as drift

        return drift(stdout, stderr, root)
    if cmd == "review":
        from .commands.review import Streams
        from .commands.review import run as review

        return review(rest, Streams(stdin, stdout, stderr), root)
    if cmd == "e2e":
        from .commands.e2e import Streams as E2EStreams
        from .commands.e2e import run as e2e

        return e2e(rest, E2EStreams(stdin, stdout, stderr), root)
    stderr.write(USAGE + "\n")
    return 2


def main(argv: list[str] | None = None) -> int:
    # Byte-stable I/O regardless of console code page: stdin/stdout/stderr are UTF-8.
    # (Feedback text contains em dashes; Windows OEM code pages would mangle them.)
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass
    return run(sys.argv[1:] if argv is None else argv, sys.stdin, sys.stdout, sys.stderr, os.getcwd())
