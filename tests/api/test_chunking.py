from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from pfa_api import create_app
from slices.chunking.contract import CountTokens, SplitText
from slices.chunking.internal.chunker import split_text
from slices.chunking.internal.handlers import CountTokensHandler, SplitTextHandler

words = lambda s: len(s.split())  # noqa: E731 — a word is a token


# ---------------------------------------------------------------- the pure algorithm


def test_short_text_is_one_chunk_untouched() -> None:
    assert split_text("  Hello there.  ", 10, words) == ["Hello there."]
    assert split_text("", 10, words) == []


def test_sentences_are_packed_greedily_and_never_split_when_they_fit() -> None:
    text = "One two three. Four five six seven. Eight nine. Ten eleven twelve thirteen."
    assert split_text(text, 4, words) == ["One two three.", "Four five six seven.", "Eight nine.", "Ten eleven twelve thirteen."]
    assert split_text(text, 6, words) == ["One two three.", "Four five six seven. Eight nine.", "Ten eleven twelve thirteen."]
    assert split_text(text, 7, words) == ["One two three. Four five six seven.", "Eight nine. Ten eleven twelve thirteen."]


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


def test_nothing_is_lost_and_every_chunk_fits() -> None:
    text = " ".join(f"w{i}" for i in range(1000)) + ". " + "tail sentence here."
    out = split_text(text, 37, words)
    assert all(words(c) <= 37 for c in out)
    assert " ".join(out).split() == text.split()


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


def test_split_route_defaults_to_the_model_budget_and_validates(client: TestClient) -> None:
    r = client.post("/chunking/split", json={"text": "short"})
    assert r.status_code == 200 and r.json()["budget"] == 500 and r.json()["chunks"] == ["short"]
    assert client.post("/chunking/split", json={"text": "x", "budget": 0}).status_code == 422
    assert client.post("/chunking/split", json={"text": "x", "budget": 513}).status_code == 422
    r = client.post("/chunking/split", json={"text": "   ", "budget": 4})
    assert r.status_code == 422 and r.json()["detail"] == "empty text"


def test_app_startup_does_not_load_the_tokenizer() -> None:
    app = create_app()
    handler = app.state.bus._handlers[SplitText]
    assert handler._tok.tokenizer_id == "intfloat/multilingual-e5-large" and handler._tok.loaded is False
