"""The scenarios: one Gherkin file per feature, ``<SCENARIOS_DIR>/<feature>.feature``, one ``Scenario:`` per use
case. Only the part of Gherkin a scenario needs is read — tags, ``Feature:``, ``Scenario:``, and the steps
``Given``/``When``/``Then`` with ``And``/``But`` continuing the step before — so a file that reaches for more
(``Background``, an outline, a table) is a problem to fix, not a scenario to skip silently. The steps are prose,
not bindings: nothing executes them; each tool's test does what they say, and ``check_test`` holds it to them."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .base import SCENARIOS_DIR, SYSTEM
from .system import System
from .tools import TOOLS

STEPS = ("given", "when", "then")
SUFFIX = ".feature"
TOOL_TAG = "tool:"  # ``@tool:<name>`` runs a scenario with a tool other than its method's
NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")  # a scenario's name: the last part of its id and of its tests' paths
_STEP = re.compile(r"^(Given|When|Then|And|But)\s+(\S.*)$")
_KEYWORD = re.compile(r"^(Feature|Scenario|Background|Scenario Outline|Scenario Template|Examples|Scenarios|Rule|Example):\s*(.*)$")


@dataclass(frozen=True)
class Scenario:
    id: str  # ``<feature>/<name>``: every test that runs the scenario shares it
    feature: str
    method: str
    tools: tuple[str, ...]
    given: tuple[str, ...]
    when: tuple[str, ...]
    then: tuple[str, ...]  # one clause per ``Then``/``And``/``But``; each tool's test asserts each one by name
    lines: tuple[int, int] = (0, 0)  # first and last line of the scenario in its feature file, tags included

    @property
    def spec_path(self) -> str:
        return f"{SCENARIOS_DIR}/{self.feature}{SUFFIX}"

    @property
    def test_paths(self) -> frozenset[str]:
        return frozenset(TOOLS[t].test_path(self.id) for t in self.tools)

    def as_state(self) -> dict[str, Any]:
        return {"id": self.id, "feature": self.feature, "method": self.method, "given": self.given, "when": self.when, "then": self.then}


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


def known_methods(system: System) -> frozenset[str]:
    """The tags that name a method: the project's, and every method a tool can test."""
    return frozenset(system.tools).union(*(t.methods or () for t in TOOLS.values()))


@dataclass
class _Block:
    """A scenario as read, before its tags are resolved: its name, its own tags and its steps."""

    name: str
    tags: list[str]
    start: int
    end: int = 0
    steps: dict[str, list[str]] | None = None
    last: str = ""


def _add_step(block: _Block, word: str, text: str, n: int) -> None:
    steps = block.steps if block.steps is not None else {k: [] for k in STEPS}
    block.steps = steps
    if word in ("And", "But"):
        if not block.last:
            raise ValueError(f"line {n}: `{word}` must follow a Given, When or Then")
        step = block.last
    else:
        step = word.lower()
        if STEPS.index(step) < STEPS.index(block.last or "given"):
            raise ValueError(f"line {n}: `{word}` after `{block.last.title()}` — a scenario's steps go Given, When, Then")
    steps[step].append(" ".join(text.split()))
    block.last = step


def _tags(line: str, n: int) -> list[str]:
    tags = line.split()
    if not all(t.startswith("@") and len(t) > 1 for t in tags):
        raise ValueError(f"line {n}: a tag line holds only @tags")
    return [t[1:] for t in tags]


class _Reader:
    """The feature file, line by line: the feature's tags and title, then one ``_Block`` per scenario."""

    def __init__(self, feature: str) -> None:
        self.feature = feature
        self.feature_tags: list[str] = []
        self.titled = False
        self.pending: list[str] = []
        self.pending_from = 0
        self.blocks: list[_Block] = []

    def keyword(self, word: str, title: str, n: int) -> None:
        if word == "Feature":
            if self.titled:
                raise ValueError(f"line {n}: one Feature per file")
            if title != self.feature:
                raise ValueError(f"line {n}: `Feature: {title}` — the file names feature `{self.feature}`")
            self.feature_tags, self.pending, self.pending_from, self.titled = self.pending, [], 0, True
        elif word == "Scenario":
            if not self.titled:
                raise ValueError(f"line {n}: `Feature: {self.feature}` must come first")
            if not NAME.match(title):
                raise ValueError(f"line {n}: scenario name {title!r} is not lower-case letters, digits, '-' and '_'")
            self.blocks.append(_Block(title, self.pending, self.pending_from or n))
            self.pending, self.pending_from = [], 0
        else:
            raise ValueError(f"line {n}: `{word}:` is not supported — write each use case as its own Scenario")

    def line(self, raw: str, n: int) -> None:
        line = raw.strip()
        if not line or line.startswith("#"):
            return
        if line.startswith("@"):
            self.pending += _tags(line, n)
            self.pending_from = self.pending_from or n
            return
        if m := _KEYWORD.match(line):
            self.keyword(m.group(1), m.group(2).strip(), n)
        elif self.blocks and (step := _STEP.match(line)):
            _add_step(self.blocks[-1], step.group(1), step.group(2), n)
            self.blocks[-1].end = n
        elif self.blocks:
            raise ValueError(f"line {n}: not a step — start it with Given, When, Then, And or But")
        elif not self.titled:
            raise ValueError(f"line {n}: `Feature: {self.feature}` must come first")
        # anything else between the Feature line and the first scenario is the feature's description


def _method(tags: list[str], feature_tags: list[str], system: System) -> str:
    known = known_methods(system)
    for where in (tags, feature_tags):
        methods = [t for t in where if t in known]
        if len(methods) > 1:
            raise ValueError(f"tags name more than one method: {methods!r}")
        if methods:
            return methods[0]
    raise ValueError(f"no method tag — tag it, or its Feature, with one of {sorted(known)!r}")


def _tools(method: str, tags: list[str], feature_tags: list[str], system: System) -> tuple[str, ...]:
    named = [t[len(TOOL_TAG) :] for t in tags if t.startswith(TOOL_TAG)] or [
        t[len(TOOL_TAG) :] for t in feature_tags if t.startswith(TOOL_TAG)
    ]
    if not named:
        if method not in system.tools:
            raise ValueError(f"method {method!r} has no tool in {SYSTEM} — add one, or tag the scenario @{TOOL_TAG}<tool>")
        named = [system.tools[method]]
    return check_tools(method, named, system)


def _scenario(block: _Block, reader: _Reader, system: System) -> Scenario:
    steps = block.steps or {}
    missing = [s.title() for s in STEPS if not steps.get(s)]
    if missing:
        raise ValueError(f"no {', '.join(missing)} step")
    method = _method(block.tags, reader.feature_tags, system)
    tools = _tools(method, block.tags, reader.feature_tags, system)
    sid = f"{reader.feature}/{block.name}"
    given, when, then = (tuple(steps[s]) for s in STEPS)
    return Scenario(sid, reader.feature, method, tools, given, when, then, (block.start, block.end))


def parse_feature(text: str, feature: str, system: System) -> tuple[list[Scenario], list[str]]:
    """The scenarios of one feature file, and one problem per scenario that is not one. A problem with the file
    itself (no Feature line, an unsupported keyword) is raised: nothing in it can be trusted."""
    reader = _Reader(feature)
    for n, raw in enumerate(text.splitlines(), start=1):
        reader.line(raw, n)
    if not reader.titled:
        raise ValueError(f"no `Feature: {feature}` line")
    scenarios: list[Scenario] = []
    problems: list[str] = []
    seen: set[str] = set()
    for block in reader.blocks:
        try:
            if block.name in seen:
                raise ValueError("a scenario of that name comes earlier in the file")
            seen.add(block.name)
            scenarios.append(_scenario(block, reader, system))
        except ValueError as e:
            problems.append(f"scenario {block.name} (line {block.start}): {e}")
    return scenarios, problems


def _check_tests(root: Path, s: Scenario) -> list[str]:
    """What is wrong with the tests that run ``s``: a test missing, or not asserting exactly its ``then``."""
    problems = []
    for name in s.tools:
        tool = TOOLS[name]
        try:
            test = (root / tool.test_path(s.id)).read_text(encoding="utf-8")
        except OSError:
            problems.append(f"no {name} test runs it — write {tool.test_path(s.id)}")
            continue
        problems += [f"{tool.test_path(s.id)}: {p}" for p in tool.check_test(s, test)]
    return problems


def load_scenarios(root: str, system: System) -> tuple[list[Scenario], list[str]]:
    """Every scenario under ``SCENARIOS_DIR``, by id, and one ``"<path>: <what is wrong>"`` per one that is not."""
    base = Path(root)
    scenarios: list[Scenario] = []
    problems: list[str] = []
    for path in sorted((base / SCENARIOS_DIR).glob(f"*{SUFFIX}")):
        rel = path.relative_to(base).as_posix()
        try:
            found, wrong = parse_feature(path.read_text(encoding="utf-8"), path.stem, system)
        except (ValueError, OSError, UnicodeDecodeError) as e:
            problems.append(f"{rel}: {e}")
            continue
        problems += [f"{rel}: {p}" for p in wrong]
        for s in found:
            test_problems = _check_tests(base, s)
            problems += [f"{rel}: scenario {s.id.split('/')[1]}: {p}" for p in test_problems]
            if not test_problems:
                scenarios.append(s)
    return scenarios, problems
