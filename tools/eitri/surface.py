"""Reconstructs a module's public API surface from its AST — a ``.pyi``-shaped stub.

This is what an agent would actually need in context to *consume* a dependency, with
every body stripped. Pricing this (not the dependency's source) is what makes EIT100
architecture-truthful — a dependency's internals cost the consumer zero tokens.
"""

from __future__ import annotations

import ast
import copy


def public_names(tree: ast.Module) -> set[str] | None:
    """Names listed in ``__all__`` when the module declares one as a literal, else ``None``."""
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets)
            and isinstance(node.value, (ast.List, ast.Tuple))
        ):
            names = set()
            for elt in node.value.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    names.add(elt.value)
            return names
    return None


def is_public(name: str, exported: set[str] | None = None) -> bool:
    if exported is not None:
        return name in exported
    if name.startswith("__") and name.endswith("__"):
        return True  # dunders are part of the surface — an agent needs __init__ to construct the type
    return not name.startswith("_")


def render_module_surface(tree: ast.Module) -> str:
    exported = public_names(tree)
    lines: list[str] = []
    for node in tree.body:
        for line in _render_top_level(node, exported):
            lines.append(line)
    return "\n".join(lines) + ("\n" if lines else "")


def _render_top_level(node: ast.stmt, exported: set[str] | None) -> list[str]:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        if not is_public(node.name, exported):
            return []
        return [ast.unparse(_stub(node))]
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        if not is_public(node.target.id, exported):
            return []
        return [f"{node.target.id}: {ast.unparse(node.annotation)}"]
    if isinstance(node, ast.Assign):
        out = []
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id != "__all__" and is_public(target.id, exported):
                out.append(_render_assign(target.id, node.value))
        return out
    return []


def _render_assign(name: str, value: ast.expr) -> str:
    if isinstance(value, ast.Constant):
        return f"{name} = {ast.unparse(value)}"
    return name


def _stub(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> ast.stmt:
    new = copy.copy(node)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        new.body = [ast.Expr(ast.Constant(...))]
        return new
    body: list[ast.stmt] = []
    for member in node.body:
        if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if is_public(member.name):
                body.append(_stub(member))
        elif isinstance(member, ast.AnnAssign) and isinstance(member.target, ast.Name):
            if is_public(member.target.id):
                stripped = copy.copy(member)
                stripped.value = None
                body.append(stripped)
        elif isinstance(member, ast.Assign):
            for target in member.targets:
                if isinstance(target, ast.Name) and is_public(target.id):
                    value = member.value if isinstance(member.value, ast.Constant) else ast.Constant(...)
                    body.append(ast.Assign(targets=[ast.Name(id=target.id, ctx=ast.Store())], value=value, lineno=0))
    new.body = body or [ast.Expr(ast.Constant(...))]
    return new
