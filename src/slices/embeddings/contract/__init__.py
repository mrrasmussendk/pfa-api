"""The embeddings Contract: three queries and their result types. Dispatch a query on the
bus; never import ``internal/``."""

from .queries import EmbedPassages, EmbedQuery, RankCandidates
from .results import Embedding, Match, PassageEmbedding, Ranking

__all__ = [
    "EmbedPassages",
    "EmbedQuery",
    "Embedding",
    "Match",
    "PassageEmbedding",
    "RankCandidates",
    "Ranking",
]
