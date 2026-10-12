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

import logging
import os
import threading
import time
from collections.abc import Callable, Sequence
from typing import Any, TypeVar

from pfa.features.embeddings.contract import EngineBusy

DEFAULT_MODEL_ID = "intfloat/multilingual-e5-large"
DEFAULT_MAX_TOKENS = 500
DEFAULT_CONCURRENCY = 1
DEFAULT_QUEUE_TIMEOUT = 30.0
SPECIAL_TOKENS = 2  # <s> ... </s>
QUERY_PREFIX = "query: "
PASSAGE_PREFIX = "passage: "

log = logging.getLogger(__name__)

T = TypeVar("T")


def _setting(given: T | None, name: str, default: T, parse: Callable[[str], T]) -> T:
    """The constructor's value when given, else the environment's, else the default. A value the
    environment cannot parse fails here, when the engine is built, naming the variable."""
    if given is not None:
        return given
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return parse(raw)
    except ValueError as e:
        raise ValueError(f"{name}={raw!r} is not a valid {parse.__name__}") from e


class E5Engine:
    def __init__(
        self,
        model_id: str | None = None,
        device: str | None = None,
        max_tokens: int | None = None,
        concurrency: int | None = None,
        queue_timeout: float | None = None,
    ) -> None:
        self._model_id = _setting(model_id, "PFA_EMBED_MODEL", DEFAULT_MODEL_ID, str)
        self._device = device or os.environ.get("PFA_EMBED_DEVICE") or None  # unset or empty: the best available
        self._max_tokens = _setting(max_tokens, "PFA_EMBED_MAX_TOKENS", DEFAULT_MAX_TOKENS, int)
        self._concurrency = max(1, _setting(concurrency, "PFA_EMBED_CONCURRENCY", DEFAULT_CONCURRENCY, int))
        self._queue_timeout = _setting(queue_timeout, "PFA_EMBED_QUEUE_TIMEOUT", DEFAULT_QUEUE_TIMEOUT, float)
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
                    started = time.perf_counter()
                    model = self.build_model()
                    model.max_seq_length = max(self._max_tokens + SPECIAL_TOKENS, 16)
                    self._model = model
                    log.info(
                        "model loaded",
                        extra={
                            "msgid": "load",
                            "model_id": self._model_id,
                            "device": self._device_name(model),
                            "max_seq_length": model.max_seq_length,
                            "load_ms": round((time.perf_counter() - started) * 1000.0, 1),
                        },
                    )
        return self._model

    def build_model(self) -> Any:
        """Fetch the weights themselves; a test overrides this to stand in for the 2 GB model."""
        from sentence_transformers import SentenceTransformer  # heavy: imported on first use only

        return SentenceTransformer(self._model_id, device=self._device)

    def encode_queries(self, texts: Sequence[str]) -> list[list[float]]:
        return self._encode([QUERY_PREFIX + t for t in texts], side="query")

    def encode_passages(self, texts: Sequence[str]) -> list[list[float]]:
        return self._encode([PASSAGE_PREFIX + t for t in texts], side="passage")

    def _device_name(self, model: Any) -> str:
        return str(getattr(model, "device", None) or self._device or "auto")

    def _encode(self, texts: list[str], side: str = "passage") -> list[list[float]]:
        model = self._load()  # load outside the gate: a cold start must not hold the inference slot
        waited = time.perf_counter()
        if not self._gate.acquire(timeout=self._queue_timeout):
            wait_ms = round((time.perf_counter() - waited) * 1000.0, 1)
            log.warning("inference gate did not open", extra={"msgid": "busy", "side": side, "texts": len(texts), "wait_ms": wait_ms})
            raise EngineBusy(self._queue_timeout)
        wait_ms = round((time.perf_counter() - waited) * 1000.0, 1)
        started = time.perf_counter()
        try:
            vectors = model.encode(texts, normalize_embeddings=True, convert_to_numpy=True)
        finally:
            self._gate.release()
        log.info(
            "encode",
            extra={
                "msgid": "encode",
                "side": side,
                "texts": len(texts),
                "chars": sum(len(t) for t in texts),  # sizes only — never the text
                "wait_ms": wait_ms,
                "encode_ms": round((time.perf_counter() - started) * 1000.0, 1),
                "device": self._device_name(model),
            },
        )
        result: list[list[float]] = vectors.tolist()
        return result
