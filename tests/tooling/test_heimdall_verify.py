"""Claude verifies Jev's doubts: a block that comes from Jev's answers stands only when Claude confirms it with
evidence found in the file; a refuted doubt falls away, an undecided one goes to a person. Facts (Eitri) are
never put to the verifier. Claude is replaced by a fake that rules as told."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from heimdall.commands.review import build_state, decide, questions, render_markdown, render_table
from heimdall.commands.review.claude import claude_check
from heimdall.commands.review.verify import (
    CONFIRMED,
    REFUTED,
    UNDECIDED,
    Doubt,
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
GOOD = {
    "safe_to_merge": {"type": "noul", "noul": 0.9},
    "needs_human_review": {"type": "noul", "noul": 0.1},
    "has_security_concern": {"type": "noul", "noul": 0.05},
    "correctness": {"type": "score", "score": 2.8},
}


def _jev(facts: dict[str, Any] | None = None, **over: Any) -> Any:
    """Jev's verdict: the good answers with ``over`` replacing some."""
    return decide({**GOOD, **over}, facts or {}, "jev", {})


def _bad_correctness() -> Any:
    return _jev(correctness={"type": "score", "score": 1.0})


def _rules(*rulings: Ruling):
    seen: list[Inquiry] = []

    def check(inquiry: Inquiry) -> list[Ruling]:
        seen.append(inquiry)
        return list(rulings)

    return check, seen


def _finding(evidence: str = "if budget < 1:") -> Finding:
    return Finding(file="a.py", function="split", line=0, evidence=evidence, problem="a budget of 0 returns nothing", fix="raise instead")


def test_a_judgment_block_goes_to_the_verifier_with_its_advice() -> None:
    check, seen = _rules(Ruling(REFUTED, "the boundary is tested"))

    settle(_bad_correctness(), check, EVIDENCE)

    assert len(seen) == 1 and seen[0].doubts[0].reason.startswith("correctness expected level")
    assert seen[0].sources == {"a.py": SOURCE}


def test_a_confirmed_doubt_blocks_naming_the_file_function_and_fix() -> None:
    check, _ = _rules(Ruling(CONFIRMED, "the zero budget", (_finding(),)))

    v = settle(_bad_correctness(), check, EVIDENCE)

    assert v.outcome == "request_changes" and v.reasons[0].startswith("confirmed by Claude: correctness")
    assert "a.py:2 split: a budget of 0 returns nothing. Fix: raise instead" in v.advice[0]


def test_a_confirmation_whose_evidence_is_not_in_the_file_goes_to_a_person() -> None:
    check, _ = _rules(Ruling(CONFIRMED, "made up", (_finding("if budget <= 0:"),)))

    v = settle(_bad_correctness(), check, EVIDENCE)

    assert v.outcome == "escalate" and v.verification[0]["verdict"] == UNDECIDED


def test_a_refuted_doubt_leaves_the_verdict_jev_would_give_without_it() -> None:
    check, _ = _rules(Ruling(REFUTED, "the boundary is tested"))

    v = settle(_bad_correctness(), check, EVIDENCE)

    assert v.outcome == "approve" and v.verification[0] == {
        "doubt": v.verification[0]["doubt"],
        "verdict": REFUTED,
        "reason": "the boundary is tested",
        "findings": [],
    }


def test_an_undecided_doubt_goes_to_a_person() -> None:
    check, _ = _rules(Ruling(UNDECIDED, "the caller is not in the diff"))

    v = settle(_bad_correctness(), check, EVIDENCE)

    assert v.outcome == "escalate" and "the caller is not in the diff" in v.advice[0]


def test_without_a_verifier_a_judgment_block_goes_to_a_person() -> None:
    v = settle(_bad_correctness(), None, EVIDENCE)

    assert v.outcome == "escalate" and v.reasons[0].startswith("unverified: correctness")


def test_a_verifier_that_fails_sends_the_doubt_to_a_person() -> None:
    def broken(_: Inquiry) -> list[Ruling]:
        raise VerifyError("Claude did not answer (APIConnectionError)")

    v = settle(_bad_correctness(), broken, EVIDENCE)

    assert v.outcome == "escalate" and "APIConnectionError" in v.advice[0]


def test_a_fact_block_stands_and_is_never_put_to_the_verifier() -> None:
    facts = {"wall_violations": [{"path": "a.py", "line": 1, "rule": "EIT001", "message": "m"}]}
    jev = _jev(facts, correctness={"type": "score", "score": 1.0})
    check, seen = _rules(Ruling(REFUTED, "fine"))

    v = settle(jev, check, EVIDENCE)

    assert v.outcome == "request_changes" and v.reasons == ["Eitri found 1 wall violation(s) in the changed files"]
    assert [d.reason for d in seen[0].doubts] == [jev.reasons[1]]


def test_a_verdict_without_judgment_blocks_is_left_alone() -> None:
    check, seen = _rules()

    assert settle(_jev(), check, EVIDENCE).outcome == "approve" and seen == []


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


# ---------------------------------------------------------------- the Claude call, with a fake client


class _FakeClient:
    """Stands in for ``anthropic.Anthropic()``: records the request and answers with ``text``."""

    def __init__(self, text: str, stop_reason: str = "end_turn") -> None:
        self.sent: dict[str, Any] = {}
        reply = SimpleNamespace(stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=text)])
        create = lambda **kw: self.sent.update(kw) or reply  # noqa: E731
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=create))


INQUIRY = Inquiry("t", (Doubt("correctness expected level 1.00/3", "walk the branches"),), [], {"a.py": SOURCE})


def test_claude_is_asked_with_the_doubts_and_the_files_whole() -> None:
    client = _FakeClient('{"rulings": [{"doubt": 1, "verdict": "refuted", "reason": "r", "findings": []}]}')

    rulings = claude_check(INQUIRY, client)

    assert rulings == [Ruling(REFUTED, "r")]
    assert client.sent["model"] == "claude-sonnet-5-5" and client.sent["fallbacks"] == "default"
    user = client.sent["messages"][0]["content"]
    assert "1. correctness expected level 1.00/3" in user and f'<file path="a.py">\n{SOURCE}' in user


@pytest.mark.parametrize(("text", "stop_reason"), [("", "refusal"), ('{"rulings": [', "max_tokens"), ("not json", "end_turn")])
def test_an_answer_that_is_not_a_ruling_is_a_verify_error(text: str, stop_reason: str) -> None:
    with pytest.raises(VerifyError):
        claude_check(INQUIRY, _FakeClient(text, stop_reason))


def test_the_report_says_how_claude_ruled_on_each_doubt() -> None:
    check, _ = _rules(Ruling(REFUTED, "the boundary is tested"))
    v = settle(_bad_correctness(), check, EVIDENCE)

    table, md = render_table(v, questions()), render_markdown(v, build_state([], None, None), questions())

    line = "Claude refuted: correctness expected level 1.00/3 — the boundary is tested"
    assert line in table and f"- {line}" in md
