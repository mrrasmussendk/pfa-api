"""Changed lines are tested: of the application lines a change adds or edits, at least ``--floor`` percent must
run under the test suite.

    python -m coverage run -m pytest -q && python -m coverage json -q
    python tools/diff_coverage.py --base origin/main            # exit 1 below the floor

The change is everything since the merge base with ``--base``: committed, uncommitted and untracked. Only
Python under ``src/`` counts, and only lines coverage calls executable, so a docstring or a blank line never
lowers the number. A changed file no test imports counts as wholly untested. The report names each file
and the line ranges no test ran, which is where the next test goes."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FLOOR = 90.0
SOURCE = "src/"
_FILE = re.compile(r"^\+\+\+ (?:b/(.+)|/dev/null)$")
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def changed_lines(diff: str) -> dict[str, set[int]]:
    """``path`` → the new-side lines a ``--unified=0`` diff adds, for Python files under ``SOURCE``."""
    out: dict[str, set[int]] = {}
    path = None
    for line in diff.splitlines():
        if m := _FILE.match(line):
            new = (m.group(1) or "").rstrip("\t")  # git ends the header with a tab when the name holds a space
            path = new if new.startswith(SOURCE) and new.endswith(".py") else None
        elif (m := _HUNK.match(line)) and path:
            start, count = int(m.group(1)), int(m.group(2) or 1)
            out.setdefault(path, set()).update(range(start, start + count))
    return {p: lines for p, lines in out.items() if lines}


@dataclass(frozen=True)
class FileReport:
    path: str
    executable: frozenset[int]  # changed lines coverage could run
    missed: frozenset[int]  # of those, the lines no test ran

    @property
    def ranges(self) -> str:
        """The missed lines as ``3-5, 9``."""
        spans: list[list[int]] = []
        for n in sorted(self.missed):
            if spans and n == spans[-1][1] + 1:
                spans[-1][1] = n
            else:
                spans.append([n, n])
        return ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in spans)


def report(changed: dict[str, set[int]], coverage: dict[str, dict[str, list[int]]]) -> list[FileReport]:
    """One report per changed file, from coverage's JSON ``files`` table. A file coverage never saw ran under
    no test, so every changed line of it is missed."""
    out = []
    for path, lines in sorted(changed.items()):
        data = coverage.get(path)
        if data is None:
            out.append(FileReport(path, frozenset(lines), frozenset(lines)))
            continue
        executable = lines & (set(data["executed_lines"]) | set(data["missing_lines"]))
        out.append(FileReport(path, frozenset(executable), frozenset(executable & set(data["missing_lines"]))))
    return [r for r in out if r.executable]


def percent(reports: list[FileReport]) -> float:
    total = sum(len(r.executable) for r in reports)
    return 100.0 if total == 0 else 100.0 * (total - sum(len(r.missed) for r in reports)) / total


# The only options this module passes to git; anything else starting with "-" is refused at the call.
_GIT_OPTIONS = frozenset({"--no-color", "--unified=0", "-z", "--others", "--exclude-standard", "--"})


def _git(root: Path, *args: str) -> str:
    """git's output in ``root``, with paths unquoted whatever characters they hold."""
    unexpected = [a for a in args[1:] if a.startswith("-") and a not in _GIT_OPTIONS]
    if unexpected:
        raise SystemExit(f"refusing git {args[0]} with {unexpected!r}: a ref or path, not an option, is expected there")
    cmd = ["git", "-C", str(root), "-c", "core.quotePath=false", *args]
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", check=True).stdout


def diff_since(base: str, root: Path = REPO) -> str:
    """The change under ``SOURCE`` since the merge base with ``base``, as a ``--unified=0`` diff: tracked changes,
    then each untracked file as wholly added."""
    if base.startswith("-"):
        raise SystemExit(f"--base {base!r} must be a ref, not an option")
    rev = _git(root, "merge-base", base, "HEAD").strip()
    diff = _git(root, "diff", "--no-color", "--unified=0", rev, "--", SOURCE)
    for path in (p for p in _git(root, "ls-files", "-z", "--others", "--exclude-standard", "--", SOURCE).split("\0") if p):
        count = len((root / path).read_text(encoding="utf-8", errors="replace").splitlines())
        diff += f"+++ b/{path}\n@@ -0,0 +1,{count} @@\n"
    return diff


def coverage_files(path: Path, root: Path = REPO) -> dict[str, dict[str, list[int]]]:
    """Coverage's JSON ``files`` table keyed the way the diff names files: posix paths relative to ``root``.
    Coverage writes relative paths with ``relative_files`` and absolute ones without; Windows writes backslashes."""
    files = json.loads(path.read_text(encoding="utf-8")).get("files", {})
    return {_repo_path(key, root): data for key, data in files.items()}


def _repo_path(key: str, root: Path) -> str:
    posix = key.replace("\\", "/")
    if not Path(posix).is_absolute():
        return posix
    try:
        return Path(posix).resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return posix  # outside the repository: no changed line can match it


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="diff_coverage", description=__doc__.split("\n\n")[0])
    p.add_argument("--base", required=True, help="the change is everything since the merge base with this ref")
    p.add_argument("--floor", type=float, default=FLOOR, help=f"lowest share of changed lines tests must run (default {FLOOR:g})")
    p.add_argument("--coverage", default="coverage.json", help="coverage's JSON report (default coverage.json)")
    args = p.parse_args(argv)
    reports = report(changed_lines(diff_since(args.base)), coverage_files(REPO / args.coverage))
    share = percent(reports)
    lines = sum(len(r.executable) for r in reports)
    print(f"diff coverage: {share:.1f}% of {lines} changed application line(s) run under the tests (floor {args.floor:g}%)")
    print("".join(f"- {r.path}: lines {r.ranges} run under no test\n" for r in reports if r.missed), end="")
    return 0 if share >= args.floor else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
