"""``heimdall e2e new``: write a scenario in the scenario shape and, for each tool that runs it, a test stub
that fails until the agent asserts the ``then``. The scenario is the agent's to phrase; the files, their place
and their link are the harness's, so a new use case is one command away from being enforced."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import NamedTuple

from .base import SCENARIOS_DIR, SYSTEM, E2EError
from .scenarios import Scenario, check_tools
from .system import System
from .tools import TOOLS

_ID = re.compile(r"^[a-z0-9][a-z0-9_-]*/[a-z0-9][a-z0-9_-]*$")


class Steps(NamedTuple):
    given: str
    when: str
    then: str


class NewScenario(NamedTuple):
    """What the agent asks for: the id, the method, the three steps, and a tool when not the method's default."""

    sid: str
    method: str
    steps: Steps
    tool: str | None = None


def _tools(method: str, tool: str | None, system: System) -> tuple[str, ...]:
    """The tool asked for, or the one ``SYSTEM`` names for the method — either way one the project uses."""
    if tool is None and method not in system.tools:
        raise E2EError(f"method {method!r} has no tool in {SYSTEM}; methods: {system.methods!r} (or pass --tool)")
    try:
        return check_tools(method, [tool or system.tools[method]], system)
    except ValueError as e:
        raise E2EError(str(e)) from None


def _scenario(ask: NewScenario, system: System) -> Scenario:
    if not _ID.match(ask.sid):
        raise E2EError(f"{ask.sid!r} is not <feature>/<name> in lower-case letters, digits, '-' and '_'")
    if not all(step.strip() for step in ask.steps):
        raise E2EError("--given, --when and --then are all required and non-empty")
    tools = _tools(ask.method, ask.tool, system)
    return Scenario(ask.sid, ask.sid.split("/")[0], ask.method, tools, *(" ".join(step.split()) for step in ask.steps))


def scaffold(root: str, ask: NewScenario, system: System) -> list[str]:
    """The paths written: the scenario, then one failing test stub per tool. Nothing is overwritten."""
    s = _scenario(ask, system)
    files = {s.spec_path: json.dumps(s.as_json(), indent=2) + "\n", **{TOOLS[t].test_path(s.id): TOOLS[t].stub(s) for t in s.tools}}
    existing = [p for p in files if Path(root, p).exists()]
    if existing:
        raise E2EError(f"already exists: {', '.join(existing)}")
    for path, text in files.items():
        Path(root, path).parent.mkdir(parents=True, exist_ok=True)
        Path(root, path).write_text(text, encoding="utf-8", newline="\n")
    return list(files)


def new_advice(feature: str, system: System) -> str:
    """The command that answers a missing scenario, for the Stop hook to hand back to the agent."""
    methods = "|".join(system.methods)
    return (
        f'heimdall e2e new {feature}/<name> --method {methods} --given "..." --when "..." --then "..."'
        f" — then make the test it writes under {SCENARIOS_DIR}/ or the tool's folder assert the `then`"
    )
