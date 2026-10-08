"""The embeddings Contract: three queries, their result types, and the one infrastructure
error a consumer may see. Dispatch a query on the bus; never import ``internal/``."""

from .errors import EngineBusy
from .queries import EmbedPassages, EmbedQuery, RankCandidates
from .results import Embedding, Match, PassageEmbedding, Ranking

__all__ = [
    "EmbedPassages",
    "EmbedQuery",
    "Embedding",
    "EngineBusy",
    "Match",
    "PassageEmbedding",
    "RankCandidates",
    "Ranking",
]
