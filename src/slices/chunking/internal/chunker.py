"""Splits text into chunks that fit a token budget, cutting on sentence boundaries first.

Pure: it takes a ``count_tokens`` callable so it can run with the real tokenizer or a fake.
Strategy, in order of preference:
  1. paragraphs and sentences are packed greedily into chunks while they fit;
  2. a sentence that alone exceeds the budget is packed word by word;
  3. a single word that alone exceeds the budget is cut by character proportion.
Token counts are summed per piece, which may over-count slightly at piece boundaries —
the safe direction for a hard model limit.
"""

from __future__ import annotations

import re
from collections.abc import Callable

_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+|\n{2,}")
_WHITESPACE = re.compile(r"\s+")

CountTokens = Callable[[str], int]


def split_text(text: str, budget: int, count_tokens: CountTokens) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if budget < 1:
        raise ValueError("budget must be at least 1 token")
    if count_tokens(text) <= budget:
        return [text]
    sentences = [s.strip() for s in _SENTENCE_END.split(text) if s and s.strip()]
    pieces: list[tuple[str, int] | None] = []  # None = hard boundary: flush the current chunk
    for s in sentences:
        n = count_tokens(s)
        if n <= budget:
            pieces.append((s, n))
        else:
            # an over-long sentence is chunked on its own, never merged into a neighbour's chunk
            pieces.append(None)
            pieces.extend(_split_long_sentence(s, budget, count_tokens))
            pieces.append(None)
    return _pack(pieces, budget, sep=" ")


def _split_long_sentence(sentence: str, budget: int, count_tokens: CountTokens) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for w in _WHITESPACE.split(sentence):
        if not w:
            continue
        n = count_tokens(w)
        if n <= budget:
            out.append((w, n))
        else:
            out.extend(_split_long_word(w, budget, count_tokens))
    return out


def _split_long_word(word: str, budget: int, count_tokens: CountTokens) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    rest = word
    while rest:
        n = count_tokens(rest)
        if n <= budget:
            out.append((rest, n))
            break
        cut = max(1, int(len(rest) * budget / n))
        head = rest[:cut]
        while cut > 1 and count_tokens(head) > budget:  # proportion was optimistic: back off
            cut = max(1, cut * 3 // 4)
            head = rest[:cut]
        out.append((head, count_tokens(head)))
        rest = rest[cut:]
    return out


def _pack(pieces: list[tuple[str, int] | None], budget: int, sep: str) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    used = 0
    for piece in pieces:
        if piece is None:
            if current:
                chunks.append(sep.join(current))
                current, used = [], 0
            continue
        text, n = piece
        if current and used + n > budget:
            chunks.append(sep.join(current))
            current, used = [], 0
        current.append(text)
        used += n
    if current:
        chunks.append(sep.join(current))
    return chunks
