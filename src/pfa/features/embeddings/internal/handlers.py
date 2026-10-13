"""One handler per query, so ``grep EmbedQuery`` finds the one place that use case is served.
Handlers return ``Result`` for what a caller got wrong and raise only for bugs, so the route,
not the handler, decides what a rejection means on the wire.

Chunking is reached through its contract on the bus, not imported: the model's window is a
chunking concern, and embeddings must not grow its own copy of the tokenizer to fit it.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any, Protocol

from pfa.features.chunking.contract import Chunks, CountTokens, SplitText
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
    """What the handlers need from an engine. A protocol rather than ``E5Engine`` itself, so a test
    can stand in for 2 GB of weights with a class that only has these four members."""

    @property
    def model_id(self) -> str: ...

    @property
    def max_tokens(self) -> int: ...

    def encode_queries(self, texts: Sequence[str]) -> list[list[float]]: ...

    def encode_passages(self, texts: Sequence[str]) -> list[list[float]]: ...


class _Base:
    """What every handler shares: fitting text to the model's window and encoding it. Each handler is
    one use case, so the shared steps live here instead of in a service the handlers would call."""

    def __init__(self, engine: Engine, bus: Bus) -> None:
        self._engine = engine
        self._bus = bus

    def _chunking(self, message: CountTokens | SplitText) -> Any:
        """Chunking's answer. Text that passed this feature's validation is always countable and
        splittable, so a failure is a bug in the seam, raised, never a domain outcome. ``Any``
        because the two messages answer different types; each caller names the one it asked for."""
        r = self._bus.dispatch(message)
        if not r.ok or r.value is None:
            raise RuntimeError(f"chunking failed on {type(message).__name__}: {r.error}")
        return r.value

    def _budget(self, prefix: str) -> int:
        """Tokens left for caller text once the prefix and special tokens are accounted for."""
        prefix_tokens: int = self._chunking(CountTokens(prefix))
        # at least one token: chunking refuses a budget of 0, and a tiny PFA_EMBED_MAX_TOKENS should
        # still embed (badly) rather than fail every request
        return max(1, self._engine.max_tokens - prefix_tokens - SPECIAL_TOKENS)

    def _split(self, text: str, budget: int) -> list[str]:
        """The text cut to fit the window: the model truncates silently, so an unsplit long text would
        lose its tail without anyone noticing."""
        chunks: Chunks = self._chunking(SplitText(text=text, budget=budget))
        if chunks.tokenizer != self._engine.model_id:
            # budgets counted by another model's tokenizer would not fit this model's window
            raise RuntimeError(f"chunking counted with {chunks.tokenizer}, but the engine encodes with {self._engine.model_id}")
        return list(chunks.chunks)

    def _embedding(self, vector: Sequence[float]) -> Embedding:
        """Dimensions are read off the vector, not assumed to be 1024, so another model id just works."""
        return Embedding(model=self._engine.model_id, dimensions=len(vector), vector=tuple(vector))

    def _query_vector(self, text: str) -> list[float]:
        chunks = self._split(text, self._budget(QUERY_PREFIX))
        vectors = self._engine.encode_queries(chunks)
        # a question is one meaning, so a long one is one vector: the chunks' mean, not a vector per chunk
        return vectors[0] if len(vectors) == 1 else _mean_pool(vectors)

    def _passage_chunks(self, texts: Sequence[str]) -> list[tuple[int, int, int, str]]:
        """(source_index, chunk_index, chunk_count, chunk_text) for every chunk of every text."""
        budget = self._budget(PASSAGE_PREFIX)
        out: list[tuple[int, int, int, str]] = []
        for i, text in enumerate(texts):
            chunks = self._split(text, budget)
            out.extend((i, j, len(chunks), c) for j, c in enumerate(chunks))
        return out

    def _encoded_passages(self, texts: Sequence[str]) -> list[tuple[tuple[int, int, int, str], list[float]]]:
        """Every chunk of every text beside its passage vector, encoded in one call so the gate is taken once."""
        chunks = self._passage_chunks(texts)
        vectors = self._engine.encode_passages([text for _, _, _, text in chunks])
        return list(zip(chunks, vectors, strict=True))


def _stripped(texts: Sequence[str]) -> list[str] | None:
    """The texts stripped, or None when there are none or one is blank: a blank text has no meaning to embed."""
    out = [t.strip() for t in texts]
    return out if out and all(out) else None


def _mean_pool(vectors: list[list[float]]) -> list[float]:
    """The chunks' mean direction, renormalised: the mean of unit vectors is shorter than one, and
    every score downstream is a dot product that assumes unit length. A zero mean (opposite chunks)
    has no direction to keep, so it is returned as is rather than divided by zero."""
    dims = len(vectors[0])
    mean = [sum(v[i] for v in vectors) / len(vectors) for i in range(dims)]
    norm = math.sqrt(sum(x * x for x in mean)) or 1.0
    return [x / norm for x in mean]


class EmbedQueryHandler(_Base):
    """A question as one query vector, to rank stored passage vectors against."""

    def __call__(self, q: EmbedQuery) -> Result[Embedding]:
        if not q.text.strip():  # the edge bounds length; whether blank text means anything is this feature's call
            return Result.failure("empty question")
        return Result.success(self._embedding(self._query_vector(q.text.strip())))


class EmbedPassagesHandler(_Base):
    """A vector per chunk, not per document: a caller stores them for retrieval, and a long document
    pooled into one vector would match nothing in particular."""

    def __call__(self, q: EmbedPassages) -> Result[tuple[PassageEmbedding, ...]]:
        texts = _stripped(q.texts)
        if texts is None:
            return Result.failure("every passage must be non-empty")
        return Result.success(
            tuple(
                PassageEmbedding(source_index=i, chunk_index=j, chunk_count=n, text=text, embedding=self._embedding(v))
                for (i, j, n, text), v in self._encoded_passages(texts)
            )
        )


class RankCandidatesHandler(_Base):
    """A candidate scores as its best chunk: one relevant paragraph in a long text is a good answer,
    and averaging it with the rest would bury it."""

    def __call__(self, q: RankCandidates) -> Result[Ranking]:
        question = q.question.strip()
        texts = _stripped(q.candidates)
        if not question:
            return Result.failure("empty question")
        if texts is None:
            return Result.failure("every candidate must be non-empty")
        query = self._query_vector(question)
        # both sides are unit-normalised, so the dot product is the cosine similarity;
        # a candidate scores as its best chunk
        best: dict[int, Match] = {}
        for (i, j, n, text), v in self._encoded_passages(texts):
            score = sum(a * b for a, b in zip(query, v, strict=True))
            if i not in best or score > best[i].score:
                best[i] = Match(source_index=i, text=text, score=score, chunk_index=j, chunk_count=n)
        matches = sorted(best.values(), key=lambda m: m.score, reverse=True)
        return Result.success(Ranking(model=self._engine.model_id, matches=tuple(matches)))
