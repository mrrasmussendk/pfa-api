"""``heimdall e2e new``: add a scenario to its feature's file and write, for each tool that runs it, a test stub
that asserts every clause of its ``then`` and fails until the agent writes them. The scenario is the agent's to
phrase; the files, their place and their link are the harness's, so a new use case is one command away from
being enforced."""

from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

from .base import SCENARIOS_DIR, SYSTEM, E2EError
from .scenarios import STEPS, SUFFIX, TOOL_TAG, Scenario, check_tools, parse_feature
from .system import System
from .tools import TOOLS

_ID = re.compile(r"^[a-z0-9][a-z0-9_-]*/[a-z0-9][a-z0-9_-]*$")
_FEATURE_TAGS = re.compile(r"^\s*((?:@\S+\s*)+)\n\s*Feature:", re.M)


class Steps(NamedTuple):
    """Each step's clauses: the first is the step itself, the rest its ``And``s."""

    given: tuple[str, ...]
    when: tuple[str, ...]
    then: tuple[str, ...]


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
    given, when, then = (tuple(" ".join(c.split()) for c in clauses) for clauses in ask.steps)
    if not all(step and all(step) for step in (given, when, then)):
        raise E2EError("--given, --when and --then are all required and non-empty")
    tools = _tools(ask.method, ask.tool, system)
    return Scenario(ask.sid, ask.sid.split("/")[0], ask.method, tools, given, when, then)


def _feature_tags(text: str) -> list[str]:
    m = _FEATURE_TAGS.search(text)
    return [t[1:] for t in m.group(1).split()] if m else []


def _block(s: Scenario, tags: list[str]) -> str:
    """The scenario as Gherkin: its tags, its name, then each step's clauses as the step and its ``And``s."""
    lines = [f"  {' '.join('@' + t for t in tags)}"] if tags else []
    lines.append(f"  Scenario: {s.id.split('/')[1]}")
    for step, clauses in zip(STEPS, (s.given, s.when, s.then), strict=True):
        lines += [f"    {step.title() if i == 0 else 'And'} {c}" for i, c in enumerate(clauses)]
    return "\n".join(lines) + "\n"


def _feature_text(root: str, s: Scenario, ask: NewScenario, system: System) -> str:
    """The feature file with ``s`` added: a new file tagged with the method, or the existing one, checked, plus ``s``
    tagged with whatever the file does not already say."""
    path = Path(root, s.spec_path)
    tool_tag = [f"{TOOL_TAG}{ask.tool}"] if ask.tool else []
    if not path.exists():
        return f"@{s.method}\nFeature: {s.feature}\n\n" + _block(s, tool_tag)
    text = path.read_text(encoding="utf-8")
    try:
        existing, _ = parse_feature(text, s.feature, system)
    except ValueError as e:
        raise E2EError(f"{s.spec_path}: {e} — fix it before adding to it") from None
    if s.id in {e.id for e in existing} or re.search(rf"^\s*Scenario:\s*{re.escape(s.id.split('/')[1])}\s*$", text, re.M):
        raise E2EError(f"already exists: scenario {s.id.split('/')[1]} in {s.spec_path}")
    method_tag = [] if s.method in _feature_tags(text) else [s.method]
    return text.rstrip("\n") + "\n\n" + _block(s, method_tag + tool_tag)


def scaffold(root: str, ask: NewScenario, system: System) -> list[str]:
    """The paths written: the feature file, then one failing test stub per tool. No test is overwritten."""
    s = _scenario(ask, system)
    tests = {TOOLS[t].test_path(s.id): TOOLS[t].stub(s) for t in s.tools}
    wrong = [p for t in s.tools for p in TOOLS[t].check_test(s, tests[TOOLS[t].test_path(s.id)])]
    if wrong:  # what the stub cannot satisfy, the scenario as asked for cannot either
        raise E2EError("; ".join(wrong))
    existing = [p for p in tests if Path(root, p).exists()]
    if existing:
        raise E2EError(f"already exists: {', '.join(existing)}")
    files = {s.spec_path: _feature_text(root, s, ask, system), **tests}
    for path, text in files.items():
        Path(root, path).parent.mkdir(parents=True, exist_ok=True)
        Path(root, path).write_text(text, encoding="utf-8", newline="\n")
    return list(files)


def new_advice(feature: str, system: System) -> str:
    """The command that answers a missing scenario, for the Stop hook to hand back to the agent."""
    methods = "|".join(system.methods)
    return (
        f'heimdall e2e new {feature}/<name> --method {methods} --given "..." --when "..." --then "..." [--then "..."]'
        f" — then make the test it writes assert each clause of the `then` ({SCENARIOS_DIR}/{feature}{SUFFIX})"
    )
