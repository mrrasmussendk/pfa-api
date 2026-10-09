"""Feedback sensor: tells the agent, right after an edit, which function it just touched is over the
line or parameter limit — the same facts ``heimdall review`` later hands the judge, so the agent
hears it while the function is still open rather than from the PR comment.

Only the functions the edit touched are judged (the text an ``Edit`` inserted, every edit of a
``MultiEdit``, the whole file for a ``Write``): the agent answers for what it wrote, not for the
file's history. Tests are left out — a scenario reads top-down however long it is.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from typing import Any

from ..model import EDIT_TOOLS, Finding, HeimdallContext, HookEvent
from ..pathutil import norm
from ..shape import FUNCTION_LINES, FUNCTION_PARAMETERS, oversized, shape_line


def is_test_path(path: str) -> bool:
    parts = norm(path).split("/")
    return "tests" in parts[:-1] or parts[-1].startswith("test_")


def project_path(path: str, root: str) -> str | None:
    """``path`` relative to the repo root, or ``None`` when it lies outside it (a scratch file, a temp dir)."""
    full = os.path.abspath(os.path.join(root, path))
    try:
        return norm(os.path.relpath(full, os.path.abspath(root)))
    except ValueError:  # another drive on Windows
        return None


def inserted_texts(e: HookEvent) -> list[str] | None:
    """What the edit put into the file; ``None`` means the whole file (a ``Write``, or an unknown shape)."""
    if e.tool_name == "Edit":
        new = e.tool_input.get("new_string")
        return [new] if isinstance(new, str) else None
    if e.tool_name == "MultiEdit":
        edits = e.tool_input.get("edits")
        if not isinstance(edits, list):
            return None
        return [x["new_string"] for x in edits if isinstance(x, dict) and isinstance(x.get("new_string"), str)]
    return None


def touched_lines(content: str, texts: list[str]) -> set[int] | None:
    """The lines of ``content`` each inserted text occupies; ``None`` when one cannot be found (fall back to the file)."""
    lines: set[int] = set()
    for text in texts:
        start = content.find(text)
        if start < 0 or not text:
            return None
        while start >= 0:
            first = content.count("\n", 0, start) + 1
            lines.update(range(first, first + text.count("\n") + 1))
            start = content.find(text, start + 1)
    return lines


class FunctionShapeSensor:
    name = "function_shape"

    def observe(self, e: HookEvent, ctx: HeimdallContext) -> Iterable[Finding]:
        path = project_path(e.edit_path, ctx.root)
        if e.tool_name not in EDIT_TOOLS or path is None or path.startswith("..") or not path.endswith(".py") or is_test_path(path):
            return []
        try:
            with open(os.path.join(ctx.root, path), encoding="utf-8") as f:
                content = f.read()
        except (OSError, ValueError):
            return []
        texts = inserted_texts(e)
        touched = touched_lines(content, texts) if texts is not None else None
        over = oversized(path, content, touched)
        if not over:
            return []
        return [Finding(event="edit", path=path, kind="function_shape", feedback=_feedback(over))]


def _feedback(over: list[dict[str, Any]]) -> str:
    named = "; ".join(shape_line(v) for v in over)
    return (
        f"the function you just edited is over the shape limit ({FUNCTION_LINES} lines, {FUNCTION_PARAMETERS} parameters): {named}. "
        "Split it into functions whose names say what they do, or group the values that travel together into one object, "
        "before moving on — the PR review hands the judge this same list"
    )
