"""Liveness and readiness. Both are ``async def`` on purpose: they run on the event loop,
never on the threadpool the sync slice routes share. A cold model load (or a burst of
first requests) can tie that pool up for seconds; the orchestrator's probes must keep
answering through it, or a slow start is mistaken for a dead process and restarted — a
loop that never finishes loading."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from pfa.api.problems import NOT_READY, Problem

router = APIRouter(tags=["service"])


class ReadyOut(BaseModel):
    status: str
    components: dict[str, bool]


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness: the process answers. Says nothing about the model."""
    return {"status": "ok"}


@router.get("/ready", response_model=ReadyOut)
async def ready(request: Request) -> ReadyOut:
    """Readiness: every heavy component is loaded, so a request will be served without a
    cold start. 503 problem document while loading — route traffic elsewhere until 200.
    Reads flags only; it never triggers a load itself."""
    components: dict[str, bool] = request.app.state.readiness()
    if not all(components.values()):
        pending = sorted(name for name, ok in components.items() if not ok)
        raise Problem(
            503,
            f"still loading: {', '.join(pending)}",
            type=NOT_READY,
            title="Service not ready",
            headers={"Retry-After": "5", "Cache-Control": "no-store"},
            components=components,
        )
    return ReadyOut(status="ready", components=components)
