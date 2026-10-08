from __future__ import annotations

from dataclasses import dataclass

from pfa.kernel import Query


@dataclass(frozen=True)
class CountTokens(Query):
    """How many model tokens ``text`` costs (no special tokens) -> ``int``."""

    text: str


@dataclass(frozen=True)
class SplitText(Query):
    """Split ``text`` into pieces of at most ``budget`` model tokens -> ``Chunks``.
    Cuts on paragraph and sentence boundaries first, then words, then characters;
    nothing is lost and no chunk exceeds the budget. Short text comes back as one chunk."""

    text: str
    budget: int
