"""The Claude call behind the PR pipeline's verifier: it is asked with the doubts and the changed files whole, an
answer that is not a ruling is an error, and a workspace ID in the environment rides on every request. The SDK
client is replaced by a fake."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from heimdall.commands.review.claude import claude_check, make_client
from heimdall.commands.review.verify import REFUTED, Doubt, Inquiry, Ruling, VerifyError

SOURCE = "def split(text, budget):\n    return [text]\n"
INQUIRY = Inquiry("t", (Doubt("a.py:1 split mishandles an edge input p=0.80", "fix it"),), [], {"a.py": SOURCE})


class _FakeClient:
    """Stands in for ``anthropic.Anthropic()``: records the request and answers with ``text``."""

    def __init__(self, text: str, stop_reason: str = "end_turn") -> None:
        self.sent: dict[str, Any] = {}
        reply = SimpleNamespace(stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=text)])
        create = lambda **kw: self.sent.update(kw) or reply  # noqa: E731
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=create))


def test_claude_is_asked_with_the_doubts_and_the_files_whole() -> None:
    client = _FakeClient('{"rulings": [{"doubt": 1, "verdict": "refuted", "reason": "r", "findings": []}]}')

    rulings = claude_check(INQUIRY, client)

    assert rulings == [Ruling(REFUTED, "r")]
    assert client.sent["model"] == "claude-sonnet-5-5" and client.sent["fallbacks"] == "default"
    user = client.sent["messages"][0]["content"]
    assert "1. a.py:1 split mishandles an edge input p=0.80" in user and f'<file path="a.py">\n{SOURCE}' in user


@pytest.mark.parametrize(("text", "stop_reason"), [("", "refusal"), ('{"rulings": [', "max_tokens"), ("not json", "end_turn")])
def test_an_answer_that_is_not_a_ruling_is_a_verify_error(text: str, stop_reason: str) -> None:
    with pytest.raises(VerifyError):
        claude_check(INQUIRY, _FakeClient(text, stop_reason))


def test_a_workspace_id_in_the_environment_is_sent_with_every_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_WORKSPACE_ID", "wrkspc_123")

    assert make_client().default_headers["anthropic-workspace-id"] == "wrkspc_123"


def test_without_a_workspace_id_no_workspace_header_is_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.delenv("ANTHROPIC_WORKSPACE_ID", raising=False)

    assert "anthropic-workspace-id" not in make_client().default_headers
