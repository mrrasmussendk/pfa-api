"""Local chunking fast path for bulk passage embedding.

``EmbedPassages`` dispatches one ``SplitText`` per passage over the bus. For a corpus
ingest of tens of thousands of passages that is one message, one ``Result`` and one
tuple copy per document before a single vector is produced. Calling the chunker
directly with the engine's own token counter keeps the whole pre-pass in one tight
loop and lets us batch ``encode_passages`` across documents instead of per document.

The chunking slice is already a declared dependency, so this only skips the bus hop.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass

from slices.chunking.internal.chunker import split_text

from .e5_engine import PASSAGE_PREFIX, SPECIAL_TOKENS

CountTokens = Callable[[str], int]


@dataclass(frozen=True, slots=True)
class PassageChunk:
    source_index: int
    chunk_index: int
    chunk_count: int
    text: str


def passage_budget(max_tokens: int, count_tokens: CountTokens) -> int:
    """Tokens left for caller text once the passage prefix and special tokens are paid for."""
    return max(1, max_tokens - count_tokens(PASSAGE_PREFIX) - SPECIAL_TOKENS)


def chunk_passages(texts: Sequence[str], budget: int, count_tokens: CountTokens) -> list[PassageChunk]:
    """Split every passage to ``budget`` and flatten, keeping provenance for re-assembly."""
    out: list[PassageChunk] = []
    for i, text in enumerate(texts):
        chunks = split_text(text, budget, count_tokens)
        out.extend(PassageChunk(i, j, len(chunks), c) for j, c in enumerate(chunks))
    return out


def batched(chunks: Iterable[PassageChunk], size: int) -> Iterator[list[PassageChunk]]:
    """Yield ``size``-sized batches of chunks so one ``encode_passages`` call spans documents."""
    if size < 1:
        raise ValueError("batch size must be at least 1")
    batch: list[PassageChunk] = []
    for chunk in chunks:
        batch.append(chunk)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch
