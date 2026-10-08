"""The model's tokenizer, loaded lazily and alone (a few MB — the weights stay with the
embeddings feature). Counting with the real tokenizer is what makes the 500-token budget a
hard guarantee rather than an estimate."""

from __future__ import annotations

import os
import threading
from typing import Any

DEFAULT_TOKENIZER_ID = "intfloat/multilingual-e5-large"


class ModelTokenizer:
    def __init__(self, tokenizer_id: str | None = None) -> None:
        self._id = tokenizer_id or os.environ.get("PFA_EMBED_MODEL", DEFAULT_TOKENIZER_ID)
        self._tok: Any = None
        self._lock = threading.Lock()

    @property
    def tokenizer_id(self) -> str:
        return self._id

    @property
    def loaded(self) -> bool:
        return self._tok is not None

    def warmup(self) -> None:
        """Load now (startup), so the first split does not pay for it."""
        self._load()

    def _load(self) -> Any:
        if self._tok is None:
            with self._lock:
                if self._tok is None:
                    from transformers import AutoTokenizer  # heavy import: first use only

                    self._tok = AutoTokenizer.from_pretrained(self._id)
        return self._tok

    def count(self, text: str) -> int:
        return len(self._load().encode(text, add_special_tokens=False))
