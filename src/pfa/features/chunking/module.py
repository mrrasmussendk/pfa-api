"""The feature's plug into the application: one tokenizer per process, one handler per query.
Only ``pfa.application`` imports this module (and the contract); nothing else ever does.
``ready()`` / ``warmup()`` are the operational hooks behind ``/ready`` and startup warm-up."""

from __future__ import annotations

from pfa.features.chunking.contract import CountTokens, SplitText
from pfa.kernel import Bus

from .internal.handlers import CountTokensHandler, SplitTextHandler
from .internal.tokenizer import ModelTokenizer

__all__ = ["ready", "register", "warmup"]

_tokenizer: ModelTokenizer | None = None


def register(bus: Bus) -> None:
    global _tokenizer  # noqa: PLW0603 — one tokenizer per process is the point
    _tokenizer = ModelTokenizer()  # loads on first use (or on warmup)
    bus.register(CountTokens, CountTokensHandler(_tokenizer))
    bus.register(SplitText, SplitTextHandler(_tokenizer))


def ready() -> dict[str, bool]:
    return {"tokenizer": _tokenizer is not None and _tokenizer.loaded}


def warmup() -> None:
    if _tokenizer is not None:
        _tokenizer.warmup()
