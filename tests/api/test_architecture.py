"""The real service must pass its own walls: this is the gate CI runs as ``eitri check``."""

from __future__ import annotations

import ast
from pathlib import Path

from eitri import analyze
from eitri.core import Severity

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"
APP = SRC / "pfa"


def _imports(file: Path) -> list[str]:
    tree = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.extend(f"{node.module}.{a.name}" for a in node.names)
    return out


def test_src_passes_the_walls_and_the_budget() -> None:
    diags = analyze(REPO)
    errors = [d.format(REPO) for d in diags if d.severity == Severity.ERROR]
    assert errors == []
    slices = sorted(d.message.split("'")[1] for d in diags if d.id == "EIT101")
    assert slices == ["chunking", "embeddings"]


def test_features_know_nothing_above_themselves() -> None:
    """A feature is messages in, ``Result`` out. It may import the kernel and declared contracts,
    never the application, its entry point, or an HTTP framework."""
    offenders = []
    for f in (APP / "features").rglob("*.py"):
        for name in _imports(f):
            root = name.split(".")[0]
            above = name.startswith("pfa.") and not name.startswith(("pfa.kernel", "pfa.features."))
            if root in ("fastapi", "starlette", "pydantic") or above:
                offenders.append(f"{f.relative_to(REPO)}: {name}")
    assert offenders == []


def test_the_kernel_imports_nothing_of_the_application() -> None:
    offenders = [f"{f.relative_to(REPO)}: {n}" for f in (APP / "kernel").rglob("*.py") for n in _imports(f) if n.startswith("pfa.")]
    assert offenders == []


def test_application_py_is_the_only_importer_of_feature_modules() -> None:
    """``pfa.features.<x>.module`` is a feature's plug. Only the composition root plugs modules in;
    routes consume features through their contracts exactly as another feature would; nothing
    outside a feature reaches its ``internal/``."""
    modules, internals = [], []
    for f in APP.rglob("*.py"):
        if (APP / "features") in f.parents or (APP / "kernel") in f.parents:
            continue
        for name in _imports(f):
            parts = name.split(".")
            if parts[:2] != ["pfa", "features"] or len(parts) < 4:
                continue
            if parts[3] == "internal":
                internals.append(f"{f.relative_to(REPO)}: {name}")
            elif parts[3] == "module" and f != APP / "application.py":
                modules.append(f"{f.relative_to(REPO)}: {name}")
    assert internals == [] and modules == []


def test_every_feature_has_its_http_edge_in_the_api_routes_package() -> None:
    """One routes module per feature, named after it, so ``heimdall map`` finds the inventory and
    an "add a route" task has one obvious file to open."""
    features = sorted(p.name for p in (APP / "features").iterdir() if (p / "feature.json").is_file())
    assert features == ["chunking", "embeddings"]
    for name in features:
        routes = APP / "api" / "routes" / f"{name}.py"
        assert routes.is_file(), routes
        assert f"pfa.features.{name}.contract" in " ".join(_imports(routes))


def test_package_inits_import_nothing() -> None:
    """``pfa`` and ``pfa.api`` must not import eagerly: features import ``pfa.kernel``, routes
    import ``pfa.api.problems``, and an eager ``app`` import from either init would loop."""
    for init in (APP / "__init__.py", APP / "api" / "__init__.py"):
        assert _imports(init) == [], init
