"""Claude answers the review's questions: its answers are read into the shapes the policy reads (a probability, an
expected level, a label), each keeping its reason and its findings, and the request carries the task, the facts,
the questions and the changed files whole. The SDK client is replaced by a fake that streams a canned answer."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from heimdall.commands.review import file_questions, questions
from heimdall.commands.review.claude import ReviewInquiry, claude_review, make_client, prompt, read_answers
from heimdall.commands.review.verify import VerifyError

FINDING = {"file": "a.py", "function": "f", "line": 1, "evidence": "x = 1", "problem": "p", "fix": "fx"}


def _item(key: str, answer: str, confidence: float = 0.8, findings: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"key": key, "answer": answer, "confidence": confidence, "reason": f"why {key}", "findings": findings or []}


def test_a_yes_no_answer_becomes_the_probability_of_yes() -> None:
    answers = read_answers([_item("single_purpose", "no", 0.8, [FINDING]), _item("stays_in_scope", "yes", 0.9)], questions())

    assert answers["single_purpose"] == {"type": "noul", "noul": pytest.approx(0.2), "reason": "why single_purpose", "findings": [FINDING]}
    assert answers["stays_in_scope"]["noul"] == pytest.approx(0.9)


def test_a_score_answer_becomes_its_level_with_the_legend() -> None:
    answers = read_answers([_item("correctness", "Probably correct", 0.7)], questions())

    a = answers["correctness"]
    assert a["type"] == "score" and a["score"] == 2.0 and a["confidence"] == 0.7
    assert a["legend"]["2"] == "Probably correct" and a["probabilities"]["2"] == 0.7


def test_a_choice_answer_becomes_the_label_with_its_confidence() -> None:
    a = read_answers([_item("biggest_risk", "tests", 0.6)], questions())["biggest_risk"]

    assert a["type"] == "choice" and a["choice"] == "tests" and a["probabilities"] == {"tests": 0.6}


@pytest.mark.parametrize("item", [_item("no_such_question", "yes"), _item("correctness", "Brilliant"), _item("biggest_risk", "luck")])
def test_an_answer_outside_the_questions_is_left_out(item: dict[str, Any]) -> None:
    assert read_answers([item], questions()) == {}


def _inquiry() -> ReviewInquiry:
    return ReviewInquiry(
        task="add a route",
        facts={"files_changed": 1},
        architecture={"rules": ["walls"]},
        questions=questions(),
        file_questions=file_questions(),
        craft_paths=["a.py"],
        diff=[{"path": "a.py", "status": "modified", "added": 1, "removed": 0, "diff": "+x = 1"}],
        sources={"a.py": "x = 1\n"},
    )


def test_the_prompt_carries_the_task_the_questions_and_the_files_whole() -> None:
    text = prompt(_inquiry())

    assert "<task>\nadd a route\n</task>" in text and '<file path="a.py">\nx = 1\n' in text
    assert "single_purpose" in text and "Probably correct" in text and "clean_code" in text
    assert '"files_changed": 1' in text


class _FakeStream:
    def __init__(self, message: Any) -> None:
        self.message = message

    def __enter__(self) -> _FakeStream:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def get_final_message(self) -> Any:
        return self.message


class _FakeClient:
    """Stands in for ``anthropic.Anthropic()``: records the request and streams back ``text``."""

    def __init__(self, text: str, stop_reason: str = "end_turn") -> None:
        self.sent: dict[str, Any] = {}
        usage = SimpleNamespace(input_tokens=1200, output_tokens=300)
        reply = SimpleNamespace(
            stop_reason=stop_reason, model="claude-sonnet-5-5", usage=usage, content=[SimpleNamespace(type="text", text=text)]
        )
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=lambda **kw: self.sent.update(kw) or _FakeStream(reply)))


def test_claude_reviews_the_diff_and_each_file_whole() -> None:
    body = {
        "answers": [_item("correctness", "Clearly correct", 0.9)],
        "files": [{"path": "a.py", "answers": [_item("clean_code", "Clean", 0.8), _item("clean_code_limit", "comments", 0.6)]}],
    }
    client = _FakeClient(json.dumps(body))

    review = claude_review(_inquiry(), client)

    assert review.answers["correctness"]["score"] == 3.0 and review.files["a.py"]["clean_code"]["score"] == 2.0
    assert review.usage == {"input_tokens": 1200, "output_tokens": 300} and review.model == "claude-sonnet-5-5"
    assert client.sent["model"] == "claude-sonnet-5-5" and client.sent["fallbacks"] == "default"
    assert client.sent["output_config"]["effort"] == "high"


@pytest.mark.parametrize(("text", "stop_reason"), [("", "refusal"), ('{"answers": [', "max_tokens"), ("not json", "end_turn")])
def test_an_answer_that_is_not_a_review_is_a_verify_error(text: str, stop_reason: str) -> None:
    with pytest.raises(VerifyError):
        claude_review(_inquiry(), _FakeClient(text, stop_reason))


def test_a_workspace_id_in_the_environment_is_sent_with_every_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_WORKSPACE_ID", "wrkspc_123")

    assert make_client().default_headers["anthropic-workspace-id"] == "wrkspc_123"


def test_without_a_workspace_id_no_workspace_header_is_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.delenv("ANTHROPIC_WORKSPACE_ID", raising=False)

    assert "anthropic-workspace-id" not in make_client().default_headers
