"""The policy: each answer becomes a row (a fault's probability, where, and whether it failed); a failing row
blocks naming the place and the fix, an unsure one is a note, facts block on their own, and nothing ever says
a change is safe to merge. Blast radius is computed from the facts, not asked."""

from __future__ import annotations

from typing import Any

import pytest

from heimdall.commands.review.items import Item
from heimdall.commands.review.policy import FAIL, PASS, UNSURE, Judgment, Row, blast_radius, decide, evaluate
from heimdall.commands.review.questions import Asked

RUN = Item("f1", "src/pfa/features/kvad/internal/engine.py", 3, "run", "def run(): ...", route=False)
MIXES = Asked("f1_mixes_concerns", "mixes_concerns", RUN)
SCOPE = Asked("out_of_scope", "out_of_scope", None)


def _noul(p: float) -> dict[str, Any]:
    return {"type": "noul", "noul": p}


def _choice(**probs: float) -> dict[str, Any]:
    top = max(probs, key=lambda k: probs[k])
    return {"type": "choice", "choice": top, "probabilities": probs, "confidence": probs[top]}


@pytest.mark.parametrize(("p", "state"), [(0.8, FAIL), (0.5, UNSURE), (0.1, PASS)])
def test_a_function_check_is_read_as_a_row_at_that_function(p: float, state: str) -> None:
    edge = Asked("f1_mishandles_edge", "mishandles_edge", RUN)

    (row,) = evaluate({"f1_mishandles_edge": _noul(p)}, [edge])

    assert row == Row("f1_mishandles_edge", "mishandles_edge", "src/pfa/features/kvad/internal/engine.py:3 run", p, state)


def test_a_file_choice_fails_at_the_file_it_names() -> None:
    (row,) = evaluate({"out_of_scope": _choice(none=0.2, **{"b.md": 0.7})}, [SCOPE])

    assert (row.where, row.p, row.state) == ("b.md", 0.7, FAIL)


def test_none_chosen_or_no_answer_is_a_pass() -> None:
    rows = evaluate({"out_of_scope": _choice(none=0.9, **{"b.md": 0.1})}, [SCOPE, MIXES])

    assert [r.state for r in rows] == [PASS, PASS]


def _judgment(*rows: Row, files: dict[str, dict[str, Any]] | None = None) -> Judgment:
    return Judgment(list(rows), files or {}, {}, "jev", {})


def _row(state: str, p: float = 0.8, check: str = "mixes_concerns") -> Row:
    return Row(f"f1_{check}", check, "src/pfa/features/kvad/internal/engine.py:3 run", p, state)


def test_a_failing_check_blocks_naming_the_function_and_the_fix() -> None:
    v = decide(_judgment(_row(FAIL)), {})

    assert v.outcome == "request_changes" and v.judged == [0]
    assert v.reasons == ["src/pfa/features/kvad/internal/engine.py:3 run does more than one thing p=0.80"]
    assert v.advice[0].startswith("Split it into functions")


def test_an_unsure_check_is_a_note_and_a_doubt_not_a_block() -> None:
    v = decide(_judgment(_row(UNSURE, 0.5)), {})

    assert v.outcome == "comment" and len(v.unsure) == 1 and v.reasons[1] == "1 unsure, listed with the checks below"


def test_a_wall_violation_blocks_as_a_fact() -> None:
    v = decide(_judgment(), {"wall_violations": [{"path": "a.py", "line": 1, "rule": "EIT001", "message": "m"}]})

    assert v.outcome == "request_changes" and v.judged == []


def test_a_file_below_the_clean_code_target_blocks_naming_it() -> None:
    low = {"clean_code": {"type": "score", "score": 1.0}, "clean_code_limit": _choice(duplication=0.7, none=0.3)}

    v = decide(_judgment(files={"src/pfa/api/routes/kvad.py": low}), {})

    assert v.reasons == ["clean code 1.00/3 in src/pfa/api/routes/kvad.py below the target 2.7"] and v.judged == [0]


def test_nothing_failing_is_a_comment_never_an_approval() -> None:
    v = decide(_judgment(_row(PASS, 0.1)), {})

    assert v.outcome == "comment" and v.reasons[0] == "no check failed (1 check on 1 function)"


def test_a_wide_blast_radius_goes_to_a_person() -> None:
    v = decide(_judgment(), {"kernel_changed": True})

    assert v.outcome == "escalate" and v.reasons[0].startswith("wide blast radius")


@pytest.mark.parametrize(
    ("facts", "level"),
    [
        ({"kernel_changed": True}, 2),
        ({"contracts_touched": {"rune": {"fan_in": 12, "frozen": True}}}, 2),
        ({"contracts_touched": {"rune": {"fan_in": 1, "frozen": False}}}, 1),
        ({"shared_changed": True}, 1),
        ({"slices_touched": ["kvad", "rune"]}, 1),
        ({"slices_touched": ["kvad"]}, 0),
    ],
)
def test_blast_radius_is_computed_from_the_facts(facts: dict[str, Any], level: int) -> None:
    assert blast_radius(facts)[0] == level


@pytest.mark.parametrize(("p", "state"), [(0.85, UNSURE), (0.92, FAIL), (0.7, PASS)])
def test_a_style_check_fails_only_when_jev_is_sure(p: float, state: str) -> None:
    (row,) = evaluate({"f1_mixes_concerns": _noul(p)}, [MIXES])

    assert row.state == state


def test_a_fault_check_keeps_the_ordinary_thresholds() -> None:
    edge = Asked("f1_mishandles_edge", "mishandles_edge", RUN)

    assert evaluate({"f1_mishandles_edge": _noul(0.65)}, [edge])[0].state == FAIL
