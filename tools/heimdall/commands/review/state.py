"""The bounded state: what one Jev call may see. Facts Heimdall and Eitri can compute are stated
as facts, so the model spends its judgment on what only judgment can decide."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...model import MapModel
from ...pathutil import app_of, is_contract_path, is_kernel_path, is_route_path, norm, shared_of, slice_of
from ...sensors.boundary_edits import FAN_IN_FREEZE
from ...shape import FUNCTION_LINES, FUNCTION_PARAMETERS, oversized, shape_line
from .diff import FileChange

MAX_FILE_CHARS = 12_000  # per file; the rest is summarised as "+N/-M more lines"
MAX_STATE_CHARS = 60_000  # all files together
# The order the budget is spent in: the code the walls are about first, prose last. A wide diff
# then truncates docs and tooling, never the slice the judge is asked about.
_AREA_PRIORITY = ("slice:", "contract:", "kernel", "app", "service", "shared:", "tests", "tooling", "ci", "docs", "other")

_FIX_LINE = re.compile(r"^\*\*Fix[^*]*:\*\*\s*(.+?)\s*$", re.MULTILINE)
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")

__all__ = ["area_of", "build_state", "fix_from_docs", "function_shape", "shape_line", "wall_findings"]


def area_of(path: str, m: MapModel | None) -> str:
    """Where a path sits in the architecture — the Heimdall half of the state."""
    p = norm(path)
    if m is not None:
        s = slice_of(p, m)
        if s is not None:
            return f"contract:{s}" if is_contract_path(p) else f"slice:{s}"
        if is_kernel_path(p, m):
            return "kernel"
        sh = shared_of(p, m)
        if sh is not None:
            return f"shared:{sh}"
        if app_of(p, m) is not None:
            return "app"
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


def _priority(area: str) -> int:
    return next((i for i, prefix in enumerate(_AREA_PRIORITY) if area == prefix or area.startswith(prefix)), len(_AREA_PRIORITY))


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    cut = text[:limit]
    cut = cut[: cut.rfind("\n")] if "\n" in cut else cut
    dropped = text.count("\n") - cut.count("\n")
    return cut + f"\n… [{dropped} more diff lines not shown]", True


def fix_from_docs(root: Path, help_link: str) -> str:
    """The ``**Fix:**`` line of a rule's page under ``harness/rules/`` — the rule doc is the single
    source of what to do about a wall, so the PR comment never drifts from it."""
    try:
        text = (root / help_link).read_text(encoding="utf-8")
    except OSError:
        return ""
    m = _FIX_LINE.search(text)
    return m.group(1) if m else ""


def wall_findings(cwd: str, files: list[FileChange]) -> list[dict[str, Any]]:
    """Eitri's errors on the changed files, as facts for the judge and a hard input to the policy.

    Deterministic and never argued with: a docstring cannot talk an import graph out of a
    violation. Returns ``[]`` when Eitri is not installed or the tree has no slices (reviews of
    a bare diff, tests), so the review still runs — ``ci.yml`` remains the gate for the whole tree."""
    try:
        from eitri import analyze
        from eitri.analyzer import EitriError
        from eitri.core import Severity
    except ImportError:  # pragma: no cover — the tools install together; stay reviewable without them
        return []
    changed = {norm(f.path) for f in files if f.status != "deleted"}
    root = Path(cwd).resolve()
    try:
        diags = analyze(root)
    except (EitriError, OSError, ValueError):
        return []
    out: list[dict[str, Any]] = []
    for d in diags:
        if d.severity != Severity.ERROR or d.path is None:
            continue
        try:
            rel = norm(str(Path(d.path).resolve().relative_to(root)))
        except ValueError:
            continue
        if rel in changed:
            out.append(
                {
                    "path": rel,
                    "line": d.line,
                    "rule": d.id,
                    "message": d.message,
                    "fix": fix_from_docs(root, d.descriptor.help_link),
                    "doc": d.descriptor.help_link,
                }
            )
    return sorted(out, key=lambda v: (v["path"], v["line"] or 0, v["rule"]))


def _touched_lines(f: FileChange) -> set[int]:
    """Line numbers in the new file that the diff adds, or that follow a removed line."""
    touched: set[int] = set()
    line = 0
    for row in f.hunks:
        m = _HUNK.match(row)
        if m:
            line = int(m.group(1))
        elif row.startswith("+"):
            touched.add(line)
            line += 1
        elif row.startswith("-"):
            touched.add(line)
        elif row.startswith(" "):
            line += 1
    return touched


def function_shape(cwd: str, files: list[FileChange], m: MapModel | None) -> list[dict[str, Any]]:
    """The changed functions over the shape limits, read from the working tree's AST: facts for the
    judge, so "single purpose" and "lean signatures" are asked about a named function at a line.

    Tests are left out (a scenario reads top-down however long it is); a file that is deleted, not
    Python or unparsable contributes nothing, so a review of a bare diff still runs."""
    out: list[dict[str, Any]] = []
    for f in files:
        path = norm(f.path)
        if f.status == "deleted" or f.binary or not path.endswith(".py") or area_of(path, m) == "tests":
            continue
        try:
            source = (Path(cwd) / path).read_text(encoding="utf-8")
        except (OSError, ValueError):
            continue
        out += oversized(path, source, _touched_lines(f))
    return sorted(out, key=lambda v: (v["path"], v["line"]))


@dataclass
class _Tally:
    """What the classified files add up to: the facts about the change as a whole."""

    slices: set[str] = field(default_factory=set)
    contracts: dict[str, int] = field(default_factory=dict)
    routes_changed: bool = False
    source_changed: bool = False
    code_changed: bool = False  # any Python outside tests: the craft questions are about this
    docs_changed: bool = False
    tests: set[str] = field(default_factory=set)
    truncated: list[str] = field(default_factory=list)

    def count(self, area: str, f: FileChange, m: MapModel | None) -> None:
        if f.status != "deleted" and norm(f.path).endswith(".py") and area != "tests":
            self.code_changed = True
        if area.startswith(("slice:", "contract:")):
            name = area.split(":", 1)[1]
            self.slices.add(name)
            self.source_changed = True
            if area.startswith("contract:"):
                self.contracts[name] = m.slices[name].fan_in if m and name in m.slices else 0
            if m is not None and is_route_path(f.path, m):
                self.routes_changed = True
        elif area in ("kernel", "app", "service") or area.startswith("shared:"):
            self.source_changed = True
        elif area == "tests":
            self.tests.add(norm(f.path))
        elif area == "docs":
            self.docs_changed = True


def _entries(files: list[FileChange], m: MapModel | None, tally: _Tally) -> list[dict[str, Any]]:
    """One entry per file, source first (see ``_AREA_PRIORITY``), git order within an area; the
    character budget is spent in that order and ``tally`` learns what the files add up to."""
    budget = MAX_STATE_CHARS
    entries: list[dict[str, Any]] = []
    classified = sorted(((area_of(f.path, m), f) for f in files), key=lambda pair: _priority(pair[0]))
    for area, f in classified:
        tally.count(area, f, m)
        if f.binary:
            text = "[binary]"
        else:
            text, cut = _truncate(f.text, min(MAX_FILE_CHARS, max(budget, 400)))
            if cut:
                tally.truncated.append(f.path)
        budget -= len(text)
        entries.append({"path": norm(f.path), "status": f.status, "area": area, "added": f.added, "removed": f.removed, "diff": text})
    return entries


def _architecture(m: MapModel | None) -> dict[str, Any]:
    """The map as the architecture's promise, and the walls in one sentence each."""
    if m is None:
        return {}
    return {
        "kernel": m.kernel_dir or m.kernel,
        "application": m.app_dir,
        "http_edge": m.routes_dir,
        "shared_packages": list(m.shared),
        "slices": {name: {"depends_on": info.depends_on, "contract_fan_in": info.fan_in} for name, info in sorted(m.slices.items())},
        "rules": [
            "a slice imports another slice only through its contract/ package, never internal/ or module.py",
            "contract/ modules import only the standard library, the kernel and their own contract — no FastAPI, no pydantic",
            "importing another feature's contract requires that feature declared in feature.json",
            "no dynamic imports, sys.path edits or wildcard imports inside slices",
            "a slice (feature) imports nothing above itself: no HTTP framework, nothing from the application or its entry points",
            "the application consumes a feature through its contract/; only the composition root (application.py) imports a feature's module.py; nothing imports internal/",
            "errors leave as RFC 9457 problem documents raised through the shared Problem type, never HTTPException",
            f"a contract with fan-in >= {FAN_IN_FREEZE} is frozen: additive changes only",
            "handlers return Result and never raise for domain reasons; routes decide status codes",
        ],
    }


def build_state(
    files: list[FileChange],
    m: MapModel | None,
    task: str | None,
    walls: list[dict[str, Any]] | None = None,
    shape: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The JSON object Jev judges: the task, the architecture, the facts and the files."""
    tally = _Tally()
    entries = _entries(files, m, tally)
    facts = {
        "files_changed": len(files),
        "lines_added": sum(f.added for f in files),
        "lines_removed": sum(f.removed for f in files),
        "slices_touched": sorted(tally.slices),
        "cross_slice_change": len(tally.slices) > 1,
        "contracts_touched": {k: {"fan_in": v, "frozen": v >= FAN_IN_FREEZE} for k, v in sorted(tally.contracts.items())},
        "routes_changed": tally.routes_changed,
        "source_changed": tally.source_changed,
        "code_changed": tally.code_changed,
        "tests_changed": sorted(tally.tests),
        "docs_changed": tally.docs_changed,
        "files_truncated": tally.truncated,
        "wall_violations": list(walls or []),
        "function_shape": list(shape or []),
        "function_limits": {"lines": FUNCTION_LINES, "params": FUNCTION_PARAMETERS},
    }
    return {"task": task or "(no task statement given)", "architecture": _architecture(m), "facts": facts, "files": entries}
