"""Function shape: the two clean-code facts an AST can state — how long a function is and how many
parameters it takes. The hook states them to the agent while it edits; ``heimdall review`` states
them to the judge, so "single purpose" and "lean signatures" are asked about a named function at a
line instead of being guessed from a diff.

A limit is a prompt, not a wall: Eitri's facts block on their own, these name the function and
leave the judgment (a flat table may stay whole) to the agent, the judge and the reviewer.
Stdlib only: the hook imports this on every tool call."""

from __future__ import annotations

import ast
from collections.abc import Iterable
from typing import Any

FUNCTION_LINES = 40  # def line to last line, docstring included
FUNCTION_PARAMETERS = 5  # ``self`` / ``cls`` not counted

Function = ast.FunctionDef | ast.AsyncFunctionDef


def parameters(fn: Function) -> int:
    a = fn.args
    positional = a.posonlyargs + a.args
    count = len(positional) + len(a.kwonlyargs) + (a.vararg is not None) + (a.kwarg is not None)
    if positional and positional[0].arg in ("self", "cls"):
        count -= 1
    return count


def functions(tree: ast.AST) -> list[tuple[str, Function]]:
    """Every function with its dotted name (``Class.method``, ``outer.inner``), nested ones included."""
    out: list[tuple[str, Function]] = []

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.append((prefix + child.name, child))
                walk(child, prefix + child.name + ".")
            elif isinstance(child, ast.ClassDef):
                walk(child, prefix + child.name + ".")
            else:
                walk(child, prefix)

    walk(tree, "")
    return out


def oversized(path: str, source: str, touched: Iterable[int] | None = None) -> list[dict[str, Any]]:
    """The functions in ``source`` over ``FUNCTION_LINES`` or ``FUNCTION_PARAMETERS``, as
    ``{path, line, name, lines, params, over}``. With ``touched`` (line numbers), only the functions
    whose span contains one of them; unparsable source contributes nothing."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    marks = None if touched is None else set(touched)
    out: list[dict[str, Any]] = []
    for name, fn in functions(tree):
        end = fn.end_lineno or fn.lineno
        if marks is not None and not any(fn.lineno <= n <= end for n in marks):
            continue
        lines, params = end - fn.lineno + 1, parameters(fn)
        over = [k for k, hit in (("lines", lines > FUNCTION_LINES), ("params", params > FUNCTION_PARAMETERS)) if hit]
        if over:
            out.append({"path": path, "line": fn.lineno, "name": name, "lines": lines, "params": params, "over": over})
    return sorted(out, key=lambda v: v["line"])


def shape_line(v: dict[str, Any]) -> str:
    """``path:line name (52 lines, 7 params)`` — one spelling in the hook, the table, the comment and the advice."""
    return f"{v['path']}:{v['line']} {v['name']} ({v['lines']} lines, {v['params']} params)"
