from __future__ import annotations

import io
import json
import textwrap
from pathlib import Path
from typing import Any

import pytest

from eitri import EitriConfig, analyze
from heimdall.app import run as heimdall_run
from heimdall.commands.e2e import Scenario

# One sample of each shape the hook's sensor and the review's judge are asked about.
LONG_FUNCTION = "def long_one():\n" + "\n".join(f"    x{i} = {i}" for i in range(44)) + "\n    return x0\n"  # 46 lines
SHORT_FUNCTION = "def short_one(a, b):\n    return a + b\n"
WIDE_FUNCTION = "def wide(a, b, c, d, e, f):\n    return a\n"
SHAPED_SOURCE = LONG_FUNCTION + "\n\n" + SHORT_FUNCTION


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
    def ev(session: str, tool: str, path: str, **tool_input: object) -> str:
        """A PostToolUse event exactly as the smoke test forges them; ``tool_input`` adds an edit's own fields."""
        return json.dumps({"session_id": session, "tool_name": tool, "tool_input": {"file_path": path, **tool_input}})

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


# ---------------------------------------------------------------- heimdall e2e

E2E_THEN = ("I receive a response",)
E2E_REQUEST = (
    "$kind: http-request\nmethod: POST\nurl: '{{base_url}}/run'\nscripts:\n"
    "  - type: afterResponse\n    code: pm.test('then: I receive a response', () => {});\n"
)


def e2e_scenario_text(name: str, *, tags: str = "", then: tuple[str, ...] = E2E_THEN) -> str:
    """One scenario as Gherkin, with ``tags`` on its own line above it when given."""
    clauses = "".join(f"    {'Then' if i == 0 else 'And'} {c}\n" for i, c in enumerate(then))
    head = f"  {tags}\n" if tags else ""
    return f"\n{head}  Scenario: {name}\n    Given I have a valid API key\n    When I send a request to POST /run\n{clauses}"


def write_e2e_scenario(repo: TempRepo, sid: str, request: str | None = E2E_REQUEST, **text: Any) -> None:
    """The scenario, added to its feature's file, and the Postman request that runs it unless ``request`` is None."""
    feature, name = sid.split("/")
    path = repo.root / f"tests/e2e/{feature}.feature"
    head = path.read_text(encoding="utf-8") if path.exists() else f"@API\nFeature: {feature}\n"
    repo.write_file(f"tests/e2e/{feature}.feature", head + e2e_scenario_text(name, **text))
    if request is not None:
        repo.write_file(f"postman/collections/e2e/{sid}.request.yaml", request)


def write_e2e_project(repo: TempRepo) -> TempRepo:
    """The sample map (kvad depends on rune), an API-only project, one scenario per feature."""
    repo.write_sample_map()
    repo.write_file("harness/e2e/system.json", json.dumps({"tools": {"API": "postman"}}))
    write_e2e_scenario(repo, "kvad/squares")
    write_e2e_scenario(repo, "rune/carves")
    return repo


def e2e_scenario(sid: str, tools: tuple[str, ...] = ("postman",), lines: tuple[int, int] = (4, 7)) -> Scenario:
    return Scenario(sid, sid.split("/")[0], "API", tools, ("g",), ("w",), ("t",), lines)
