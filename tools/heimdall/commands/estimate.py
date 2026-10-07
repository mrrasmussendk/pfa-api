"""``heimdall estimate <file-or-dir>`` — prints the token estimate as a bare integer.
Directories walk ``*.py`` recursively (what EIT100 budgets). Same estimator Eitri checks
with, so the number cannot drift from the gate."""

from __future__ import annotations

import os
from typing import TextIO

from brokkr.tokenization import estimate


def _read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def run(args: list[str], stdout: TextIO, stderr: TextIO, cwd: str) -> int:
    if len(args) != 1:
        stderr.write("usage: heimdall estimate <file-or-dir>\n")
        return 2
    target = os.path.abspath(os.path.join(cwd, args[0]))
    if os.path.isfile(target):
        stdout.write(f"{estimate(_read(target))}\n")
        return 0
    if os.path.isdir(target):
        total = 0
        for dirpath, dirnames, filenames in os.walk(target):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d != "__pycache__")
            for name in filenames:
                if name.endswith(".py"):
                    total += estimate(_read(os.path.join(dirpath, name)))
        stdout.write(f"{total}\n")
        return 0
    stderr.write(f"heimdall estimate: no such file or directory: {args[0]}\n")
    return 1
