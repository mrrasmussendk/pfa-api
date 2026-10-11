"""``heimdall e2e`` — the BDD scenarios a change reaches, rerun against the running system, whatever the system is.

A scenario is a use case written in Gherkin — ``Given``/``When``/``Then``, ``And`` for each further clause — as
``Scenario: <name>`` in ``tests/e2e/<feature>.feature``. A tag names its method (``@API``, ``@UI``, ``@service``,
...) and ``harness/e2e/system.json`` the tool that tests it (``@tool:<tool>`` overrides); each tool keeps its test
at a path derived from ``<feature>/<name>`` and asserts each ``then`` clause by name, so the text and the test are
checked against each other. ``system.json`` is the project's part: how to start the system under test and which
tool tests each method. One module per step:

- ``scenarios``  read the feature files, and check each scenario against the project's tools and its tests;
- ``system``     the project's settings, and the started system for the length of a run;
- ``tools``      postman, playwright, pytest: where each keeps its test, what it asserts, the failing stub, the run command;
- ``scaffold``   ``heimdall e2e new``: the scenario and one failing test per tool;
- ``change``     the change — committed since the base, uncommitted and untracked — and the features it reaches;
- ``judge``      Jev answers typed questions (rerun this scenario? is one missing? do its text and test agree?);
                 thresholds in code decide, facts first; without Jev the slice map decides (``fallback``);
- ``runner``     start the system, run each tool's batch, stop it;
- ``command``    argument parsing, the remembered green run, exit codes.

Exit codes: 0 green or nothing to run · 1 a scenario failed · 2 a scenario is missing or malformed · 3 could not run.
"""

from __future__ import annotations

from ..review import Streams
from .base import EXIT_ERROR, EXIT_FAILED, EXIT_NEEDS_SCENARIOS, EXIT_OK, GUIDE, SCENARIOS_DIR, SYSTEM, E2EError
from .change import Change, fingerprint, working_diff
from .command import USAGE, Seams, run
from .judge import Plan, T, decide, drift_questions, fallback, questions
from .runner import Runner, RunResult, by_tool, system_runner
from .scaffold import NewScenario, Steps, new_advice, scaffold
from .scenarios import Scenario, load_scenarios, parse_feature
from .system import System, load_system
from .tools import NOT_WRITTEN, TOOLS, Tool

__all__ = [
    "EXIT_ERROR",
    "EXIT_FAILED",
    "EXIT_NEEDS_SCENARIOS",
    "EXIT_OK",
    "GUIDE",
    "NOT_WRITTEN",
    "SCENARIOS_DIR",
    "SYSTEM",
    "TOOLS",
    "USAGE",
    "Change",
    "E2EError",
    "NewScenario",
    "Plan",
    "RunResult",
    "Runner",
    "Scenario",
    "Seams",
    "Steps",
    "Streams",
    "System",
    "T",
    "Tool",
    "by_tool",
    "decide",
    "drift_questions",
    "fallback",
    "fingerprint",
    "load_scenarios",
    "load_system",
    "new_advice",
    "parse_feature",
    "questions",
    "run",
    "scaffold",
    "system_runner",
    "working_diff",
]
