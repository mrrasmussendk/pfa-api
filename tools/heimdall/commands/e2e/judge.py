"""Which scenarios to rerun, whether one is missing, and whether a scenario's text still says what its test does:
Jev answers typed questions, the thresholds here decide. Facts come first, as in the review: a scenario whose
own text or test changed always reruns, and a feature that changed with no scenario always asks for one.
Without Jev, the slice map decides (``fallback``), and the text is held to the test only clause for clause."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .change import Change
from .scenarios import Scenario

# Thresholds. A needless rerun costs a minute; a missed regression costs a release, so rerun is set low.
T = {
    "rerun": 0.30,  # rerun_<i> >= → run the scenario
    "new_scenario": 0.60,  # needs_new_scenario >= → ask for a new scenario
    "drift": 0.50,  # drifted_<i> >= → the scenario's text and its test disagree: one of them must change
}


@dataclass
class Plan:
    rerun: list[Scenario]
    uncovered: list[str]  # features that changed with no scenario: a fact
    unnamed: float | None = None  # Jev's p that client-visible behaviour has no scenario, when over the threshold
    drifted: list[tuple[Scenario, float]] = field(default_factory=list)  # text and test disagree, with Jev's p


def drift_questions(scenarios: list[Scenario], edited: list[Scenario]) -> dict[str, dict[str, Any]]:
    """One yes/no per scenario whose text or test changed, by its index in ``state.scenarios``, where its tests are."""
    return {
        f"drifted_{scenarios.index(s)}": {
            "type": "noul",
            "instructions": (
                f"`scenarios[{scenarios.index(s)}]` ({s.id}) is a use case in prose, and `scenarios[{scenarios.index(s)}].tests` "
                "the tests that run it. Do the tests do something the prose does not say — set up or send an input the "
                "`given`/`when` leave out, assert an outcome the `then` does not state — or leave out something it does? "
                "A concrete value standing for a general phrase is fine; what counts is whether a reader of the prose "
                "alone would be surprised by what the test does."
            ),
            "criteria": {
                "true": "The prose and the tests disagree: one of them must change",
                "false": "The prose says what the tests do",
            },
        }
        for s in edited
    }


def questions(scenarios: list[Scenario], edited: list[Scenario] | None = None) -> dict[str, dict[str, Any]]:
    """One rerun question per scenario, by its index in ``state.scenarios``, one about the scenarios not yet
    written, and the drift question for each ``edited`` scenario."""
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
    return {**qs, **drift_questions(scenarios, edited or [])}


def _noul(answers: dict[str, Any], key: str, default: float) -> float:
    v = (answers.get(key) or {}).get("noul")
    return float(v) if isinstance(v, (int, float)) else default


def own_files_changed(s: Scenario, change: Change) -> bool:
    """Whether the change touched the scenario's own lines of its feature file, or any test that runs it."""
    return not s.test_paths.isdisjoint(change.paths) or change.touches(s.spec_path, *s.lines)


def drifted(answers: dict[str, Any], scenarios: list[Scenario], edited: list[Scenario]) -> list[tuple[Scenario, float]]:
    """The edited scenarios Jev finds the text and the test of disagree. Unanswered is not a finding: the clause
    check has already held the test to the text."""
    found = [(s, _noul(answers, f"drifted_{scenarios.index(s)}", 0.0)) for s in edited]
    return [(s, p) for s, p in found if p >= T["drift"]]


def _uncovered(scenarios: list[Scenario], change: Change) -> list[str]:
    return sorted(change.touched - {s.feature for s in scenarios})


def decide(answers: dict[str, Any], scenarios: list[Scenario], change: Change) -> Plan:
    """Jev's answers through the thresholds. An unanswered rerun question reruns: silence is not evidence."""
    rerun = [s for i, s in enumerate(scenarios) if own_files_changed(s, change) or _noul(answers, f"rerun_{i}", 1.0) >= T["rerun"]]
    p = _noul(answers, "needs_new_scenario", 0.0)
    edited = [s for s in scenarios if own_files_changed(s, change)]
    return Plan(rerun, _uncovered(scenarios, change), p if p >= T["new_scenario"] else None, drifted(answers, scenarios, edited))


def fallback(scenarios: list[Scenario], change: Change) -> Plan:
    """Without Jev: the scenarios of every feature the change reaches through the dependency graph, or all of
    them when the kernel or the application changed."""
    rerun = [s for s in scenarios if change.wide or s.feature in change.reach or own_files_changed(s, change)]
    return Plan(rerun, _uncovered(scenarios, change))
