from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Embedding:
    model: str
    dimensions: int
    vector: tuple[float, ...]


@dataclass(frozen=True)
class PassageEmbedding:
    """One chunk of one input passage. ``source_index`` is the position in the request;
    ``chunk_index`` / ``chunk_count`` locate the chunk within that passage."""

    source_index: int
    chunk_index: int
    chunk_count: int
    text: str
    embedding: Embedding


@dataclass(frozen=True)
class Match:
    """One candidate scored against a question. ``score`` is cosine similarity in [-1, 1];
    with this model everything meaningful lands roughly in 0.7–1.0, so compare scores to
    each other, not to an absolute threshold. ``text`` is the best-matching chunk."""

    source_index: int
    text: str
    score: float
    chunk_index: int
    chunk_count: int


@dataclass(frozen=True)
class Ranking:
    model: str
    matches: tuple[Match, ...]
