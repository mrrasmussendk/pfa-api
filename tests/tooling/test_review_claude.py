"""The Claude call behind the PR pipeline's verifier: it is asked with the doubts and the changed files whole, an
answer that is not a ruling is an error, and a workspace ID in the environment rides on every request. The SDK
client is replaced by a fake."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from heimdall.commands.review.claude import BATCH, claude_check, make_client
from heimdall.commands.review.verify import REFUTED, UNDECIDED, Doubt, Inquiry, Ruling, VerifyError

SOURCE = "def split(text, budget):\n    return [text]\n"
INQUIRY = Inquiry("t", (Doubt("a.py:1 split mishandles an edge input p=0.80", "fix it"),), [], {"a.py": SOURCE})


class _FakeClient:
    """Stands in for ``anthropic.Anthropic()``: records each request and answers them with ``replies`` in turn,
    each a ``(text, stop_reason)``; the last reply repeats."""

    def __init__(self, *replies: tuple[str, str]) -> None:
        self.sent: list[dict[str, Any]] = []
        answers = [SimpleNamespace(stop_reason=r, content=[SimpleNamespace(type="text", text=t)]) for t, r in replies]

        def create(**kw: Any) -> Any:
            self.sent.append(kw)
            return answers[min(len(self.sent), len(answers)) - 1]

        self.beta = SimpleNamespace(messages=SimpleNamespace(create=create))


def _refuted(count: int) -> tuple[str, str]:
    """An answer refuting ``count`` doubts."""
    rulings = [{"doubt": i, "verdict": "refuted", "reason": f"r{i}", "findings": []} for i in range(1, count + 1)]
    return json.dumps({"rulings": rulings}), "end_turn"


def _inquiry(count: int) -> Inquiry:
    return Inquiry(
        "t", tuple(Doubt(f"a.py:{i} split mishandles an edge input p=0.80", "fix it") for i in range(count)), [], {"a.py": SOURCE}
    )


def test_claude_is_asked_with_the_doubts_and_the_files_whole() -> None:
    client = _FakeClient(_refuted(1))

    rulings = claude_check(INQUIRY, client)

    assert rulings == [Ruling(REFUTED, "r1")]
    sent = client.sent[0]
    assert sent["model"] == "claude-sonnet-5-5" and sent["fallbacks"] == "default"
    shared, doubts = sent["messages"][0]["content"]
    assert f'<file path="a.py">\n{SOURCE}' in shared["text"] and shared["cache_control"] == {"type": "ephemeral"}
    assert "1. a.py:1 split mishandles an edge input p=0.80" in doubts["text"] and "<file" not in doubts["text"]


def test_many_doubts_are_asked_in_batches_over_one_cached_evidence_block() -> None:
    client = _FakeClient(_refuted(BATCH), _refuted(BATCH), _refuted(3))

    rulings = claude_check(_inquiry(2 * BATCH + 3), client)

    assert len(client.sent) == 3 and len(rulings) == 2 * BATCH + 3
    assert [r.reason for r in rulings[BATCH - 1 : BATCH + 1]] == [f"r{BATCH}", "r1"]
    evidence = {json.dumps(kw["messages"][0]["content"][0], sort_keys=True) for kw in client.sent}
    assert len(evidence) == 1  # byte-identical in every request, so every batch after the first reads it from the cache


def test_a_batch_cut_short_leaves_only_its_own_doubts_undecided() -> None:
    client = _FakeClient(('{"rulings": [', "max_tokens"), _refuted(2))

    rulings = claude_check(_inquiry(BATCH + 2), client)

    assert [r.verdict for r in rulings] == [UNDECIDED] * BATCH + [REFUTED] * 2
    assert "cut at max_tokens" in rulings[0].reason


@pytest.mark.parametrize(("text", "stop_reason"), [("", "refusal"), ('{"rulings": [', "max_tokens"), ("not json", "end_turn")])
def test_an_answer_that_is_not_a_ruling_is_a_verify_error(text: str, stop_reason: str) -> None:
    with pytest.raises(VerifyError):
        claude_check(INQUIRY, _FakeClient((text, stop_reason)))


def test_a_workspace_id_in_the_environment_is_sent_with_every_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_WORKSPACE_ID", "wrkspc_123")

    assert make_client().default_headers["anthropic-workspace-id"] == "wrkspc_123"


def test_without_a_workspace_id_no_workspace_header_is_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.delenv("ANTHROPIC_WORKSPACE_ID", raising=False)

    assert "anthropic-workspace-id" not in make_client().default_headers
