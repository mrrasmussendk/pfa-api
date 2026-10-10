"""The scenarios: one JSON file per use case, in the shape of ``KEYS`` and ``STEPS``, its feature named by its folder.
A file that does not follow that shape, names a tool the project does not use, or has no test that asserts
something is a problem to fix, not a scenario to skip silently."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .base import SCENARIOS_DIR, SYSTEM
from .system import System
from .tools import TOOLS

# A scenario file is ``{"method": ..., "tools": [...], "scenario": {"given": ..., "when": ..., "then": ...}}``.
KEYS = ("method", "tools", "scenario")
STEPS = ("given", "when", "then")


@dataclass(frozen=True)
class Scenario:
    id: str  # ``<feature>/<name>``: the scenario file and every test that runs it share it
    feature: str
    method: str
    tools: tuple[str, ...]
    given: str
    when: str
    then: str

    @property
    def spec_path(self) -> str:
        return f"{SCENARIOS_DIR}/{self.id}.json"

    @property
    def files(self) -> frozenset[str]:
        """The scenario and the test each of its tools runs it with."""
        return frozenset({self.spec_path, *(TOOLS[t].test_path(self.id) for t in self.tools)})

    def as_state(self) -> dict[str, Any]:
        return {"id": self.id, "feature": self.feature, "method": self.method, "given": self.given, "when": self.when, "then": self.then}

    def as_json(self) -> dict[str, Any]:
        """The scenario file, in the shape of ``KEYS`` and ``STEPS``."""
        return {"method": self.method, "tools": list(self.tools), "scenario": {"given": self.given, "when": self.when, "then": self.then}}


def _steps(data: dict[str, Any]) -> dict[str, str]:
    steps = data.get("scenario")
    steps = steps if isinstance(steps, dict) else {}
    for step in STEPS:
        value = steps.get(step)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"scenario.{step} is missing or empty")
    return steps


def check_tools(method: str, tools: Any, system: System) -> tuple[str, ...]:
    """The tools, when each is one the project uses (``SYSTEM``) and can test ``method``."""
    allowed = sorted(set(system.tools.values()))
    if not isinstance(tools, list) or not tools or any(t not in allowed for t in tools):
        raise ValueError(f"tools {tools!r} — this project runs scenarios with {allowed!r} ({SYSTEM})")
    for t in tools:
        methods = TOOLS[t].methods
        if methods is not None and method not in methods:
            raise ValueError(f"{t} cannot test method {method!r}; it tests {sorted(methods)!r}")
    return tuple(tools)


def _read_object(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"not JSON ({e.msg}, line {e.lineno})") from None
    if not isinstance(data, dict):
        raise ValueError("not a JSON object")
    return data


def _scenario(data: dict[str, Any], system: System, path: Path) -> Scenario:
    """The scenario ``data`` describes, if it has ``KEYS`` and ``STEPS`` and names tools that can run it."""
    missing = [k for k in KEYS if k not in data]
    if missing:
        raise ValueError(f"{', '.join(missing)} missing")
    steps = _steps(data)
    method = str(data["method"])
    tools = check_tools(method, data["tools"], system)
    feature = path.parent.name
    return Scenario(f"{feature}/{path.stem}", feature, method, tools, steps["given"], steps["when"], steps["then"])


def _check_tests(root: Path, s: Scenario) -> None:
    """A scenario with no test, or a test that asserts nothing, would pass without testing anything."""
    for name in s.tools:
        tool = TOOLS[name]
        try:
            test = (root / tool.test_path(s.id)).read_text(encoding="utf-8")
        except OSError:
            raise ValueError(f"no {name} test runs it — write {tool.test_path(s.id)}") from None
        if tool.marker not in test:
            raise ValueError(f"{tool.test_path(s.id)} has no {tool.marker.strip()} asserting the `then`")


def load_scenarios(root: str, system: System) -> tuple[list[Scenario], list[str]]:
    """Every scenario under ``SCENARIOS_DIR``, by id, and one ``"<path>: <what is wrong>"`` per file that is not one."""
    base = Path(root)
    scenarios: list[Scenario] = []
    problems: list[str] = []
    for path in sorted((base / SCENARIOS_DIR).glob("*/*.json")):
        try:
            s = _scenario(_read_object(path), system, path)
            _check_tests(base, s)
            scenarios.append(s)
        except ValueError as e:
            problems.append(f"{path.relative_to(base).as_posix()}: {e}")
    return scenarios, problems
