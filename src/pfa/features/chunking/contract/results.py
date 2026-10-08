from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Chunks:
    tokenizer: str
    budget: int
    chunks: tuple[str, ...]
    token_counts: tuple[int, ...]
