"""The real service must pass its own walls: this is the gate CI runs as ``eitri check``."""

from __future__ import annotations

from pathlib import Path

from eitri import analyze
from eitri.core import Severity

REPO = Path(__file__).resolve().parents[2]


def test_src_passes_the_walls_and_the_budget() -> None:
    diags = analyze(REPO)
    errors = [d.format(REPO) for d in diags if d.severity == Severity.ERROR]
    assert errors == []
    slices = sorted(d.message.split("'")[1] for d in diags if d.id == "EIT101")
    assert slices == ["chunking", "embeddings"]


def test_composition_root_is_the_only_importer_of_slice_internals() -> None:
    """Outside slices/, only pfa_api/composition.py may reach into a slice's internal/."""
    offenders = []
    for f in (REPO / "src" / "pfa_api").rglob("*.py"):
        if ".internal" in f.read_text(encoding="utf-8") and f.name != "composition.py":
            offenders.append(str(f.relative_to(REPO)))
    assert offenders == []


def test_http_common_sits_below_the_slices() -> None:
    """``http_common`` is imported by slices, so it may import nothing above itself: no slice,
    not the composition root. (Eitri does not police non-slice packages; this does.)"""
    offenders = []
    for f in (REPO / "src" / "http_common").rglob("*.py"):
        text = f.read_text(encoding="utf-8")
        if "slices." in text or "pfa_api" in text:
            offenders.append(str(f.relative_to(REPO)))
    assert offenders == []
