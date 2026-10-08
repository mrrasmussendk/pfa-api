"""The bounded state: what one Jev call may see. Facts Heimdall and Eitri can compute are stated
as facts, so the model spends its judgment on what only judgment can decide."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ...model import MapModel
from ...pathutil import app_of, is_contract_path, is_kernel_path, is_route_path, norm, shared_of, slice_of
from ...sensors.boundary_edits import FAN_IN_FREEZE
from .diff import FileChange

MAX_FILE_CHARS = 12_000  # per file; the rest is summarised as "+N/-M more lines"
MAX_STATE_CHARS = 60_000  # all files together
# The order the budget is spent in: the code the walls are about first, prose last. A wide diff
# then truncates docs and tooling, never the slice the judge is asked about.
_AREA_PRIORITY = ("slice:", "contract:", "kernel", "app", "service", "shared:", "tests", "tooling", "ci", "docs", "other")

_FIX_LINE = re.compile(r"^\*\*Fix[^*]*:\*\*\s*(.+?)\s*$", re.MULTILINE)


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


def build_state(files: list[FileChange], m: MapModel | None, task: str | None, walls: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """The JSON object Jev judges. Files are listed source first (see ``_AREA_PRIORITY``), git order
    within an area, and the character budget is spent in that order."""
    slices_touched: set[str] = set()
    contracts_touched: dict[str, int] = {}
    routes_changed = False
    tests_touched: set[str] = set()
    source_changed = False
    docs_changed = False
    truncated: list[str] = []
    budget = MAX_STATE_CHARS
    entries: list[dict[str, Any]] = []
    classified = sorted(((area_of(f.path, m), f) for f in files), key=lambda pair: _priority(pair[0]))
    for area, f in classified:
        if area.startswith(("slice:", "contract:")):
            name = area.split(":", 1)[1]
            slices_touched.add(name)
            source_changed = True
            if area.startswith("contract:"):
                contracts_touched[name] = m.slices[name].fan_in if m and name in m.slices else 0
            if m is not None and is_route_path(f.path, m):
                routes_changed = True
        elif area in ("kernel", "app", "service") or area.startswith("shared:"):
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
        "wall_violations": list(walls or []),
    }
    return {"task": task or "(no task statement given)", "architecture": architecture, "facts": facts, "files": entries}
