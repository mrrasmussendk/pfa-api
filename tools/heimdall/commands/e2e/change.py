"""The change: what the agent left behind — committed since the base, uncommitted, and untracked — as one
unified diff, and what it reaches in the slice map. Untracked files count: a new route or a new scenario is
exactly the change that decides what to run."""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ...model import MapModel
from ...pathutil import norm
from ..review import FileChange
from ..review.state import area_of, is_application
from .base import E2EError

# Areas whose change reaches every feature: the shared primitives and the wiring around them.
_WIDE = ("kernel", "app", "service")


def _git(cwd: str, *args: str) -> str:
    try:
        out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    except OSError as e:
        raise E2EError(f"git not available: {e}") from None
    if out.returncode != 0:
        raise E2EError(f"git {args[0]} failed: {out.stderr.strip()}")
    return out.stdout


def _as_added(cwd: str, path: str) -> str:
    """An untracked file as the diff ``git add`` would show, without touching the index."""
    head = f"diff --git a/{path} b/{path}\nnew file mode 100644\n"
    try:
        lines = Path(cwd, path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return head + f"Binary files /dev/null and b/{path} differ\n"
    return head + f"--- /dev/null\n+++ b/{path}\n@@ -0,0 +1,{len(lines)} @@\n" + "".join(f"+{line}\n" for line in lines)


def working_diff(cwd: str, base: str | None) -> str:
    """Tracked changes against the merge base with ``base`` (or ``HEAD``), then every untracked file."""
    against = _git(cwd, "merge-base", base, "HEAD").strip() if base else "HEAD"
    tracked = _git(cwd, "diff", "--no-color", "--unified=3", "--no-ext-diff", against)
    untracked = [p for p in _git(cwd, "ls-files", "--others", "--exclude-standard", "-z").split("\0") if p]
    return tracked + "".join(_as_added(cwd, p) for p in untracked)


def fingerprint(diff: str) -> str:
    return hashlib.sha256(diff.encode("utf-8")).hexdigest()


def _reach(touched: frozenset[str], m: MapModel | None) -> frozenset[str]:
    """``touched`` and every feature that depends on one of them, transitively."""
    reach = set(touched)
    grew = True
    while grew and m is not None:
        grew = False
        for name, info in m.slices.items():
            if name not in reach and reach.intersection(info.depends_on):
                reach.add(name)
                grew = True
    return frozenset(reach)


@dataclass(frozen=True)
class Change:
    paths: frozenset[str]
    touched: frozenset[str]  # features whose own code changed
    reach: frozenset[str]  # touched, and every feature that depends on one of them
    wide: bool  # the kernel, the application or code shared by every feature changed
    application: bool  # any application code changed; without it, no client-visible behaviour can have

    @classmethod
    def of(cls, files: list[FileChange], m: MapModel | None) -> Change:
        paths = frozenset(norm(f.path) for f in files)
        areas = [area_of(p, m) for p in paths]
        touched = frozenset(a.split(":", 1)[1] for a in areas if a.startswith(("slice:", "contract:")))
        wide = any(a in _WIDE or a.startswith("shared:") for a in areas)
        return cls(paths, touched, _reach(touched, m), wide, any(is_application(a) for a in areas))
