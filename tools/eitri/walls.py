"""EIT001–EIT006: the walls, as an import-graph checker.

Python has no compiler-enforced visibility and no linker, so the walls are expressed on
the one thing a Python module *can* do to another: import it.

Two packages are policed, with different rules, because they have different jobs:

- a **slice** (``slices.<x>``): other slices only through their ``contract`` (EIT001), explicit
  imports only (EIT002), pure contracts (EIT003), declared dependencies (EIT004), and no HTTP
  framework at all (EIT006) — a slice answers messages with ``Result``;
- the **application package** (composition root + entry points): a slice's ``contract``
  anywhere, its ``module`` only from the composition root, never its ``internal`` (EIT001), and
  errors as problem documents, never ``HTTPException`` (EIT005).
"""

from __future__ import annotations

import ast
from pathlib import Path

from .core import EIT001, EIT002, EIT003, EIT004, EIT005, EIT006, Diagnostic, EitriConfig
from .imports import classify, collect_imports, inside, resolve_relative, under
from .project import MANIFEST, Slice

_SYS_PATH_MUTATORS = frozenset({"append", "insert", "extend", "remove", "clear", "pop"})
# EIT006: the HTTP frameworks. A slice that imports either has grown an HTTP edge of its own.
# EIT005: the framework's own error type. Raised from the edge it answers as the framework's
# ``{"detail": …}`` body, not as an RFC 9457 problem document — one shape per error is the
# promise to API clients, so the edge goes through the shared ``Problem`` instead.
_HTTP_FRAMEWORKS = frozenset({"fastapi", "starlette"})
_HTTP_EXCEPTION = "HTTPException"


def analyze_module(
    tree: ast.Module,
    module_name: str,
    path: Path,
    config: EitriConfig,
    slice: Slice | None,
) -> list[Diagnostic]:
    if under(module_name, config.slices_package):
        parts = inside(module_name, config.slices_package)
        if not parts:
            return []  # the slices package itself
        return _analyze_slice_module(tree, module_name, path, config, slice, parts[0])
    if under(module_name, config.kernel):
        return []  # the kernel is priced (EIT100), not policed
    if under(module_name, config.app_package):
        return _analyze_app_module(tree, module_name, path, config)
    return []  # everything else is not policed


def _analyze_slice_module(
    tree: ast.Module, module_name: str, path: Path, config: EitriConfig, slice: Slice | None, own: str
) -> list[Diagnostic]:
    parts = inside(module_name, config.slices_package)
    in_contract = len(parts) >= 2 and parts[1] == "contract"
    is_package = path.name == "__init__.py"
    declared = slice.depends_on if slice is not None else ()

    diags: list[Diagnostic] = []
    for target in collect_imports(tree, module_name, is_package):
        c = classify(target.name, config)

        # ---- EIT002: wildcard imports hide the coupling from grep ----
        if target.wildcard:
            diags.append(EIT002.create(path, target.line, f"'from {target.name} import *'"))

        # ---- EIT001: binding to another slice's internals (its module.py included — only the app may) ----
        if c.kind == "slice" and c.slice != own and not c.in_contract:
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

        # ---- EIT006: a slice depends only downward — no HTTP framework, nothing from the application ----
        # (contracts are excluded: EIT003 already forbids the import there, with the right remedy)
        if not in_contract and (target.root in _HTTP_FRAMEWORKS or c.kind == "app"):
            diags.append(EIT006.create(path, target.line, module_name, target.name, config.app_package))

        # ---- EIT004: consuming a contract requires declaring the dependency ----
        if c.kind == "slice" and c.slice != own and c.in_contract and c.slice not in declared:
            diags.append(EIT004.create(path, target.line, own, c.slice, MANIFEST))

    # ---- EIT002: dynamic imports and sys.path surgery ----
    for node in ast.walk(tree):
        hatch = _escape_hatch(node)
        if hatch is not None:
            diags.append(EIT002.create(path, getattr(node, "lineno", None), hatch))
    return diags


def _analyze_app_module(tree: ast.Module, module_name: str, path: Path, config: EitriConfig) -> list[Diagnostic]:
    is_package = path.name == "__init__.py"
    # The module that defines the problem helper is the conversion point: it is the one place
    # that must see the framework's HTTPException in order to turn it into a problem document.
    converts = module_name == config.problem_helper.rpartition(".")[0]
    diags: list[Diagnostic] = []
    for target in collect_imports(tree, module_name, is_package):
        c = classify(target.name, config)

        # ---- EIT001: the application consumes a slice through its contract; only the composition root plugs its module in ----
        if c.kind == "slice" and not c.in_contract and not (c.is_wiring and module_name == config.composition_root):
            diags.append(EIT001.create(path, target.line, module_name, target.name, c.slice))

        # ---- EIT005: the framework's HTTPException is not an RFC 9457 problem document ----
        if not converts and _is_http_exception(target.name):
            diags.append(EIT005.create(path, target.line, module_name, target.name, config.problem_helper))

    if converts:
        return diags

    # ---- EIT005: HTTPException reached by attribute, through any alias ----
    aliases = _module_aliases(tree, module_name, is_package)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
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
    return dotted.split(".")[0] in _HTTP_FRAMEWORKS and dotted.rsplit(".", 1)[-1] == _HTTP_EXCEPTION


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
                if alias.name.split(".")[0] in _HTTP_FRAMEWORKS:
                    if alias.asname:
                        out[alias.asname] = alias.name
                    else:
                        out[alias.name.split(".")[0]] = alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            base = resolve_relative(module_name, is_package, node.level, node.module)
            if base.split(".")[0] in _HTTP_FRAMEWORKS:
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
