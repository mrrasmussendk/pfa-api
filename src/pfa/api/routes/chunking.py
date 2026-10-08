from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from pfa.api.problems import Problem
from pfa.api.validation import StrictRequest, text
from pfa.features.chunking.contract import SplitText

router = APIRouter(prefix="/chunking", tags=["chunking"])


MAX_TEXT_CHARS = 200_000
MAX_BUDGET = 512  # the model's window; the tokenizer, not this number, is what makes it exact


class SplitRequest(StrictRequest):
    text: text(MAX_TEXT_CHARS)  # type: ignore[valid-type]
    budget: int = Field(default=500, ge=1, le=MAX_BUDGET, description="max model tokens per chunk")


class SplitOut(BaseModel):
    tokenizer: str
    budget: int
    chunks: list[str]
    token_counts: list[int]


@router.post("/split", response_model=SplitOut)
def split(body: SplitRequest, request: Request) -> SplitOut:
    """Split text into chunks that each fit the token budget, cutting on sentence boundaries.
    Counts with the model's own tokenizer, so the budget is a hard limit."""
    result = request.app.state.bus.dispatch(SplitText(text=body.text, budget=body.budget))
    if not result.ok or result.value is None:
        raise Problem.domain_rejection(result.error)
    c = result.value
    return SplitOut(tokenizer=c.tokenizer, budget=c.budget, chunks=list(c.chunks), token_counts=list(c.token_counts))
