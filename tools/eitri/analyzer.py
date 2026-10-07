"""The analysis entry point: discover slices, run the walls on every module, meter the budgets."""

from __future__ import annotations

import ast
from pathlib import Path

from .context_budget import analyze_budgets
from .core import EIT000, Diagnostic, EitriConfig
from .project import (
    ManifestError,
    discover_slices,
    find_kernel_dir,
    find_slices_dir,
    module_name,
    source_files,
)
from .walls import analyze_module


class EitriError(Exception):
    """A configuration or layout problem that prevents analysis (exit 1, message on stderr)."""


def analyze(
    root: Path | str,
    config: EitriConfig | None = None,
    *,
    slices_dir: Path | str | None = None,
) -> list[Diagnostic]:
    root = Path(root).resolve()
    config = config or EitriConfig.read(root)
    if not config.enabled:
        return []

    sd = Path(slices_dir).resolve() if slices_dir is not None else find_slices_dir(root, config)
    if sd is None or not sd.is_dir():
        raise EitriError(f"no {config.slices_package}/ under {root}")
    import_root = sd.parent
    kernel_dir = find_kernel_dir(root, config, sd)

    try:
        slices = discover_slices(sd)
    except ManifestError as e:
        raise EitriError(str(e)) from e

    diags: list[Diagnostic] = []
    for s in slices:
        for f in source_files(s.path):
            text = f.read_text(encoding="utf-8", errors="replace")
            try:
                tree = ast.parse(text, filename=str(f))
            except SyntaxError as e:
                diags.append(EIT000.create(f, e.lineno, f.name, e.msg))
                continue
            diags.extend(analyze_module(tree, module_name(f, import_root), f, config, s))

    diags.extend(analyze_budgets(slices, kernel_dir, config))
    return diags
