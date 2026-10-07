"""The questions: fixed, typed, greppable. Wording is the whole interface to the model — each
question names what it judges and the criteria name what each answer means."""

from __future__ import annotations

from typing import Any

YES_NO = {"true": "Yes", "false": "No"}


def questions() -> dict[str, dict[str, Any]]:
    return {
        # ---- gates
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
        # ---- applicability-gated judgments
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
        # ---- rubrics
        "correctness": {
            "type": "score",
            "instructions": "How likely is the changed code to behave as intended, including edge cases visible in the diff (empty input, None, boundaries, error paths)?",
            "criteria": ["Likely broken", "Possibly defective", "Probably correct", "Clearly correct"],
        },
        "clean_code": {
            "type": "score",
            "instructions": "Rate the readability and craft of the changed code: names that say what things are, small single-purpose functions, no duplication, comments that explain why rather than what, consistency with the surrounding style, no dead code.",
            "criteria": ["Hard to follow", "Acceptable", "Clean", "Exemplary"],
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
        # ---- labels for the report
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
