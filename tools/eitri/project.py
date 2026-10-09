"""Slice discovery: the ``feature.json`` manifest declares each slice's seam."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .core import EitriConfig

MANIFEST = "feature.json"


class ManifestError(ValueError):
    pass


@dataclass(frozen=True)
class Slice:
    name: str
    path: Path
    depends_on: tuple[str, ...]

    @property
    def manifest(self) -> Path:
        return self.path / MANIFEST

    @property
    def contract_dir(self) -> Path:
        return self.path / "contract"


def package_path(dotted: str) -> Path:
    """``pfa.features`` -> ``pfa/features``."""
    return Path(*dotted.split("."))


def find_slices_dir(root: Path, config: EitriConfig) -> Path | None:
    rel = package_path(config.slices_package)
    for cand in (root / rel, root / "src" / rel):
        if cand.is_dir():
            return cand
    return None


def import_root_of(slices_dir: Path, config: EitriConfig) -> Path:
    """The directory ``slices_package`` is imported from: ``src`` for ``src/pfa/features``."""
    depth = len(config.slices_package.split("."))
    return slices_dir.parents[depth - 1]


def find_kernel_dir(root: Path, config: EitriConfig, slices_dir: Path | None = None) -> Path | None:
    rel = package_path(config.kernel)
    cands = []
    if slices_dir is not None:
        cands.append(import_root_of(slices_dir, config) / rel)
    cands += [root / rel, root / "src" / rel]
    for cand in cands:
        if cand.is_dir():
            return cand
    return None


def read_manifest(path: Path) -> tuple[str, ...]:
    """``depends_on`` from a feature.json; a missing manifest means a leaf slice."""
    if not path.is_file():
        return ()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ManifestError(f"unreadable {path}: {e}") from e
    deps = data.get("depends_on", []) if isinstance(data, dict) else None
    if not isinstance(deps, list) or not all(isinstance(d, str) for d in deps):
        raise ManifestError(f"{path}: depends_on must be a list of slice names")
    return tuple(sorted(set(deps)))


def discover_slices(slices_dir: Path) -> list[Slice]:
    """Every subdirectory that is a package or carries a manifest, in ordinal name order."""
    slices: list[Slice] = []
    for child in sorted(slices_dir.iterdir(), key=lambda p: p.name):
        if not child.is_dir() or child.name.startswith((".", "_")):
            continue
        if not ((child / MANIFEST).is_file() or (child / "__init__.py").is_file()):
            continue
        slices.append(Slice(child.name, child, read_manifest(child / MANIFEST)))
    return slices


def module_name(file: Path, import_root: Path) -> str:
    """``src/pfa/features/kvad/internal/engine.py`` under ``src`` -> ``pfa.features.kvad.internal.engine``."""
    rel = file.relative_to(import_root).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def source_files(directory: Path) -> list[Path]:
    """All ``*.py`` under ``directory``, skipping hidden and cache folders, sorted for determinism."""
    out = []
    for p in directory.rglob("*.py"):
        rel_parts = p.relative_to(directory).parts[:-1]
        if any(part.startswith(".") or part == "__pycache__" for part in rel_parts):
            continue
        out.append(p)
    return sorted(out)
