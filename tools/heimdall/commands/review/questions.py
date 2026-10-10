"""The questions: narrow fault checks, built in code per item. Jev's guidance is to ask "the most explicit,
narrow, specific, atomic questions you can" and one per candidate: a broad question ("is this correct?",
"is it safe to merge?") hides several judgments behind one answer and names no place. So each changed
function is asked a few one-property questions about itself (``functions.<id>`` in the state), a route a
few more, and the change as a whole which file, if any, is out of scope or misplaced.

Every question is phrased so that "yes", or a named file, is a fault someone made. None asks whether to
merge: a person always decides that. ``file_questions()`` is asked about each edited application file,
whole, so clean code is judged on the file the change leaves behind and the verdict can name it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .items import Item

MAX_CHOICE_OPTIONS = 255  # System One's limit on a choice question's criteria

# Asked about every changed function. ``{f}`` becomes the function's path in the state, ``functions.f3``.
FUNCTION_CHECKS: dict[str, str] = {
    "mixes_concerns": (
        "Does `{f}.source` do two things that would each stand as a function of its own, such as parsing input and deciding "
        "on it, or deciding and writing to a store? A step that does one thing and reports it, or calls one helper per step, does one thing."
    ),
    "mode_parameter": (
        "Does a boolean or mode parameter of `{f}.source` make it do a different job, rather than tune the same one? "
        "Reading an option that only skips or repeats the same work is tuning."
    ),
    "mishandles_edge": "Is there an input `{f}.source` can receive (empty, None, zero, a boundary value) that it handles wrongly?",
    "swallows_error": "Does `{f}.source` catch an exception and carry on without handling it, re-raising it or reporting it?",
    "untested": "Is `{f}` changed with no test among the entries of `files` whose area is `tests` that exercises what changed?",
    "unsafe_input": "Does `{f}.source` pass input it did not create into a shell command, a file path, a query or a log without validating it?",
}

# Asked in addition about a function in a feature's HTTP edge: it answers clients.
ROUTE_CHECKS: dict[str, str] = {
    "error_not_problem": "Does `{f}.source` answer an error other than by raising `Problem` (an HTTPException, a returned error dict, a status set by hand)?",
    "leaks_internals": "Can `{f}.source` put a stack trace, a file path, a secret or raw exception text into a response?",
}

_FAULT = {"true": "Yes: the fault is there", "false": "No"}


@dataclass(frozen=True)
class Asked:
    """One question as the policy reads its answer: which check, and about which function (``None``: the change)."""

    key: str
    check: str
    item: Item | None


def _noul(text: str) -> dict[str, Any]:
    return {"type": "noul", "instructions": text, "criteria": _FAULT}


def _which_file(text: str, paths: list[str]) -> dict[str, Any]:
    options = ["none", *paths][:MAX_CHOICE_OPTIONS]
    return {"type": "choice", "instructions": text, "criteria": dict.fromkeys(options)}


def _change_questions(paths: list[str]) -> dict[str, dict[str, Any]]:
    """The few questions about the change as a whole: its scope, its placement and its docs."""
    return {
        "out_of_scope": _which_file("Which changed file does not serve `task`? Choose `none` when every file does.", paths),
        "misplaced": _which_file(
            "Which changed file puts code where `architecture.rules` say it does not belong (framework code in a feature, "
            "feature logic in the HTTP edge, implementation in a contract)? Choose `none` when every file is placed right.",
            paths,
        ),
        "docs_stale": _noul(
            "Does a hunk in `files` change behaviour a user or an agent relies on (an endpoint, a command, a flag, an output) "
            "while no documentation file in `files` describes the change?"
        ),
    }


def ask(items: list[Item], paths: list[str]) -> tuple[dict[str, dict[str, Any]], list[Asked]]:
    """The questions for one review, by key, and how to read each answer: per function, per route, and the change."""
    qs: dict[str, dict[str, Any]] = {}
    asked: list[Asked] = []
    for item in items:
        checks = {**FUNCTION_CHECKS, **(ROUTE_CHECKS if item.route else {})}
        for check, text in checks.items():
            key = f"{item.id}_{check}"
            qs[key] = _noul(text.format(f=f"functions.{item.id}"))
            asked.append(Asked(key, check, item))
    for key, q in _change_questions(paths).items():
        qs[key] = q
        asked.append(Asked(key, key, None))
    return qs, asked


# Clean code, asked about one file whole (`file.content`): the state of a craft call is the file as the
# change leaves it, with `facts.changed_lines` marking what this change touched. A diff would show only
# the lines that moved; the file shows the duplication two hunks apart and the branch left dead.
_FILE_CRAFT: dict[str, dict[str, Any]] = {
    "clean_code": {
        "type": "score",
        "instructions": (
            "Rate the craft of `file.content` as a whole — the file as it now stands, not only the lines in `facts.changed_lines`: "
            "no duplication, no dead code, comments that explain why rather than what, one consistent style throughout, errors "
            "handled where they arise. Function length, single purpose and signatures are judged elsewhere; do not re-score them here."
        ),
        "criteria": ["Hard to follow", "Acceptable", "Clean", "Exemplary"],
    },
    "clean_code_limit": {
        "type": "choice",
        "instructions": "What keeps `file.content` from exemplary most? Choose `none` only when nothing would make the file cleaner.",
        "criteria": {
            "none": "Nothing: it is exemplary",
            "duplication": "The same logic written twice",
            "dead_code": "Unused code, parameters or branches",
            "comments": "Comments that say what instead of why, or missing where a why is needed",
            "style": "Inconsistent with the rest of the file",
            "error_handling": "Errors handled far from where they arise, or swallowed",
        },
    },
}


def file_questions() -> dict[str, dict[str, Any]]:
    """The questions asked about each edited application file, whole: clean code and what limits it."""
    return dict(_FILE_CRAFT)
