"""Liveness and readiness. Both are ``async def`` on purpose: they run on the event loop,
never on the threadpool the sync feature routes share. A cold model load (or a burst of
first requests) can tie that pool up for seconds; the orchestrator's probes must keep
answering through it, or a slow start is mistaken for a dead process and restarted — a
loop that never finishes loading."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from pfa.api.problems import NOT_READY, Problem, problem_response
from pfa.api.validation import with_example

TAG = {
    "name": "service",
    "description": (
        "Probes for the orchestrator. `/health` says the process answers; `/ready` says every heavy component is in "
        "memory. Route traffic on `/ready`, restart on `/health`."
    ),
}
router = APIRouter(tags=["service"])


class HealthOut(BaseModel):
    model_config = with_example({"status": "ok"})

    status: str = Field(description="Always `ok`: the process answers")


class ReadyOut(BaseModel):
    model_config = with_example({"status": "ready", "components": {"tokenizer": True, "embedding_model": True}})

    status: str = Field(description="`ready` once every component is loaded")
    components: dict[str, bool] = Field(description="Each heavy component and whether it is in memory")


def _not_ready(components: dict[str, bool]) -> Problem:
    """503 while loading: ``components`` says which, ``Retry-After`` says when to poll again."""
    pending = sorted(name for name, ok in components.items() if not ok)
    return Problem(
        503,
        f"still loading: {', '.join(pending)}",
        type=NOT_READY,
        title="Service not ready",
        headers={"Retry-After": "5", "Cache-Control": "no-store"},
        components=components,
    )


NOT_READY_RESPONSE = problem_response(
    "Still loading: route traffic elsewhere and poll again after `Retry-After` seconds; `components` says what is pending",
    {"loading": _not_ready({"tokenizer": True, "embedding_model": False})},
    instance="/ready",
    headers={"Retry-After": "Seconds until the next poll", "Cache-Control": "`no-store`: a probe is never cached"},
)


@router.get("/health", summary="Liveness", response_model=HealthOut, response_description="The process answers")
async def health() -> HealthOut:
    """Liveness: the process answers. Says nothing about the model."""
    return HealthOut(status="ok")


@router.get(
    "/ready",
    summary="Readiness",
    response_model=ReadyOut,
    response_description="Every heavy component is in memory; a request is served without a cold start",
    responses={503: NOT_READY_RESPONSE},
)
async def ready(request: Request) -> ReadyOut:
    """Readiness: every heavy component is loaded, so a request will be served without a
    cold start. 503 problem document while loading — route traffic elsewhere until 200.
    Reads flags only; it never triggers a load itself."""
    components: dict[str, bool] = request.app.state.readiness()
    if not all(components.values()):
        raise _not_ready(components)
    return ReadyOut(status="ready", components=components)
