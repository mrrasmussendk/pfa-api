"""The model wrapper. Loads intfloat/multilingual-e5-large lazily, once, on the best device,
and serialises inference behind a bounded gate.

Lazy on purpose: importing sentence-transformers pulls in torch (seconds) and loading the
weights takes more (2 GB); neither belongs in ``create_app()`` or in a unit test. The first
request pays — or ``warmup()`` pays at startup when the deployment asks for it — and every
later request reuses the loaded model.

Concurrency: the HTTP edge runs its sync routes on a threadpool, so without a gate N concurrent
requests would run N ``encode`` calls at once. On CPU that thrashes the cores torch already
parallelises across; on a GPU it races on one model and can exhaust memory. ``_gate`` allows
``PFA_EMBED_CONCURRENCY`` encodes at a time (default 1 — a queue, in order), and a request
that waits longer than ``PFA_EMBED_QUEUE_TIMEOUT`` seconds raises ``EngineBusy`` (from the
contract) so the HTTP edge can answer 503 with Retry-After instead of letting the client hang.

The model's window is 512 tokens and it truncates silently beyond that. ``max_tokens``
(default 500, ``PFA_EMBED_MAX_TOKENS``) is the hard ceiling per encoded text INCLUDING the
instruction prefix and the two special tokens. Splitting to fit is the chunking feature's job;
the handlers ask it through the bus before encoding.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Sequence
from typing import Any

from pfa.features.embeddings.contract import EngineBusy

DEFAULT_MODEL_ID = "intfloat/multilingual-e5-large"
DEFAULT_MAX_TOKENS = 500
DEFAULT_CONCURRENCY = 1
DEFAULT_QUEUE_TIMEOUT = 30.0
SPECIAL_TOKENS = 2  # <s> ... </s>
QUERY_PREFIX = "query: "
PASSAGE_PREFIX = "passage: "


class E5Engine:
    def __init__(
        self,
        model_id: str | None = None,
        device: str | None = None,
        max_tokens: int | None = None,
        concurrency: int | None = None,
        queue_timeout: float | None = None,
    ) -> None:
        self._model_id = model_id or os.environ.get("PFA_EMBED_MODEL", DEFAULT_MODEL_ID)
        self._device = device or os.environ.get("PFA_EMBED_DEVICE") or None
        self._max_tokens = max_tokens or int(os.environ.get("PFA_EMBED_MAX_TOKENS", DEFAULT_MAX_TOKENS))
        self._concurrency = max(1, concurrency or int(os.environ.get("PFA_EMBED_CONCURRENCY", DEFAULT_CONCURRENCY)))
        self._queue_timeout = (
            queue_timeout if queue_timeout is not None else float(os.environ.get("PFA_EMBED_QUEUE_TIMEOUT", DEFAULT_QUEUE_TIMEOUT))
        )
        self._model: Any = None
        self._lock = threading.Lock()  # guards the one-time load
        self._gate = threading.BoundedSemaphore(self._concurrency)  # guards inference

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def max_tokens(self) -> int:
        return self._max_tokens

    @property
    def loaded(self) -> bool:
        return self._model is not None

    @property
    def concurrency(self) -> int:
        return self._concurrency

    def warmup(self) -> None:
        """Load the model now (startup), so the first request does not pay for it."""
        self._load()

    def _load(self) -> Any:
        if self._model is None:
            with self._lock:
                if self._model is None:
                    from sentence_transformers import SentenceTransformer  # heavy: imported on first use only

                    model = SentenceTransformer(self._model_id, device=self._device)
                    model.max_seq_length = max(self._max_tokens + SPECIAL_TOKENS, 16)
                    self._model = model
        return self._model

    def encode_queries(self, texts: Sequence[str]) -> list[list[float]]:
        return self._encode([QUERY_PREFIX + t for t in texts])

    def encode_passages(self, texts: Sequence[str]) -> list[list[float]]:
        return self._encode([PASSAGE_PREFIX + t for t in texts])

    def _encode(self, texts: list[str]) -> list[list[float]]:
        model = self._load()  # load outside the gate: a cold start must not hold the inference slot
        if not self._gate.acquire(timeout=self._queue_timeout):
            raise EngineBusy(self._queue_timeout)
        try:
            vectors = model.encode(texts, normalize_embeddings=True, convert_to_numpy=True)
        finally:
            self._gate.release()
        result: list[list[float]] = vectors.tolist()
        return result
