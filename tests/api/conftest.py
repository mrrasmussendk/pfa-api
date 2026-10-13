from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pfa.api.app import create_app
from pfa.features.chunking.contract import Chunks, CountTokens, SplitText
from pfa.kernel import Bus, Result


@pytest.fixture(scope="session")
def client() -> TestClient:
    return TestClient(create_app())


# ---------------------------------------------------------------- chunking, faked through its contract

FAKE_TOKENIZER = "fake/whitespace"


def _words(text: str) -> int:
    return len(text.split())


def _count_words(q: CountTokens) -> Result[int]:
    return Result.success(_words(q.text))


def _split_words(q: SplitText) -> Result[Chunks]:
    if q.budget < 1:
        return Result.failure("budget must be at least 1 token")
    words = q.text.split()
    if not words:
        return Result.failure("empty text")
    chunks = tuple(" ".join(words[i : i + q.budget]) for i in range(0, len(words), q.budget))
    return Result.success(Chunks(tokenizer=FAKE_TOKENIZER, budget=q.budget, chunks=chunks, token_counts=tuple(_words(c) for c in chunks)))


def fake_chunking(bus: Bus) -> None:
    """Stand in for the chunking feature without loading its tokenizer: a token is a whitespace
    word and a chunk is ``budget`` words. The fake speaks only the contract (``CountTokens``,
    ``SplitText`` -> ``Chunks``), exactly as any consumer sees the feature — the embeddings
    handlers keep reaching chunking through the bus, so the seam is real. How the real feature
    packs sentences is its own concern, tested in ``test_chunking.py``."""
    bus.register(CountTokens, _count_words, replace=True)
    bus.register(SplitText, _split_words, replace=True)


@pytest.fixture
def words_app() -> FastAPI:
    """The real app with chunking replaced by the word-counting fake."""
    app = create_app()
    fake_chunking(app.state.bus)
    return app
