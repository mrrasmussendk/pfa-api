"""Fast, dependency-free token estimator.

Approximates BPE tokenizers on source code: counts word/identifier chunks, digit runs,
and punctuation, then applies a calibration factor. Within roughly +/-10% of
``o200k_base`` on typical source — good enough for a budget gate; swap in a real
tokenizer when precision matters.

This is the single source of truth for token counting. It is imported by BOTH Eitri
(the EIT100/EIT101 check gate) and Heimdall (the ``heimdall estimate`` subcommand), so
the harness and the gate never drift. Keep it dependency-free.

The chunking rules:

* an identifier (letter or ``_`` followed by word characters) costs ``max(1, (len + 5) // 6)``
  tokens — BPE splits long identifiers into subwords roughly every 6 characters;
* a digit run costs ``max(1, (len + 2) // 3)`` tokens;
* a run of up to 3 punctuation characters costs 1 token — runs of the same operator
  often merge into one token;
* whitespace is free.
"""

from __future__ import annotations

import re

_CHUNK = re.compile(r"(?P<ident>[^\W\d]\w*)|(?P<digits>\d+)|(?P<punct>[^\w\s]{1,3})")


def estimate(text: str) -> int:
    """Estimate the number of BPE tokens in ``text``."""
    if not text:
        return 0
    tokens = 0
    for m in _CHUNK.finditer(text):
        kind = m.lastgroup
        if kind == "ident":
            tokens += max(1, (m.end() - m.start() + 5) // 6)
        elif kind == "digits":
            tokens += max(1, (m.end() - m.start() + 2) // 3)
        else:
            tokens += 1
    return tokens
