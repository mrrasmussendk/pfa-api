from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Chunks:
    """The answer to ``SplitText``. Tuples, not lists, because a result is a value: no consumer
    may change what another one received."""

    # The model whose tokenizer counted. A budget only holds for that model, so a consumer feeding
    # another one must refuse the chunks rather than trust the counts.
    tokenizer: str
    # Echoed back so a caller that logs or forwards the chunks need not carry the request beside them.
    budget: int
    chunks: tuple[str, ...]
    # One per chunk, each at most ``budget``: a caller can check the guarantee without a tokenizer of its own.
    token_counts: tuple[int, ...]
