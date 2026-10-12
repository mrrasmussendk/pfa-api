"""The model's tokenizer, loaded alone: a few MB, where the weights are 2 GB, so chunking can
count without the embeddings feature's model. Counting with the model's own tokenizer, not
characters or words, is what makes a caller's token budget a guarantee rather than an estimate."""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Protocol

# Repeats the embeddings engine's default model: chunking may not import embeddings, and should the two
# ever drift, embeddings refuses the chunks (``Chunks.tokenizer``) instead of mis-sizing them.
DEFAULT_TOKENIZER_ID = "intfloat/multilingual-e5-large"

log = logging.getLogger(__name__)


class Encoder(Protocol):
    """The one thing chunking asks of a transformers tokenizer. A protocol, so transformers (seconds
    of import) is loaded only when a tokenizer is, and a test can stand in for it."""

    def encode(self, text: str, *, add_special_tokens: bool, verbose: bool) -> list[Any]: ...


class ModelTokenizer:
    """Counts tokens exactly as the embedding model will see them. Loads on first use, not at
    construction, so building the app (and every test) costs no download."""

    def __init__(self, tokenizer_id: str | None = None) -> None:
        # the embedding model's own variable: the tokenizer must be the one of the model that encodes
        self._id = tokenizer_id if tokenizer_id is not None else os.environ.get("PFA_EMBED_MODEL", DEFAULT_TOKENIZER_ID)
        self._tok: Encoder | None = None
        self._lock = threading.Lock()  # routes run on a threadpool: concurrent first requests must load once

    @property
    def tokenizer_id(self) -> str:
        """Stamped on every ``Chunks``, so a consumer can refuse counts made for another model."""
        return self._id

    @property
    def loaded(self) -> bool:
        """Whether the load happened, without triggering it: ``/ready`` asks this and must stay cheap."""
        return self._tok is not None

    def warmup(self) -> None:
        """Load now, at startup, so the first request does not wait seconds for the download."""
        self._load()

    def _load(self) -> Encoder:
        """The tokenizer, fetched once per process. The load is timed and logged because it is the slow
        step an operator watches at startup, and the one ``/ready`` waits on."""
        if self._tok is None:  # checked before the lock so a loaded tokenizer never waits on it
            with self._lock:
                if self._tok is None:  # and again inside it: another thread may have loaded meanwhile
                    started = time.perf_counter()
                    self._tok = self.build_tokenizer()
                    load_ms = round((time.perf_counter() - started) * 1000.0, 1)
                    log.info("tokenizer loaded", extra={"msgid": "load", "tokenizer_id": self._id, "load_ms": load_ms})
        return self._tok

    def build_tokenizer(self) -> Encoder:
        """Fetch the tokenizer itself; a test overrides this to stand in for the download."""
        from transformers import AutoTokenizer  # see Encoder: imported only when a load is due

        try:
            encoder: Encoder = AutoTokenizer.from_pretrained(self._id)
            return encoder
        except OSError as e:  # unreachable hub, unknown id, unreadable path: say which tokenizer
            raise RuntimeError(f"could not load tokenizer {self._id!r} (PFA_EMBED_MODEL)") from e

    def count(self, text: str) -> int:
        """Model tokens in ``text``. Without special tokens: the consumer adds its own prefix and
        specials to the budget it asks for. Not verbose: chunking counts whole texts longer than the
        model's window before cutting them, and transformers would warn on each of indexing errors
        that cannot happen, since an uncut text never reaches the model."""
        return len(self._load().encode(text, add_special_tokens=False, verbose=False))
