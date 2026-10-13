"""The runner: start the system under test once, run the chosen scenarios with each tool they name — one
command per tool — and stop it. The run is green only when every tool's command is."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass

from .scenarios import Scenario
from .system import System, running
from .tools import TOOLS, environment


@dataclass(frozen=True)
class RunResult:
    ok: bool
    output: str


Runner = Callable[[list[Scenario]], RunResult]


def by_tool(scenarios: list[Scenario]) -> dict[str, list[Scenario]]:
    """The scenarios each tool runs, in the order the tools first appear."""
    groups: dict[str, list[Scenario]] = {}
    for s in scenarios:
        for t in s.tools:
            groups.setdefault(t, []).append(s)
    return groups


def _run_tool(name: str, scenarios: list[Scenario], base_url: str, cwd: str) -> RunResult:
    argv, extra = TOOLS[name].command(scenarios, base_url)
    p = subprocess.run(
        argv, cwd=cwd, env=environment(extra), capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
    )
    return RunResult(p.returncode == 0, f"[{name}]\n" + (p.stdout + p.stderr).strip())


def system_runner(cwd: str, system: System) -> Runner:
    def run(scenarios: list[Scenario]) -> RunResult:
        with running(system, cwd) as base_url:
            results = [_run_tool(name, group, base_url, cwd) for name, group in by_tool(scenarios).items()]
        return RunResult(all(r.ok for r in results), "\n\n".join(r.output for r in results))

    return run
