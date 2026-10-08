from __future__ import annotations

from typing import Protocol

from pfa.features.chunking.contract import Chunks, CountTokens, SplitText
from pfa.kernel import Result

from .chunker import split_text


class Tokenizer(Protocol):
    """What the handlers need — satisfied by ``ModelTokenizer`` and by test fakes."""

    @property
    def tokenizer_id(self) -> str: ...

    def count(self, text: str) -> int: ...


class CountTokensHandler:
    def __init__(self, tokenizer: Tokenizer) -> None:
        self._tok = tokenizer

    def __call__(self, q: CountTokens) -> Result[int]:
        return Result.success(self._tok.count(q.text))


class SplitTextHandler:
    def __init__(self, tokenizer: Tokenizer) -> None:
        self._tok = tokenizer

    def __call__(self, q: SplitText) -> Result[Chunks]:
        if q.budget < 1:
            return Result.failure("budget must be at least 1 token")
        if not q.text.strip():
            return Result.failure("empty text")
        chunks = split_text(q.text, q.budget, self._tok.count)
        return Result.success(
            Chunks(
                tokenizer=self._tok.tokenizer_id,
                budget=q.budget,
                chunks=tuple(chunks),
                token_counts=tuple(self._tok.count(c) for c in chunks),
            )
        )
