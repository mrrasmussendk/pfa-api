"""The PR pipeline: Claude verifies each check Jev failed or was unsure about. A failing check stands only when
Claude confirms it with evidence found in the file; an unsure one blocks only when confirmed. A refuted doubt
falls away, an undecided failing one goes to a person. Facts (Eitri) are never put to the verifier."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from heimdall.commands.review import build_state, render_comment, render_table
from heimdall.commands.review.policy import FAIL, PASS, UNSURE, Judgment, Row, decide
from heimdall.commands.review.verify import (
    CONFIRMED,
    REFUTED,
    UNDECIDED,
    Evidence,
    Finding,
    Inquiry,
    Ruling,
    VerifyError,
    grounded,
    parse_rulings,
    read_sources,
    settle,
)

SOURCE = "def split(text, budget):\n    if budget < 1:\n        return []\n    return [text]\n"
EVIDENCE = Evidence(task="t", diff=[], sources={"a.py": SOURCE})
FAILING = Row("f1_mishandles_edge", "mishandles_edge", "a.py:1 split", 0.8, FAIL)
DOUBTFUL = Row("f1_swallows_error", "swallows_error", "a.py:1 split", 0.5, UNSURE)


def _verdict(*rows: Row, facts: dict[str, Any] | None = None) -> Any:
    return decide(Judgment(list(rows), {}, {}, "jev", {}), facts or {})


def _rules(*rulings: Ruling):
    seen: list[Inquiry] = []

    def check(inquiry: Inquiry) -> list[Ruling]:
        seen.append(inquiry)
        return list(rulings)

    return check, seen


def _finding(evidence: str = "if budget < 1:") -> Finding:
    return Finding(file="a.py", function="split", line=0, evidence=evidence, problem="a budget of 0 returns nothing", fix="raise instead")


def test_a_failing_check_goes_to_the_verifier_with_the_files() -> None:
    check, seen = _rules(Ruling(REFUTED, "the boundary is tested"))

    settle(_verdict(FAILING), check, EVIDENCE)

    assert seen[0].doubts[0].reason == "a.py:1 split mishandles an edge input p=0.80" and seen[0].sources == {"a.py": SOURCE}


def test_a_confirmed_check_blocks_naming_the_line_and_the_fix() -> None:
    check, _ = _rules(Ruling(CONFIRMED, "the zero budget", (_finding(),)))

    v = settle(_verdict(FAILING), check, EVIDENCE)

    assert v.outcome == "request_changes" and "a.py:2 split: a budget of 0 returns nothing. Fix: raise instead." in v.advice[0]


def test_a_confirmation_whose_evidence_is_not_in_the_file_goes_to_a_person() -> None:
    check, _ = _rules(Ruling(CONFIRMED, "made up", (_finding("if budget <= 0:"),)))

    v = settle(_verdict(FAILING), check, EVIDENCE)

    assert v.outcome == "escalate" and v.verification[0]["verdict"] == UNDECIDED


def test_a_refuted_check_leaves_the_outcome_without_it() -> None:
    check, _ = _rules(Ruling(REFUTED, "the boundary is tested"))

    v = settle(_verdict(FAILING), check, EVIDENCE)

    assert v.outcome == "comment" and v.verification[0]["reason"] == "the boundary is tested"


@pytest.mark.parametrize("ruling", [Ruling(UNDECIDED, "the caller is not in the diff"), None])
def test_an_undecided_or_unverified_failing_check_goes_to_a_person(ruling: Ruling | None) -> None:
    check = _rules(ruling)[0] if ruling else None

    v = settle(_verdict(FAILING), check, EVIDENCE)

    assert v.outcome == "escalate" and v.reasons[0].startswith("unverified: a.py:1 split")


def test_a_verifier_that_fails_sends_the_check_to_a_person() -> None:
    def broken(_: Inquiry) -> list[Ruling]:
        raise VerifyError("Claude did not answer (APIConnectionError)")

    v = settle(_verdict(FAILING), broken, EVIDENCE)

    assert v.outcome == "escalate" and "APIConnectionError" in v.advice[0]


def test_an_unsure_check_claude_confirms_blocks() -> None:
    check, seen = _rules(Ruling(CONFIRMED, "it swallows", (_finding(),)))

    v = settle(_verdict(DOUBTFUL), check, EVIDENCE)

    assert [d.reason for d in seen[0].doubts] == ["a.py:1 split may swallow an error p=0.50"]
    assert v.outcome == "request_changes" and v.reasons == ["a.py:1 split may swallow an error p=0.50"]


@pytest.mark.parametrize("ruling", [Ruling(REFUTED, "it re-raises"), Ruling(UNDECIDED, "cannot tell")])
def test_an_unsure_check_not_confirmed_is_dropped(ruling: Ruling) -> None:
    check, _ = _rules(ruling)

    assert settle(_verdict(DOUBTFUL), check, EVIDENCE).outcome == "comment"


def test_a_fact_block_stands_and_is_never_put_to_the_verifier() -> None:
    facts = {"wall_violations": [{"path": "a.py", "line": 1, "rule": "EIT001", "message": "m"}]}
    check, seen = _rules(Ruling(REFUTED, "fine"))

    v = settle(_verdict(FAILING, facts=facts), check, EVIDENCE)

    assert v.outcome == "request_changes" and v.reasons == ["Eitri found 1 wall violation(s) in the changed files"]
    assert [d.reason for d in seen[0].doubts] == ["a.py:1 split mishandles an edge input p=0.80"]


def test_a_verdict_with_nothing_to_verify_is_left_alone() -> None:
    check, seen = _rules()

    assert settle(_verdict(Row("k", "untested", "a.py:1 split", 0.1, PASS)), check, EVIDENCE).outcome == "comment" and seen == []


def test_evidence_is_found_by_its_lines_and_located() -> None:
    f = grounded(_finding("    return []\n    return [text]"), {"a.py": SOURCE})

    assert f is not None and f.line == 3


@pytest.mark.parametrize("evidence", ["", "   ", "return None"])
def test_evidence_that_is_empty_or_absent_is_not_grounded(evidence: str) -> None:
    assert grounded(_finding(evidence), {"a.py": SOURCE}) is None


def test_rulings_are_read_in_the_order_of_the_doubts() -> None:
    raw = {"rulings": [{"doubt": 1, "verdict": "refuted", "reason": "r", "findings": []}]}

    assert parse_rulings(raw, 1) == [Ruling(REFUTED, "r")]


@pytest.mark.parametrize(
    "raw",
    [{"rulings": []}, {"rulings": [{"doubt": 1, "verdict": "maybe", "reason": "r", "findings": []}]}, {"nope": 1}],
)
def test_rulings_of_the_wrong_shape_are_an_error(raw: dict[str, Any]) -> None:
    with pytest.raises(VerifyError):
        parse_rulings(raw, 1)


def test_sources_are_read_whole_in_order_within_the_budget(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("y = 2\n" * 400, encoding="utf-8")

    sources = read_sources(str(tmp_path), ["a.py", "gone.py", "b.py"], budget=50)

    assert sources == {"a.py": "x = 1\n"}


def test_the_report_says_how_claude_ruled_on_each_check() -> None:
    check, _ = _rules(Ruling(REFUTED, "the boundary is tested"))
    v = settle(_verdict(FAILING), check, EVIDENCE)

    table, md = render_table(v, {}), render_comment(v, build_state([], None, None))

    assert "Claude refuted: a.py:1 split mishandles an edge input p=0.80 — the boundary is tested" in table
    assert "1 check Claude cleared" in md and "| `a.py:1` · `split` | may mishandle an edge input | the boundary is tested |" in md
