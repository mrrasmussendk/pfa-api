"""The feature's plug into the application: one engine per process (the model loads once), one
handler per query, registered on the bus. This is the only module of the feature besides
``contract/`` that ``pfa.application`` imports; nothing else ever does. Chunking is consumed
through the bus, so the ``chunking`` module must be registered before the first request, not
before this call.

Besides ``register`` the module exposes two operational hooks the application exposes for
``/ready`` and startup warm-up: ``ready()`` reports whether the model is loaded without loading
it, ``warmup()`` loads it."""

from __future__ import annotations

from pfa.features.embeddings.contract import EmbedPassages, EmbedQuery, RankCandidates
from pfa.kernel import Bus

from .internal.e5_engine import E5Engine
from .internal.handlers import EmbedPassagesHandler, EmbedQueryHandler, RankCandidatesHandler

__all__ = ["ready", "register", "warmup"]

_engine: E5Engine | None = None


def register(bus: Bus) -> None:
    global _engine  # noqa: PLW0603 — one engine per process is the point
    _engine = E5Engine()
    bus.register(EmbedQuery, EmbedQueryHandler(_engine, bus))
    bus.register(EmbedPassages, EmbedPassagesHandler(_engine, bus))
    bus.register(RankCandidates, RankCandidatesHandler(_engine, bus))


def ready() -> dict[str, bool]:
    """Component readiness, never triggering a load."""
    return {"embedding_model": _engine is not None and _engine.loaded}


def warmup() -> None:
    """Load the model now. Called from a startup thread when ``PFA_WARMUP`` is on."""
    if _engine is not None:
        _engine.warmup()
