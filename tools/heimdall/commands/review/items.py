"""What Jev is asked about: the functions this change touches, each read whole from the working tree. Jev's
own guidance is to ask narrow questions per item, built in code ("ask one question per candidate"), so each
question is about one function, and a failing answer names it: the agent is told where, not "the change".

Tests are left out (they are what the questions ask about, not what they judge). The application's code comes
before the tooling, in the order the state's budget ranks areas, and what does not fit the budget is counted,
not cut mid-function."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from brokkr import estimate

from ...model import MapModel
from ...pathutil import is_route_path, norm
from ...shape import Function, functions
from .diff import FileChange
from .state import area_of, priority, touched_lines

MAX_ITEM_TOKENS = 14_000  # every function's source together; the rest of Jev's window is the diff and the facts
MAX_FUNCTION_TOKENS = 1_500  # one function, cut at a line boundary beyond it


@dataclass(frozen=True)
class Item:
    id: str  # ``f1``, ``f2``…: how a question names it (``functions.f1``)
    path: str
    line: int
    name: str
    source: str
    route: bool  # it lives in a feature's HTTP edge, so it answers clients

    @property
    def where(self) -> str:
        return f"{self.path}:{self.line} {self.name}"


def _outermost_touched(tree: ast.AST, touched: set[int]) -> list[tuple[str, Function]]:
    """The touched functions not inside another touched one: an inner function is read as part of its outer one."""
    picked: list[tuple[str, Function]] = []
    for name, fn, _ in functions(tree):
        end = fn.end_lineno or fn.lineno
        inside = any(p.lineno <= fn.lineno and end <= (p.end_lineno or p.lineno) for _, p in picked)
        if not inside and any(fn.lineno <= n <= end for n in touched):
            picked.append((name, fn))
    return picked


def _candidates(cwd: str, f: FileChange) -> list[tuple[str, int, str]]:
    """``(name, line, source)`` for each outermost touched function in the file on disk; none when it cannot be read."""
    path = norm(f.path)
    try:
        text = (Path(cwd) / path).read_text(encoding="utf-8")
        tree = ast.parse(text)
    except (OSError, ValueError, SyntaxError):
        return []
    lines = text.splitlines()
    return [
        (name, fn.lineno, "\n".join(lines[fn.lineno - 1 : fn.end_lineno or fn.lineno]))
        for name, fn in _outermost_touched(tree, touched_lines(f))
    ]


def _askable(f: FileChange, m: MapModel | None) -> bool:
    path = norm(f.path)
    return f.status != "deleted" and not f.binary and path.endswith(".py") and area_of(path, m) != "tests"


def changed_functions(cwd: str, files: list[FileChange], m: MapModel | None, budget: int = MAX_ITEM_TOKENS) -> tuple[list[Item], int]:
    """The items to ask about, application first, and how many touched functions did not fit ``budget``."""
    ordered = sorted((f for f in files if _askable(f, m)), key=lambda f: priority(area_of(norm(f.path), m)))
    items: list[Item] = []
    skipped = 0
    for f in ordered:
        path = norm(f.path)
        route = m is not None and is_route_path(path, m)
        for name, line, source in _candidates(cwd, f):
            cost = min(estimate(source), MAX_FUNCTION_TOKENS)
            if cost > budget:
                skipped += 1
                continue
            budget -= cost
            items.append(Item(f"f{len(items) + 1}", path, line, name, _cut(source), route))
    return items, skipped


def _cut(source: str) -> str:
    """The function's source, cut at a line boundary past ``MAX_FUNCTION_TOKENS``."""
    if estimate(source) <= MAX_FUNCTION_TOKENS:
        return source
    kept: list[str] = []
    for row in source.splitlines():
        if estimate("\n".join([*kept, row])) > MAX_FUNCTION_TOKENS:
            break
        kept.append(row)
    return "\n".join(kept) + "\n# … the rest of the function is not shown"
