"""The craft pass: clean code is judged file by file, on the whole file, never on the hunks alone.

A diff shows the judge the lines that moved; the file shows it what those lines now sit in — the
duplication two hunks apart, the branch the change left dead, the style the new code departs from.
So every edited file of the application's Python is read whole from the working tree and sent in a
call of its own, with the lines this change touched and the functions the AST found over the limits,
and answers the two clean-code questions alone. The verdict then names the file, so the agent is
sent to a path rather than to "the changed code"."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from brokkr import estimate

from ...model import MapModel
from ...pathutil import norm
from ...shape import FUNCTION_LINES, FUNCTION_PARAMETERS
from .diff import FileChange
from .state import area_of, is_application, touched_lines, truncate

# One file per call, so a file may take the whole state window the diff shares between files.
MAX_CRAFT_FILE_TOKENS = 28_000

__all__ = ["MAX_CRAFT_FILE_TOKENS", "CraftFile", "craft_files", "craft_state"]


@dataclass(frozen=True)
class CraftFile:
    """One edited application file as it stands in the working tree, and the lines this change touched."""

    path: str
    area: str
    content: str
    changed_lines: list[int]


def craft_files(cwd: str, files: list[FileChange], m: MapModel | None) -> list[CraftFile]:
    """The application's Python files this change edits, read whole. Deleted files, binaries, tests, the
    tooling, docs and a file that is not on disk (a review of a bare diff) contribute nothing: the craft
    floor is about the application's code, and only a file the judge can read whole is judged whole."""
    out: list[CraftFile] = []
    for f in files:
        path = norm(f.path)
        area = area_of(path, m)
        if f.status == "deleted" or f.binary or not path.endswith(".py") or not is_application(area):
            continue
        try:
            content = (Path(cwd) / path).read_text(encoding="utf-8")
        except (OSError, ValueError):
            continue
        out.append(CraftFile(path, area, content, sorted(touched_lines(f))))
    return out


def craft_state(f: CraftFile, task: str | None, shape: list[dict[str, Any]]) -> dict[str, Any]:
    """The JSON object one craft call judges: the task, the file whole, and the facts about it — which lines
    this change touched, which of its functions the AST found over the limits, and whether it was cut."""
    content, cut = truncate(f.content, MAX_CRAFT_FILE_TOKENS)
    return {
        "task": task or "(no task statement given)",
        "file": {"path": f.path, "area": f.area, "content": content},
        "facts": {
            "changed_lines": f.changed_lines,
            "function_shape": [v for v in shape if v["path"] == f.path],
            "function_limits": {"lines": FUNCTION_LINES, "params": FUNCTION_PARAMETERS},
            "truncated": cut,
            "file_tokens": estimate(f.content),
            "state_tokens": estimate(content),
        },
    }
