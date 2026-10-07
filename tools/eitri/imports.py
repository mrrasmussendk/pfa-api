"""Import resolution and classification — the seam Eitri polices in a language without a linker."""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass

from .core import EitriConfig

_STDLIB = frozenset(getattr(sys, "stdlib_module_names", ())) | {"__future__"}


@dataclass(frozen=True)
class ImportTarget:
    name: str
    line: int
    wildcard: bool = False

    @property
    def root(self) -> str:
        return self.name.split(".", 1)[0]


@dataclass(frozen=True)
class Classification:
    kind: str  # "slice" | "kernel" | "stdlib" | "external"
    slice: str | None = None
    in_contract: bool = False
    is_wiring: bool = False


def resolve_relative(module_name: str, is_package: bool, level: int, module: str | None) -> str:
    if level == 0:
        return module or ""
    pkg = module_name.split(".") if is_package else module_name.split(".")[:-1]
    if level > 1:
        pkg = pkg[: max(0, len(pkg) - (level - 1))]
    base = ".".join(pkg)
    if module:
        return f"{base}.{module}" if base else module
    return base


def collect_imports(tree: ast.AST, module_name: str, is_package: bool) -> list[ImportTarget]:
    """Every import in the module, including lazy ones inside functions — those are classic wall-dodges."""
    out: list[ImportTarget] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.append(ImportTarget(alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            base = resolve_relative(module_name, is_package, node.level, node.module)
            for alias in node.names:
                if alias.name == "*":
                    out.append(ImportTarget(base, node.lineno, wildcard=True))
                else:
                    out.append(ImportTarget(f"{base}.{alias.name}" if base else alias.name, node.lineno))
    return out


def classify(target: str, config: EitriConfig) -> Classification:
    parts = target.split(".")
    if parts[0] == config.slices_package and len(parts) >= 2:
        in_contract = len(parts) >= 3 and parts[2] == "contract"
        is_wiring = len(parts) >= 4 and parts[2] == "internal" and parts[3] == "module"
        return Classification("slice", parts[1], in_contract, is_wiring)
    if parts[0] == config.kernel:
        return Classification("kernel")
    if parts[0] in _STDLIB:
        return Classification("stdlib")
    return Classification("external")
