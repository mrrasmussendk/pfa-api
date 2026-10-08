from __future__ import annotations

import math
import os
import threading
import time
from collections.abc import Sequence

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pfa.api.app import create_app
from pfa.features.embeddings.contract import EmbedPassages, EmbedQuery, EngineBusy, RankCandidates
from pfa.features.embeddings.internal.e5_engine import E5Engine
from pfa.features.embeddings.internal.handlers import EmbedPassagesHandler, EmbedQueryHandler, RankCandidatesHandler


class FakeEngine:
    """Stands in for the model so the handlers and routes are tested without 2 GB of weights.
    A vector is the bag of the first 8 letters of each word, so texts sharing words land
    close together — enough to test chunking and ranking."""

    model_id = "fake/e5"

    def __init__(self, max_tokens: int = 500) -> None:
        self.max_tokens = max_tokens
        self.encoded: list[str] = []

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * 26
        for w in text.lower().split():
            for ch in w[:8]:
                if ch.isalpha() and ch.isascii():
                    v[ord(ch) - 97] += 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]

    def encode_queries(self, texts: Sequence[str]) -> list[list[float]]:
        self.encoded.extend("query: " + t for t in texts)
        return [self._vec(t) for t in texts]

    def encode_passages(self, texts: Sequence[str]) -> list[list[float]]:
        self.encoded.extend("passage: " + t for t in texts)
        return [self._vec(t) for t in texts]


def _fake_app(app: FastAPI, max_tokens: int = 500) -> tuple[TestClient, FakeEngine]:
    """The real embeddings handlers over a fake engine, on an app whose chunking is the
    word-counting fake from ``conftest`` (a token is a word). The handlers still reach chunking
    through the bus — the seam is real; only chunking's contract is in play."""
    bus = app.state.bus
    engine = FakeEngine(max_tokens)
    bus.register(EmbedQuery, EmbedQueryHandler(engine, bus), replace=True)
    bus.register(EmbedPassages, EmbedPassagesHandler(engine, bus), replace=True)
    bus.register(RankCandidates, RankCandidatesHandler(engine, bus), replace=True)
    return TestClient(app), engine


@pytest.fixture
def fake(words_app: FastAPI) -> tuple[TestClient, FakeEngine]:
    return _fake_app(words_app)


def test_question_is_embedded_as_a_query_vector(fake) -> None:
    client, engine = fake
    r = client.post("/embeddings/query", json={"question": "Hvad er en vertikal slice?"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["model"] == "fake/e5" and body["dimensions"] == 26 == len(body["embedding"])
    assert engine.encoded == ["query: Hvad er en vertikal slice?"]  # the query prefix, exactly once


def test_blank_question_is_a_domain_rejection(fake) -> None:
    client, _ = fake
    r = client.post("/embeddings/query", json={"question": "   "})
    assert r.status_code == 422 and r.json()["detail"] == "empty question"


def test_body_is_validated(fake) -> None:
    client, _ = fake
    assert client.post("/embeddings/query", json={}).status_code == 422
    assert client.post("/embeddings/query", json={"question": ""}).status_code == 422
    assert client.post("/embeddings/similarity", json={"question": "x", "candidates": []}).status_code == 422


def test_passages_get_the_passage_prefix_and_keep_their_order(fake) -> None:
    client, engine = fake
    r = client.post("/embeddings/passages", json={"texts": ["first doc", "second doc"]})
    assert r.status_code == 200, r.text
    chunks = r.json()["chunks"]
    assert [(c["source_index"], c["chunk_index"], c["chunk_count"]) for c in chunks] == [(0, 0, 1), (1, 0, 1)]
    assert engine.encoded == ["passage: first doc", "passage: second doc"]


def test_long_passage_is_chunked_through_the_chunking_feature_within_the_budget(words_app: FastAPI) -> None:
    client, _ = _fake_app(words_app, max_tokens=13)  # 13 - "passage:"(1 word) - 2 specials = 10 words per chunk
    text = "One two three four five six. Seven eight nine ten eleven. Twelve thirteen. Fourteen fifteen sixteen seventeen eighteen nineteen twenty twentyone twentytwo twentythree twentyfour."
    r = client.post("/embeddings/passages", json={"texts": [text, "short"]})
    assert r.status_code == 200, r.text
    chunks = r.json()["chunks"]
    long_chunks = [c for c in chunks if c["source_index"] == 0]
    assert [c["chunk_index"] for c in long_chunks] == list(range(len(long_chunks)))
    assert all(c["chunk_count"] == len(long_chunks) for c in long_chunks)
    assert [len(c["text"].split()) for c in long_chunks] == [10, 10, 4]  # 24 words at the budget the handler computed
    assert " ".join(c["text"] for c in long_chunks) == text  # nothing lost
    last = chunks[-1]
    assert (last["source_index"], last["chunk_index"], last["chunk_count"], last["text"]) == (1, 0, 1, "short")


def test_similarity_ranks_candidates_best_first_and_reports_the_best_chunk(fake) -> None:
    client, _ = fake
    r = client.post(
        "/embeddings/similarity",
        json={"question": "reset my password", "candidates": ["office hours today", "how to reset a password", "the weather report"]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["model"] == "fake/e5"
    assert body["matches"][0]["text"] == "how to reset a password"
    scores = [m["score"] for m in body["matches"]]
    assert scores == sorted(scores, reverse=True)
    assert body["matches"][0]["source_index"] == 1 and body["matches"][0]["chunk_count"] == 1


def test_similarity_scores_a_long_candidate_by_its_best_chunk(words_app: FastAPI) -> None:
    client, _ = _fake_app(words_app, max_tokens=8)  # 5 words per chunk
    filler = "lorem ipsum dolor sit amet. " * 4
    needle = "reset the password now."
    r = client.post("/embeddings/similarity", json={"question": "reset my password", "candidates": [filler + needle + " " + filler, "zzz"]})
    assert r.status_code == 200, r.text
    top = r.json()["matches"][0]
    assert top["source_index"] == 0
    assert "password" in top["text"] and top["chunk_count"] > 1 and 0 < top["chunk_index"] < top["chunk_count"] - 1


def test_similarity_rejects_blank_question(fake) -> None:
    client, _ = fake
    r = client.post("/embeddings/similarity", json={"question": " ", "candidates": ["x"]})
    assert r.status_code == 422 and r.json()["detail"] == "empty question"


def test_app_startup_does_not_load_the_model() -> None:
    app = create_app()
    handler = app.state.bus._handlers[EmbedQuery]
    assert handler._engine.model_id == "intfloat/multilingual-e5-large"
    assert handler._engine.loaded is False


def test_bus_refuses_two_handlers_for_one_query() -> None:
    app = create_app()
    with pytest.raises(ValueError):
        app.state.bus.register(EmbedQuery, lambda q: None)


# ---------------------------------------------------------------- real model (opt-in)

real = pytest.mark.skipif(os.environ.get("PFA_RUN_MODEL_TESTS") != "1", reason="set PFA_RUN_MODEL_TESTS=1 to run the real model")


@real
def test_real_model_returns_a_unit_vector_of_1024_dims() -> None:
    client = TestClient(create_app())
    r = client.post("/embeddings/query", json={"question": "Hvor mange sprog taler modellen?"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["model"] == "intfloat/multilingual-e5-large"
    assert body["dimensions"] == 1024 == len(body["embedding"])
    assert math.isclose(math.sqrt(sum(x * x for x in body["embedding"])), 1.0, abs_tol=1e-3)


@real
def test_real_model_ranks_the_right_answer_first_across_languages() -> None:
    client = TestClient(create_app())
    r = client.post(
        "/embeddings/similarity",
        json={
            "question": "Hvordan nulstiller jeg min adgangskode?",
            "candidates": [
                "Our office is closed on public holidays",
                "Opskrift på rugbrød med kerner",
                "To reset your password, open Settings and choose Security",
            ],
        },
    )
    assert r.status_code == 200, r.text
    matches = r.json()["matches"]
    assert matches[0]["text"].startswith("To reset your password")
    assert matches[0]["score"] > matches[-1]["score"] + 0.05  # a clear margin, not a coin flip


@real
def test_real_model_chunks_a_long_document_with_the_real_tokenizer_and_finds_the_needle() -> None:
    client = TestClient(create_app())
    filler = "The committee reviewed the quarterly logistics figures and noted minor variances in regional delivery times. " * 60
    needle = "To reset your password, open Settings and choose Security, then follow the e-mail link."
    doc = filler + needle + " " + filler
    split = client.post("/chunking/split", json={"text": doc, "budget": 497}).json()
    assert len(split["chunks"]) > 2 and max(split["token_counts"]) <= 497
    r = client.post("/embeddings/passages", json={"texts": [doc]})
    assert r.status_code == 200, r.text
    chunks = r.json()["chunks"]
    assert len(chunks) == len(split["chunks"]) and all(c["chunk_count"] == len(chunks) for c in chunks)
    r = client.post(
        "/embeddings/similarity", json={"question": "Hvordan nulstiller jeg min adgangskode?", "candidates": [doc, "Opening hours"]}
    )
    top = r.json()["matches"][0]
    assert top["source_index"] == 0 and "password" in top["text"] and top["chunk_count"] == len(chunks)


# ---------------------------------------------------------------- the inference gate


class _SlowModel:
    """Stands in for SentenceTransformer: records concurrency, sleeps on command."""

    def __init__(self) -> None:
        self.active = 0
        self.peak = 0
        self.release = threading.Event()
        self.lock = threading.Lock()

    def encode(self, texts, normalize_embeddings=True, convert_to_numpy=True):
        import numpy as np

        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            self.release.wait(2)
            return np.ones((len(texts), 4), dtype="float32")
        finally:
            with self.lock:
                self.active -= 1


def _gated_engine(model: _SlowModel, concurrency: int = 1, timeout: float = 0.2) -> E5Engine:
    engine = E5Engine(model_id="fake", device="cpu", max_tokens=500, concurrency=concurrency, queue_timeout=timeout)
    engine._model = model  # loaded already: the gate, not the load, is under test
    return engine


def test_engine_serialises_inference_behind_the_gate() -> None:
    model = _SlowModel()
    engine = _gated_engine(model, concurrency=1, timeout=5)
    out: list[list[list[float]]] = []
    threads = [threading.Thread(target=lambda: out.append(engine.encode_queries(["a"]))) for _ in range(3)]
    for t in threads:
        t.start()
    time.sleep(0.1)
    model.release.set()
    for t in threads:
        t.join(5)
    assert model.peak == 1 and len(out) == 3 and out[0] == [[1.0, 1.0, 1.0, 1.0]]


def test_engine_raises_busy_when_the_gate_does_not_open_in_time() -> None:
    model = _SlowModel()
    engine = _gated_engine(model, concurrency=1, timeout=0.1)
    holder = threading.Thread(target=lambda: engine.encode_passages(["hold the slot"]))
    holder.start()
    time.sleep(0.05)
    try:
        with pytest.raises(EngineBusy) as e:
            engine.encode_queries(["b"])
        assert e.value.retry_after == 5
    finally:
        model.release.set()
        holder.join(5)


def test_busy_engine_is_a_503_problem_with_retry_after(fake) -> None:
    client, _ = fake
    bus = client.app.state.bus

    class BusyEngine(FakeEngine):
        def encode_queries(self, texts):
            raise EngineBusy(30.0)

    bus.register(EmbedQuery, EmbedQueryHandler(BusyEngine(), bus), replace=True)
    r = client.post("/embeddings/query", json={"question": "anything"})
    assert r.status_code == 503 and r.headers["retry-after"] == "5"
    body = r.json()
    assert body["type"] == "urn:pfa-api:problem:model-busy" and body["retry_after"] == 5 and "busy" in body["detail"]


def test_passage_and_candidate_texts_are_size_capped(fake) -> None:
    client, _ = fake
    huge = "x" * 200_001
    assert client.post("/embeddings/passages", json={"texts": [huge]}).status_code == 422
    assert client.post("/embeddings/similarity", json={"question": "q", "candidates": [huge]}).status_code == 422
