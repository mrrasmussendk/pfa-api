"""EIT001–EIT005: the slice walls, as an import-graph checker.

Python has no compiler-enforced visibility and no linker, so the walls are expressed on
the one thing a Python module *can* do to another: import it.
"""

from __future__ import annotations

import ast
from pathlib import Path

from .core import EIT001, EIT002, EIT003, EIT004, EIT005, Diagnostic, EitriConfig
from .imports import classify, collect_imports, resolve_relative
from .project import MANIFEST, Slice

_SYS_PATH_MUTATORS = frozenset({"append", "insert", "extend", "remove", "clear", "pop"})
# EIT005: the framework's own error type. Raised from a slice it answers as the framework's
# ``{"detail": …}`` body, not as an RFC 9457 problem document — one shape per error is the
# promise to API clients, so slices go through the shared ``Problem`` instead.
_HTTP_EXCEPTION_ROOTS = frozenset({"fastapi", "starlette"})
_HTTP_EXCEPTION = "HTTPException"


def analyze_module(
    tree: ast.Module,
    module_name: str,
    path: Path,
    config: EitriConfig,
    slice: Slice | None,
) -> list[Diagnostic]:
    parts = module_name.split(".")
    if len(parts) < 2 or parts[0] != config.slices_package:
        return []  # only police slice modules
    own = parts[1]
    in_contract = len(parts) >= 3 and parts[2] == "contract"
    is_package = path.name == "__init__.py"
    declared = slice.depends_on if slice is not None else ()

    diags: list[Diagnostic] = []
    for target in collect_imports(tree, module_name, is_package):
        c = classify(target.name, config)

        # ---- EIT002: wildcard imports hide the coupling from grep ----
        if target.wildcard:
            diags.append(EIT002.create(path, target.line, f"'from {target.name} import *'"))

        # ---- EIT001: binding to another slice's internals (Module wiring is the exemption) ----
        if c.kind == "slice" and c.slice != own and not c.in_contract and not c.is_wiring:
            diags.append(EIT001.create(path, target.line, module_name, target.name, c.slice))

        # ---- EIT003: contract modules may reach only kernel / stdlib / same-contract ----
        if in_contract:
            allowed = (
                c.kind in ("stdlib", "kernel")
                or (c.kind == "slice" and c.slice == own and c.in_contract)
                or target.root in config.contract_allowed_modules
            )
            if not allowed:
                diags.append(EIT003.create(path, target.line, module_name, target.name))

        # ---- EIT005: the framework's HTTPException is not an RFC 9457 problem document ----
        # (contracts are excluded: EIT003 already forbids the import there, with the right remedy)
        if not in_contract and _is_http_exception(target.name):
            diags.append(EIT005.create(path, target.line, module_name, target.name, config.problem_helper))

        # ---- EIT004: consuming a contract requires declaring the dependency ----
        if c.kind == "slice" and c.slice != own and c.in_contract and c.slice not in declared:
            diags.append(EIT004.create(path, target.line, own, c.slice, MANIFEST))

    # ---- EIT002: dynamic imports and sys.path surgery · EIT005: HTTPException reached by attribute ----
    aliases = _module_aliases(tree, module_name, is_package)
    for node in ast.walk(tree):
        hatch = _escape_hatch(node)
        if hatch is not None:
            diags.append(EIT002.create(path, getattr(node, "lineno", None), hatch))
        if in_contract or not isinstance(node, ast.Attribute):
            continue
        dotted = _dotted(node)
        if dotted is None:
            continue
        head, _, rest = dotted.partition(".")
        resolved = aliases.get(head, head) + ("." + rest if rest else "")
        if _is_http_exception(resolved):
            diags.append(EIT005.create(path, node.lineno, module_name, resolved, config.problem_helper))
    return diags


def _is_http_exception(dotted: str) -> bool:
    """``fastapi.HTTPException``, ``fastapi.exceptions.HTTPException``, ``starlette.exceptions.HTTPException`` …"""
    return dotted.split(".")[0] in _HTTP_EXCEPTION_ROOTS and dotted.rsplit(".", 1)[-1] == _HTTP_EXCEPTION


def _module_aliases(tree: ast.Module, module_name: str, is_package: bool) -> dict[str, str]:
    """Local name -> the framework module it is bound to, for every ``import``/``from … import``
    that binds (a part of) fastapi or starlette: ``import fastapi as fa`` → ``{"fa": "fastapi"}``,
    ``from fastapi import exceptions`` → ``{"exceptions": "fastapi.exceptions"}``,
    ``import fastapi.exceptions`` → ``{"fastapi": "fastapi"}``. Attribute chains are resolved
    through this before the EIT005 test, so an alias cannot dodge the wall."""
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in _HTTP_EXCEPTION_ROOTS:
                    if alias.asname:
                        out[alias.asname] = alias.name
                    else:
                        out[alias.name.split(".")[0]] = alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            base = resolve_relative(module_name, is_package, node.level, node.module)
            if base.split(".")[0] in _HTTP_EXCEPTION_ROOTS:
                for alias in node.names:
                    if alias.name != "*":
                        out[alias.asname or alias.name] = f"{base}.{alias.name}"
    return out


def _dotted(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def _escape_hatch(node: ast.AST) -> str | None:
    if isinstance(node, ast.Call):
        name = _dotted(node.func)
        if name is None:
            return None
        if name == "__import__" or name.startswith("importlib."):
            return f"'{name}(...)'"
        if name.startswith("sys.path.") and name.rsplit(".", 1)[1] in _SYS_PATH_MUTATORS:
            return f"'{name}(...)'"
        return None
    targets: list[ast.expr] = []
    if isinstance(node, ast.Assign):
        targets = list(node.targets)
    elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
        targets = [node.target]
    for t in targets:
        target = t.value if isinstance(t, ast.Subscript) else t
        name = _dotted(target)
        if name in ("sys.path", "sys.modules"):
            return f"assignment to '{name}'"
    return None
