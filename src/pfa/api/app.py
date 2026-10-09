from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.routing import APIRoute

from pfa import application

from .logs import TraceContext, configure_logging
from .problems import install_problem_details
from .routes import ROUTERS, TAGS

log = logging.getLogger("pfa.api")

DESCRIPTION = """\
One model, [intfloat/multilingual-e5-large](https://huggingface.co/intfloat/multilingual-e5-large): 100+ languages,
1024-dimensional unit-normalised vectors, cross-lingual on meaning rather than shared words.

**The flow.** Embed your documents once with `POST /embeddings/passages` and store the vectors. For each question,
`POST /embeddings/query` gives the vector to rank them by: dot product, which is cosine since every vector is
unit-normalised. `POST /embeddings/similarity` does both in one call when the candidates are at hand. A long document
is chunked on sentence boundaries with the model's own tokenizer; `POST /chunking/split` shows the cuts.

**Errors** are RFC 9457 problem documents (`application/problem+json`): branch on `type`, read `detail`, and quote
`trace_id` when reporting. It is the W3C trace id on every response header and every log line.
"""


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def _operation_id(route: APIRoute) -> str:
    """Client generators name methods after the operation id: ``embed_passages``, not FastAPI's
    ``embed_passages_embeddings_passages_post``. Route function names are unique by construction."""
    return route.name


def _warmup_in_background(app: FastAPI) -> None:
    """Load the tokenizer and the model on a daemon thread so startup returns at once:
    ``/health`` answers immediately (liveness), ``/ready`` flips when the loads finish
    (readiness). A load failure is logged and leaves ``/ready`` at 503 — the orchestrator
    sees it; a request would hit the same error and surface it as a 500 problem."""

    def load() -> None:
        started = time.perf_counter()
        try:
            application.warmup_all()
            ms = round((time.perf_counter() - started) * 1000.0, 1)
            log.info("warm-up complete", extra={"msgid": "warmup", "load_ms": ms, **application.readiness()})
        except Exception:
            log.exception("warm-up failed; /ready stays 503 until a load succeeds", extra={"msgid": "warmup"})

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

    configure_logging()  # RFC 5424 records on stdout; idempotent
    app = FastAPI(
        title="PFA API",
        summary="Multilingual text embeddings, chunking and similarity",
        description=DESCRIPTION,
        version="0.1.0",
        openapi_tags=list(TAGS),
        generate_unique_id_function=_operation_id,
        # Try it out is open from the start; the Schemas list at the foot is hidden, every model
        # being one click away under its operation.
        swagger_ui_parameters={"tryItOutEnabled": True, "displayRequestDuration": True, "defaultModelsExpandDepth": -1},
        lifespan=lifespan,
    )
    install_problem_details(app)  # every error leaves as RFC 9457 application/problem+json
    app.add_middleware(TraceContext)  # traceparent in and out, one request line per request
    app.state.bus = application.build_bus()
    app.state.readiness = application.readiness
    for router in ROUTERS:
        app.include_router(router)
    return app
