"""Queries the embeddings slice answers. The model embeds; it does not answer questions.
It places text in a vector space so passages can be ranked against a question.

Queries and passages are embedded differently (the model needs an instruction prefix on
each side); callers pick the side by choosing the query, never the prefix.

Texts longer than the model's window are split into chunks on sentence boundaries; every
result says which chunk of which input it came from.
"""

from __future__ import annotations

from dataclasses import dataclass

from shared_kernel import Query


@dataclass(frozen=True)
class EmbedQuery(Query):
    """A question or search phrase -> one ``Embedding``. A long question is chunked and its
    chunk vectors mean-pooled, so the result is always a single unit vector."""

    text: str


@dataclass(frozen=True)
class EmbedPassages(Query):
    """Documents -> ``tuple[PassageEmbedding, ...]``, one per chunk, in input order."""

    texts: tuple[str, ...]


@dataclass(frozen=True)
class RankCandidates(Query):
    """Score every candidate against the question -> ``Ranking``, best first. A candidate's
    score is that of its best-matching chunk."""

    question: str
    candidates: tuple[str, ...]
