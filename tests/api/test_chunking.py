from __future__ import annotations

import logging
import sys
import types
from typing import Any

import pytest
from fastapi.testclient import TestClient

from pfa.api.app import create_app
from pfa.features.chunking.contract import CountTokens, SplitText
from pfa.features.chunking.internal.chunker import split_text
from pfa.features.chunking.internal.handlers import CountTokensHandler, SplitTextHandler
from pfa.features.chunking.internal.tokenizer import DEFAULT_TOKENIZER_ID, ModelTokenizer

words = lambda s: len(s.split())  # noqa: E731 — a word is a token


# ---------------------------------------------------------------- the pure algorithm


def test_short_text_is_one_chunk_untouched() -> None:
    assert split_text("  Hello there.  ", 10, words) == ["Hello there."]


def test_empty_text_is_no_chunks() -> None:
    assert split_text("", 10, words) == []


SENTENCES = "One two three. Four five six seven. Eight nine. Ten eleven twelve thirteen."


@pytest.mark.parametrize(
    ("budget", "expected"),
    [
        (4, ["One two three.", "Four five six seven.", "Eight nine.", "Ten eleven twelve thirteen."]),
        (6, ["One two three.", "Four five six seven. Eight nine.", "Ten eleven twelve thirteen."]),
        (7, ["One two three. Four five six seven.", "Eight nine. Ten eleven twelve thirteen."]),
    ],
)
def test_whole_sentences_are_packed_greedily_while_they_fit(budget: int, expected: list[str]) -> None:
    assert split_text(SENTENCES, budget, words) == expected


def test_an_over_long_sentence_never_merges_into_a_neighbours_chunk() -> None:
    text = "Short one. a b c d e f g. Short two."
    assert split_text(text, 5, words) == ["Short one.", "a b c d e", "f g.", "Short two."]


def test_paragraph_breaks_also_split() -> None:
    assert split_text("alpha beta\n\ngamma delta", 2, words) == ["alpha beta", "gamma delta"]


def test_a_sentence_over_budget_is_split_by_words() -> None:
    assert split_text("a b c d e f g", 3, words) == ["a b c", "d e f", "g"]


def test_a_single_word_over_budget_is_cut_by_characters() -> None:
    chars = lambda s: len(s)  # noqa: E731 — now a character is a token
    out = split_text("abcdefghij", 4, chars)
    assert "".join(out) == "abcdefghij" and all(len(c) <= 4 for c in out)


LONG_TEXT = " ".join(f"w{i}" for i in range(1000)) + ". " + "tail sentence here."


def test_every_chunk_fits_the_budget() -> None:
    out = split_text(LONG_TEXT, 37, words)
    assert max(words(c) for c in out) <= 37


def test_nothing_is_lost_in_the_split() -> None:
    out = split_text(LONG_TEXT, 37, words)
    assert " ".join(out).split() == LONG_TEXT.split()


def test_budget_must_be_positive() -> None:
    with pytest.raises(ValueError):
        split_text("x", 0, words)


# ---------------------------------------------------------------- the slice, through the bus and the route


class FakeTokenizer:
    tokenizer_id = "fake/whitespace"

    def count(self, text: str) -> int:
        return words(text)


@pytest.fixture
def client() -> TestClient:
    app = create_app()
    tok = FakeTokenizer()
    app.state.bus.register(CountTokens, CountTokensHandler(tok), replace=True)
    app.state.bus.register(SplitText, SplitTextHandler(tok), replace=True)
    return TestClient(app)


def test_split_route_returns_chunks_with_their_token_counts(client: TestClient) -> None:
    r = client.post("/chunking/split", json={"text": "One two three. Four five six seven. Eight nine.", "budget": 4})
    assert r.status_code == 200, r.text
    assert r.json() == {
        "tokenizer": "fake/whitespace",
        "budget": 4,
        "chunks": ["One two three.", "Four five six seven.", "Eight nine."],
        "token_counts": [3, 4, 2],
    }


def test_split_route_defaults_to_the_model_budget(client: TestClient) -> None:
    r = client.post("/chunking/split", json={"text": "short"})
    assert r.status_code == 200 and r.json()["budget"] == 500 and r.json()["chunks"] == ["short"]


@pytest.mark.parametrize("budget", [0, 513])
def test_a_budget_outside_the_window_is_a_validation_problem(client: TestClient, budget: int) -> None:
    r = client.post("/chunking/split", json={"text": "x", "budget": budget})
    assert r.status_code == 422 and r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["type"] == "urn:pfa-api:problem:validation-error"


def test_blank_text_is_a_domain_rejection(client: TestClient) -> None:
    r = client.post("/chunking/split", json={"text": "   ", "budget": 4})
    assert r.status_code == 422 and r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["type"] == "urn:pfa-api:problem:domain-rejection" and r.json()["detail"] == "empty text"


def test_app_startup_does_not_load_the_tokenizer() -> None:
    app = create_app()
    assert app.state.readiness()["tokenizer"] is False


# ---------------------------------------------------------------- the model tokenizer and its setting


class _Encoding:
    def encode(self, text: str, add_special_tokens: bool = False) -> list[str]:
        return text.split()


class _StandInTokenizer(ModelTokenizer):
    """The real ``ModelTokenizer`` with the download replaced: everything but the weights is real."""

    def build_tokenizer(self) -> Any:
        return _Encoding()


def test_the_tokenizer_defaults_to_the_e5_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PFA_EMBED_MODEL", raising=False)
    assert ModelTokenizer().tokenizer_id == DEFAULT_TOKENIZER_ID == "intfloat/multilingual-e5-large"


def test_the_constructor_overrides_the_tokenizer_setting() -> None:
    assert ModelTokenizer("some/other-model").tokenizer_id == "some/other-model"


def test_the_tokenizer_load_is_logged_with_its_duration(caplog: pytest.LogCaptureFixture) -> None:
    tok = _StandInTokenizer("fake/whitespace")
    with caplog.at_level(logging.INFO):
        tok.warmup()
    (rec,) = [r for r in caplog.records if getattr(r, "msgid", None) == "load"]
    assert rec.tokenizer_id == "fake/whitespace" and rec.load_ms >= 0  # type: ignore[attr-defined]


def test_a_loaded_tokenizer_counts_without_special_tokens() -> None:
    assert _StandInTokenizer("fake/whitespace").count("one two three") == 3


class _AutoTokenizer:
    """Stands in for ``transformers.AutoTokenizer``: what it returns says what was asked for."""

    @staticmethod
    def from_pretrained(name: str) -> str:
        return f"tokenizer of {name}"


def test_the_tokenizer_is_fetched_by_its_configured_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "transformers", types.SimpleNamespace(AutoTokenizer=_AutoTokenizer))
    assert ModelTokenizer("some/model").build_tokenizer() == "tokenizer of some/model"


class _UnreachableHub:
    """Stands in for ``transformers.AutoTokenizer`` when the hub cannot be reached."""

    @staticmethod
    def from_pretrained(name: str) -> str:
        raise OSError("connection refused")


def test_a_tokenizer_that_cannot_be_fetched_fails_naming_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "transformers", types.SimpleNamespace(AutoTokenizer=_UnreachableHub))
    with pytest.raises(RuntimeError, match="'some/model'"):
        ModelTokenizer("some/model").build_tokenizer()
