"""Shared by every step: where the scenarios and the project's e2e settings live, the exit codes, and the one
error that means "could not run"."""

from __future__ import annotations

# Where the scenario format is explained; named in every message that asks for a scenario to be written or fixed.
GUIDE = "harness/guides/e2e-scenarios.md"
# A scenario is ``Scenario: <name>`` in ``<SCENARIOS_DIR>/<feature>.feature``. Each tool that runs it keeps its
# test at a path derived from ``<feature>/<name>`` (see tools.py), so the scenario needs no field pointing at it.
SCENARIOS_DIR = "tests/e2e"
# What is project-specific: how to start the system under test, and which tool tests each method. Harness
# settings, not a scenario, so it lives with the harness rather than among the tests.
SYSTEM = "harness/e2e/system.json"

EXIT_OK, EXIT_FAILED, EXIT_NEEDS_SCENARIOS, EXIT_ERROR = 0, 1, 2, 3


class E2EError(Exception):
    """Could not run (exit 3): no runner, no system — not an answer about the change."""
