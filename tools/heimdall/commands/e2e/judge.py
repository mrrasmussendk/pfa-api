"""Which scenarios to rerun and whether one is missing: Jev answers typed questions, the thresholds here
decide. Facts come first, as in the review: a scenario whose own files changed always reruns, and a feature
that changed with no scenario always asks for one. Without Jev, the slice map decides (``fallback``)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .change import Change
from .scenarios import Scenario

# Thresholds. A needless rerun costs a minute; a missed regression costs a release, so rerun is set low.
T = {
    "rerun": 0.30,  # rerun_<i> >= → run the scenario
    "new_scenario": 0.60,  # needs_new_scenario >= → ask for a new scenario
}


@dataclass
class Plan:
    rerun: list[Scenario]
    uncovered: list[str]  # features that changed with no scenario: a fact
    unnamed: float | None = None  # Jev's p that client-visible behaviour has no scenario, when over the threshold


def questions(scenarios: list[Scenario]) -> dict[str, dict[str, Any]]:
    """One yes/no per scenario, by its index in ``state.scenarios``, then one about the scenarios not yet written."""
    qs: dict[str, dict[str, Any]] = {
        f"rerun_{i}": {
            "type": "noul",
            "instructions": (
                f"Could the change in `files` alter what `scenarios[{i}]` ({s.id}, method {s.method}) observes from outside "
                "the system — the state its `given` sets up, the action its `when` takes, or the outcome its `then` expects? "
                "Count indirect paths: a dependency's contract, the kernel, the application wiring, the entry point, error "
                "handling, configuration. A change only to docs, unit tests or dev tooling never does."
            ),
            "criteria": {
                "true": "The scenario could now pass or fail differently: rerun it",
                "false": "Nothing the scenario observes changed",
            },
        }
        for i, s in enumerate(scenarios)
    }
    qs["needs_new_scenario"] = {
        "type": "noul",
        "instructions": (
            "Does the change add or alter behaviour a user or client of the system can observe — a new route, screen, "
            "command, field, error, status code or rule — that none of `scenarios` describes with its given/when/then?"
        ),
        "criteria": {"true": "Observable behaviour is left without a scenario", "false": "Every observable change is described"},
    }
    return qs


def _noul(answers: dict[str, Any], key: str, default: float) -> float:
    v = (answers.get(key) or {}).get("noul")
    return float(v) if isinstance(v, (int, float)) else default


def _own_files_changed(s: Scenario, change: Change) -> bool:
    return not s.files.isdisjoint(change.paths)


def _uncovered(scenarios: list[Scenario], change: Change) -> list[str]:
    return sorted(change.touched - {s.feature for s in scenarios})


def decide(answers: dict[str, Any], scenarios: list[Scenario], change: Change) -> Plan:
    """Jev's answers through the thresholds. An unanswered rerun question reruns: silence is not evidence."""
    rerun = [s for i, s in enumerate(scenarios) if _own_files_changed(s, change) or _noul(answers, f"rerun_{i}", 1.0) >= T["rerun"]]
    p = _noul(answers, "needs_new_scenario", 0.0)
    return Plan(rerun, _uncovered(scenarios, change), p if p >= T["new_scenario"] else None)


def fallback(scenarios: list[Scenario], change: Change) -> Plan:
    """Without Jev: the scenarios of every feature the change reaches through the dependency graph, or all of
    them when the kernel or the application changed."""
    rerun = [s for s in scenarios if change.wide or s.feature in change.reach or _own_files_changed(s, change)]
    return Plan(rerun, _uncovered(scenarios, change))
