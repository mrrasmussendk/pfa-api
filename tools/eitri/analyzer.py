"""The analysis entry point: discover slices, run the walls on every module, meter the budgets."""

from __future__ import annotations

import ast
from pathlib import Path

from .context_budget import analyze_budgets
from .core import EIT000, Diagnostic, EitriConfig
from .imports import under
from .project import (
    ManifestError,
    Slice,
    discover_slices,
    find_kernel_dir,
    find_slices_dir,
    import_root_of,
    module_name,
    package_path,
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
        raise EitriError(f"no {config.slices_package.replace('.', '/')}/ under {root}")
    import_root = import_root_of(sd, config)
    kernel_dir = find_kernel_dir(root, config, sd)

    try:
        slices = discover_slices(sd)
    except ManifestError as e:
        raise EitriError(str(e)) from e

    diags: list[Diagnostic] = []

    def police(files: list[Path], owner: Slice | None) -> None:
        for f in files:
            text = f.read_text(encoding="utf-8", errors="replace")
            try:
                tree = ast.parse(text, filename=str(f))
            except SyntaxError as e:
                diags.append(EIT000.create(f, e.lineno, f.name, e.msg))
                continue
            diags.extend(analyze_module(tree, module_name(f, import_root), f, config, owner))

    for s in slices:
        police(source_files(s.path), s)

    # The application package (composition root + entry points): EIT001 against slice internals,
    # EIT005 against HTTPException. In a nested layout the slices and the kernel live inside it,
    # so those files are skipped here — the slices were policed above, the kernel is not policed.
    app_dir = import_root / package_path(config.app_package)
    if app_dir.is_dir():
        own = [
            f
            for f in source_files(app_dir)
            if not under(module_name(f, import_root), config.slices_package) and not under(module_name(f, import_root), config.kernel)
        ]
        police(own, None)

    diags.extend(analyze_budgets(slices, kernel_dir, config))
    return diags
