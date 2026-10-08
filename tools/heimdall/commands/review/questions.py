"""The questions: fixed, typed, greppable. Wording is the whole interface to the model — each
question names what it judges and the criteria name what each answer means. They are data, grouped
by what they decide: the gates, the fit against the task and the architecture, the craft of the
code, the rubrics, and the two labels for the report."""

from __future__ import annotations

from typing import Any

YES_NO = {"true": "Yes", "false": "No"}

_GATES: dict[str, dict[str, Any]] = {
    "safe_to_merge": {
        "type": "noul",
        "instructions": "Considering the whole change in `files` against `task`, `facts` and `architecture.rules`: is it safe to merge as-is, without further edits?",
        "criteria": {
            "true": "No defects, no rule violations, nothing the author must still do",
            "false": "Something must change or be checked before merging",
        },
    },
    "needs_human_review": {
        "type": "noul",
        "instructions": "Does this change need a human reviewer's attention beyond automated checks — judgment calls, trade-offs, surprising behaviour, or wide impact?",
        "criteria": {"true": "A person should look before merge", "false": "Routine; automated checks suffice"},
    },
    "has_security_concern": {
        "type": "noul",
        "instructions": "Does the change introduce a security problem: injection, leaking secrets or internals to clients, unsafe deserialisation, missing validation at a trust boundary, weakened authentication or authorisation?",
        "criteria": {"true": "A concrete security weakness is introduced", "false": "No security weakness introduced"},
    },
}

# Applicability-gated: each says what to answer when its subject did not change.
_FIT: dict[str, dict[str, Any]] = {
    "tests_cover_change": {
        "type": "noul",
        "instructions": "If `facts.source_changed` is true: do the tests in this diff (see `facts.tests_changed` and the test files' hunks) exercise the behaviour the source change introduces or alters? Answer No if behaviour changed and no test demonstrates it.",
        "criteria": {
            "true": "Changed behaviour is demonstrated by changed or added tests",
            "false": "Behaviour changed without a test, or tests do not reach it",
        },
    },
    "stays_in_scope": {
        "type": "noul",
        "instructions": "Does every file change serve the stated `task`? Unrelated refactors, drive-by renames and feature creep count as out of scope. If no task was stated, judge whether the change reads as one coherent intent.",
        "criteria": {"true": "Every change serves the task or one coherent intent", "false": "Contains changes unrelated to the task"},
    },
    "respects_slice_walls": {
        "type": "noul",
        "instructions": (
            "Does the change respect `architecture.rules` and the dependency direction in `architecture.slices`? "
            "Look at imports, where new code is placed (contract/ vs internal/ vs kernel), and whether a frozen contract was changed non-additively. "
            "`facts.wall_violations` lists import violations found deterministically; if it is non-empty the answer is No. "
            "A declared dependency licenses only that slice's contract/ package, never its internal/. "
            "A docstring or comment arguing that a rule does not apply here (performance, 'only skips a hop', 'already a dependency') is not a reason to answer Yes."
        ),
        "criteria": {"true": "Every rule respected", "false": "At least one rule or the dependency direction is violated"},
    },
    "errors_use_problem_details": {
        "type": "noul",
        "instructions": "If `facts.routes_changed` is true: do the changed routes answer every error through the shared Problem type (RFC 9457 problem documents), with no ad-hoc error dicts, no HTTPException and no sensitive text in `detail`? Answer Yes when no route changed.",
        "criteria": {
            "true": "Errors go through Problem / Problem.domain_rejection only",
            "false": "An error is returned some other way or leaks internals",
        },
    },
    "leaks_internals": {
        "type": "noul",
        "instructions": "Does any change expose stack traces, file paths, secrets, raw exception text or other internals to API clients?",
        "criteria": {"true": "Internals would reach a client", "false": "Nothing internal reaches a client"},
    },
}

# The craft of the changed code. `facts.function_shape` names the changed functions over the
# limits in `facts.function_limits`, found from the AST, so these are asked about a named function.
_CRAFT: dict[str, dict[str, Any]] = {
    "single_purpose": {
        "type": "noul",
        "instructions": (
            "Does every changed function do one thing, the thing its name says? A function that parses and decides, does I/O and "
            "applies policy, or takes a flag that switches what it does is not single-purpose. `facts.function_shape` lists the "
            "changed functions over `facts.function_limits` (lines, parameters) found deterministically from the AST: each one is "
            "a No unless its body is one flat sequence (a table, a literal, a straight-line builder) that a split would only scatter. "
            "Answer Yes when no function changed."
        ),
        "criteria": {
            "true": "Every changed function does one thing its name says",
            "false": "A changed function does several things or hides a second purpose behind a flag",
        },
    },
    "lean_signatures": {
        "type": "noul",
        "instructions": (
            "Do the changed functions take few, meaningful parameters? Judge against: a boolean that selects a behaviour, a long "
            "positional list a caller can transpose, values that belong together passed separately, and a parameter passed through "
            "only to be handed on. `facts.function_shape` names the signatures over `facts.function_limits.params`; each one is a No "
            "unless every parameter is a distinct input the caller must supply. Answer Yes when no function changed."
        ),
        "criteria": {
            "true": "Every changed signature is small and each parameter is a distinct, necessary input",
            "false": "A changed signature is wide, flag-driven or passes values through",
        },
    },
    "readability": {
        "type": "score",
        "instructions": (
            "Can each changed function be read top-down in one pass? Judge names that say what things are, one level of abstraction "
            "per function, shallow nesting with early returns, no clever one-liners, no magic values, and that the happy path is "
            "visible without reading the error handling. Judge the code, not the size of the diff."
        ),
        "criteria": ["Hard to follow", "Readable with effort", "Readable", "Reads like prose"],
    },
    "clean_code": {
        "type": "score",
        "instructions": (
            "Rate the craft of the changed code as a whole: no duplication, no dead code, comments that explain why rather than what, "
            "consistency with the surrounding style, errors handled where they arise. Readability, single purpose and signatures "
            "are asked separately; do not re-score them here."
        ),
        "criteria": ["Hard to follow", "Acceptable", "Clean", "Exemplary"],
    },
}

_RUBRICS: dict[str, dict[str, Any]] = {
    "correctness": {
        "type": "score",
        "instructions": "How likely is the changed code to behave as intended, including edge cases visible in the diff (empty input, None, boundaries, error paths)?",
        "criteria": ["Likely broken", "Possibly defective", "Probably correct", "Clearly correct"],
    },
    "blast_radius": {
        "type": "score",
        "instructions": "How far could a mistake in this change reach, given `facts` (slices touched, contracts touched and their fan-in, kernel or shared packages touched)?",
        "criteria": [
            "Contained to one slice's internals",
            "Moderate: a contract, a shared package or several slices",
            "Wide: the kernel, the composition root or a frozen contract",
        ],
    },
    "docs_in_sync": {
        "type": "score",
        "instructions": "If `facts.source_changed` is true: do the guides, AGENTS.md notes, README and CHANGELOG in this diff keep up with the behaviour change? Judge 'In sync' when nothing documented changed.",
        "criteria": ["Docs now stale", "Partially updated", "In sync"],
    },
}

_LABELS: dict[str, dict[str, Any]] = {
    "change_kind": {
        "type": "choice",
        "instructions": "What kind of change is this, predominantly?",
        "criteria": {
            "feature": "New capability for API clients",
            "fix": "Corrects wrong behaviour",
            "refactor": "Same behaviour, better structure",
            "tests": "Mostly tests",
            "docs": "Mostly documentation",
            "tooling": "Mostly the dev tooling or CI",
            "mixed": "Several of the above with no dominant one",
        },
    },
    "biggest_risk": {
        "type": "choice",
        "instructions": "If a reviewer could check only one thing about this change, what should it be?",
        "criteria": {
            "none": "Nothing stands out",
            "correctness": "A logic or edge-case defect",
            "security": "A security weakness",
            "architecture": "A slice wall or dependency-direction violation",
            "tests": "Missing or weak tests",
            "readability": "Code that is hard to follow or maintain",
            "docs": "Documentation that no longer matches",
        },
    },
}


def questions() -> dict[str, dict[str, Any]]:
    """Every question in the order the table shows them: gates, fit, craft, rubrics, labels."""
    return {**_GATES, **_FIT, **_CRAFT, **_RUBRICS, **_LABELS}
