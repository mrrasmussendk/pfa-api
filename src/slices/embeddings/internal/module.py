"""The composition-root wiring: one engine per process (the model loads once), one handler
per query, registered on the bus. ``internal/module.py`` is the single sanctioned exception
to EIT001 — only the composition root imports it. Chunking is consumed through the bus, so
the ``chunking`` module must be registered before the first request, not before this call.

Besides ``register`` and ``router`` the module exposes two operational hooks the composition
root wires into ``/ready`` and startup warm-up: ``ready()`` reports whether the model is
loaded without loading it, ``warmup()`` loads it."""

from __future__ import annotations

from shared_kernel import Bus
from slices.embeddings.contract import EmbedPassages, EmbedQuery, RankCandidates

from .e5_engine import E5Engine
from .handlers import EmbedPassagesHandler, EmbedQueryHandler, RankCandidatesHandler
from .routes import router

__all__ = ["ready", "register", "router", "warmup"]

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
