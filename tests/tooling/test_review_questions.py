"""The questions: narrow fault checks built in code, one set per changed function and per route, plus a few
about the change as a whole. Every one is a valid System One question, and "yes" (or a named file) is a fault."""

from __future__ import annotations

import pytest

from heimdall.commands.review.items import Item
from heimdall.commands.review.questions import FUNCTION_CHECKS, ROUTE_CHECKS, ask, file_questions

ENGINE = Item("f1", "src/pfa/features/kvad/internal/engine.py", 3, "run", "def run(): ...", route=False)
ROUTE = Item("f2", "src/pfa/api/routes/kvad.py", 9, "add_item", "def add_item(): ...", route=True)


def test_each_function_gets_every_function_check_named_by_its_id() -> None:
    qs, asked = ask([ENGINE], ["src/pfa/features/kvad/internal/engine.py"])

    keys = [a.key for a in asked if a.item is ENGINE]
    assert keys == [f"f1_{check}" for check in FUNCTION_CHECKS]
    assert all("`functions.f1" in str(qs[k]["instructions"]) for k in keys)


def test_a_route_also_gets_the_route_checks_and_other_functions_do_not() -> None:
    _, asked = ask([ENGINE, ROUTE], [])

    assert {a.check for a in asked if a.item is ROUTE} == set(FUNCTION_CHECKS) | set(ROUTE_CHECKS)
    assert not {a.check for a in asked if a.item is ENGINE} & set(ROUTE_CHECKS)


def test_the_change_is_asked_which_file_is_out_of_scope_or_misplaced() -> None:
    qs, asked = ask([], ["a.py", "b.md"])

    assert qs["out_of_scope"]["type"] == "choice" and list(qs["out_of_scope"]["criteria"]) == ["none", "a.py", "b.md"]
    assert qs["misplaced"]["type"] == "choice" and qs["docs_stale"]["type"] == "noul"
    assert {a.key for a in asked if a.item is None} == {"out_of_scope", "misplaced", "docs_stale"}


def test_a_choice_never_offers_more_than_system_one_takes() -> None:
    qs, _ = ask([], [f"f{i}.py" for i in range(400)])

    assert len(qs["out_of_scope"]["criteria"]) == 255


@pytest.mark.parametrize("build", [lambda: ask([ENGINE, ROUTE], ["a.py"])[0], file_questions])
def test_every_question_is_a_valid_system_one_question(build) -> None:
    for key, q in build().items():
        assert q["type"] in ("noul", "choice", "score"), key
        assert isinstance(q["instructions"], str) and q["instructions"], key
        if q["type"] == "choice":
            assert 2 <= len(q["criteria"]) <= 255, key
        if q["type"] == "score":
            assert 2 <= len(q["criteria"]) <= 10, key
