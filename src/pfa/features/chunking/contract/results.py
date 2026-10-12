from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Chunks:
    """``text`` split to ``budget``: the pieces in order, each with its token count, counted by
    ``tokenizer`` (the model's id, so a consumer can check it counts for the model it feeds)."""

    tokenizer: str
    budget: int
    chunks: tuple[str, ...]
    token_counts: tuple[int, ...]
