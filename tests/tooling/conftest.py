from __future__ import annotations

import io
import json
import textwrap
from pathlib import Path

import pytest

from eitri import EitriConfig, analyze
from heimdall.app import run as heimdall_run


class TempRepo:
    """A throwaway repo root that ``heimdall.app.run`` treats as CWD (port of TempRepo.cs)."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def write_file(self, rel: str, content: str) -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8", newline="\n")
        return p

    def read_file(self, rel: str) -> str:
        return (self.root / rel).read_text(encoding="utf-8")

    def has_file(self, rel: str) -> bool:
        return (self.root / rel).is_file()

    @property
    def telemetry(self) -> str:
        return self.read_file(".heimdall/telemetry.jsonl") if self.has_file(".heimdall/telemetry.jsonl") else ""

    def run(self, stdin_text: str, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        code = heimdall_run(list(args), io.StringIO(stdin_text), out, err, str(self.root))
        return code, out.getvalue(), err.getvalue()

    def hook(self, event_json: str) -> tuple[int, str]:
        code, _, err = self.run(event_json, "hook")
        return code, err

    @staticmethod
    def ev(session: str, tool: str, path: str) -> str:
        """A PostToolUse event exactly as the smoke test forges them."""
        return json.dumps({"session_id": session, "tool_name": tool, "tool_input": {"file_path": path}})

    def write_sample_map(self, with_frozen_core: bool = False) -> None:
        """The fixture-shaped map: kvad depends on rune; optional frozen high fan-in slice."""
        slices = {
            "kvad": {"path": "src/pfa/features/kvad", "depends_on": ["rune"], "budget": 15000, "fan_in": 0},
            "rune": {"path": "src/pfa/features/rune", "depends_on": [], "budget": 15000, "fan_in": 1},
        }
        if with_frozen_core:
            slices["core"] = {"path": "src/pfa/features/core", "depends_on": [], "budget": 15000, "fan_in": 12}
        self.write_file(
            ".heimdall/map.json",
            json.dumps(
                {
                    "kernel": "kernel",
                    "kernel_dir": "src/pfa/kernel",
                    "slices_dir": "src/pfa/features",
                    "app_dir": "src/pfa",
                    "routes_dir": "src/pfa/api/routes",
                    "slices": slices,
                },
                indent=2,
            ),
        )


@pytest.fixture
def repo(tmp_path: Path) -> TempRepo:
    return TempRepo(tmp_path)


class TempProject:
    """A throwaway application tree for Eitri, laid out like the real one:
    ``<root>/pfa/kernel`` + ``<root>/pfa/features/<name>/...`` (+ ``<root>/pfa/...`` for the app)."""

    def __init__(self, root: Path) -> None:
        self.root = root
        (root / "pfa" / "kernel").mkdir(parents=True)
        (root / "pfa" / "__init__.py").write_text("", encoding="utf-8")
        (root / "pfa" / "kernel" / "__init__.py").write_text("class StaveId:\n    pass\n\nclass Result:\n    pass\n", encoding="utf-8")
        (root / "pfa" / "features").mkdir()
        (root / "pfa" / "features" / "__init__.py").write_text("", encoding="utf-8")

    def write(self, rel: str, code: str) -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(code).lstrip("\n"), encoding="utf-8")
        return p

    def slice(self, name: str, depends_on: list[str] | None = None, **files: str) -> None:
        """``files`` maps a path relative to the slice (e.g. ``internal/x.py``) to its code."""
        base = f"pfa/features/{name}"
        self.write(f"{base}/feature.json", json.dumps({"depends_on": depends_on or []}))
        self.write(f"{base}/__init__.py", "")
        for rel, code in files.items():
            self.write(f"{base}/{rel}", code)

    def app(self, **files: str) -> None:
        """``files`` maps a path relative to the application package ``pfa`` (e.g. ``api/routes/x.py``,
        ``application.py``) to its code."""
        for rel, code in files.items():
            self.write(f"pfa/{rel}", code)

    def analyze(self, budget: int = 15_000, **overrides):
        config = EitriConfig(token_budget=budget, **overrides)
        return analyze(self.root, config)

    def ids(self, budget: int = 15_000, **overrides) -> list[str]:
        return [d.id for d in self.analyze(budget, **overrides)]

    def errors(self, budget: int = 15_000, **overrides) -> list[str]:
        return [d.id for d in self.analyze(budget, **overrides) if d.severity.value == "error"]


@pytest.fixture
def project(tmp_path: Path) -> TempProject:
    return TempProject(tmp_path)
