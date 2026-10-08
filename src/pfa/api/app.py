from __future__ import annotations

import logging
import os
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from pfa import application

from .problems import install_problem_details
from .routes import ROUTERS

log = logging.getLogger("pfa.api")


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def _warmup_in_background(app: FastAPI) -> None:
    """Load the tokenizer and the model on a daemon thread so startup returns at once:
    ``/health`` answers immediately (liveness), ``/ready`` flips when the loads finish
    (readiness). A load failure is logged and leaves ``/ready`` at 503 — the orchestrator
    sees it; a request would hit the same error and surface it as a 500 problem."""

    def load() -> None:
        try:
            application.warmup_all()
            log.info("warm-up complete: %s", application.readiness())
        except Exception:
            log.exception("warm-up failed; /ready stays 503 until a load succeeds")

    threading.Thread(target=load, name="pfa-warmup", daemon=True).start()


def create_app(*, warmup: bool | None = None) -> FastAPI:
    """The HTTP entry point: builds the application (the bus with every feature registered) and
    wraps it in FastAPI with the problem-details machinery and every router mounted.

    ``warmup`` (default: ``PFA_WARMUP`` env, off) preloads the heavy components at startup
    on a background thread. Off in tests and local dev, on in the container image."""
    do_warmup = _truthy(os.environ.get("PFA_WARMUP")) if warmup is None else warmup

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if do_warmup:
            _warmup_in_background(app)
        yield

    app = FastAPI(title="PFA API", version="0.1.0", lifespan=lifespan)
    install_problem_details(app)  # every error leaves as RFC 9457 application/problem+json
    app.state.bus = application.build_bus()
    app.state.readiness = application.readiness
    for router in ROUTERS:
        app.include_router(router)
    return app
