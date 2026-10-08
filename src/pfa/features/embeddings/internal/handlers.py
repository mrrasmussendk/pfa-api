"""One handler per query. Each is a small callable built around the engine and the bus;
``module.py`` registers them. Handlers return ``Result``, never raise for domain reasons,
and never know about HTTP.

Chunking is another slice's capability: the handlers dispatch ``SplitText`` / ``CountTokens``
from ``pfa.features.chunking.contract`` on the bus — declared in ``feature.json``, consumed by contract.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Protocol

from pfa.features.chunking.contract import CountTokens, SplitText
from pfa.features.embeddings.contract import (
    Embedding,
    EmbedPassages,
    EmbedQuery,
    Match,
    PassageEmbedding,
    RankCandidates,
    Ranking,
)
from pfa.kernel import Bus, Result

from .e5_engine import PASSAGE_PREFIX, QUERY_PREFIX, SPECIAL_TOKENS


class Engine(Protocol):
    """What the handlers need from an engine — satisfied by ``E5Engine`` and by test fakes."""

    @property
    def model_id(self) -> str: ...

    @property
    def max_tokens(self) -> int: ...

    def encode_queries(self, texts: Sequence[str]) -> list[list[float]]: ...

    def encode_passages(self, texts: Sequence[str]) -> list[list[float]]: ...


class _Base:
    def __init__(self, engine: Engine, bus: Bus) -> None:
        self._engine = engine
        self._bus = bus

    def _budget(self, prefix: str) -> int:
        """Tokens left for caller text once the prefix and special tokens are accounted for."""
        prefix_tokens = self._bus.dispatch(CountTokens(prefix)).value or 0
        return max(1, self._engine.max_tokens - prefix_tokens - SPECIAL_TOKENS)

    def _split(self, text: str, budget: int) -> list[str]:
        r = self._bus.dispatch(SplitText(text=text, budget=budget))
        if not r.ok or r.value is None:
            raise RuntimeError(f"chunking failed: {r.error}")  # a bug, not a domain outcome
        return list(r.value.chunks)

    def _embedding(self, vector: Sequence[float]) -> Embedding:
        return Embedding(model=self._engine.model_id, dimensions=len(vector), vector=tuple(vector))

    def _query_vector(self, text: str) -> list[float]:
        chunks = self._split(text, self._budget(QUERY_PREFIX))
        vectors = self._engine.encode_queries(chunks)
        return vectors[0] if len(vectors) == 1 else _mean_pool(vectors)

    def _passage_chunks(self, texts: Sequence[str]) -> list[tuple[int, int, int, str]]:
        """(source_index, chunk_index, chunk_count, chunk_text) for every chunk of every text."""
        budget = self._budget(PASSAGE_PREFIX)
        out: list[tuple[int, int, int, str]] = []
        for i, text in enumerate(texts):
            chunks = self._split(text, budget)
            out.extend((i, j, len(chunks), c) for j, c in enumerate(chunks))
        return out


def _mean_pool(vectors: list[list[float]]) -> list[float]:
    dims = len(vectors[0])
    mean = [sum(v[i] for v in vectors) / len(vectors) for i in range(dims)]
    norm = math.sqrt(sum(x * x for x in mean)) or 1.0
    return [x / norm for x in mean]


class EmbedQueryHandler(_Base):
    def __call__(self, q: EmbedQuery) -> Result[Embedding]:
        if not q.text.strip():
            return Result.failure("empty question")
        return Result.success(self._embedding(self._query_vector(q.text.strip())))


class EmbedPassagesHandler(_Base):
    def __call__(self, q: EmbedPassages) -> Result[tuple[PassageEmbedding, ...]]:
        texts = [t.strip() for t in q.texts]
        if not texts or any(not t for t in texts):
            return Result.failure("every passage must be non-empty")
        chunks = self._passage_chunks(texts)
        vectors = self._engine.encode_passages([c[3] for c in chunks])
        return Result.success(
            tuple(
                PassageEmbedding(source_index=i, chunk_index=j, chunk_count=n, text=text, embedding=self._embedding(v))
                for (i, j, n, text), v in zip(chunks, vectors, strict=True)
            )
        )


class RankCandidatesHandler(_Base):
    def __call__(self, q: RankCandidates) -> Result[Ranking]:
        question = q.question.strip()
        texts = [c.strip() for c in q.candidates]
        if not question:
            return Result.failure("empty question")
        if not texts or any(not t for t in texts):
            return Result.failure("every candidate must be non-empty")
        query = self._query_vector(question)
        chunks = self._passage_chunks(texts)
        vectors = self._engine.encode_passages([c[3] for c in chunks])
        # both sides are unit-normalised, so the dot product is the cosine similarity;
        # a candidate scores as its best chunk
        best: dict[int, Match] = {}
        for (i, j, n, text), v in zip(chunks, vectors, strict=True):
            score = sum(a * b for a, b in zip(query, v, strict=True))
            if i not in best or score > best[i].score:
                best[i] = Match(source_index=i, text=text, score=score, chunk_index=j, chunk_count=n)
        matches = sorted(best.values(), key=lambda m: m.score, reverse=True)
        return Result.success(Ranking(model=self._engine.model_id, matches=tuple(matches)))
