from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field
from pydantic.json_schema import JsonDict

from pfa.api.problems import Problem, domain_rejections
from pfa.api.validation import StrictRequest, text
from pfa.features.chunking.contract import SplitText

TAG = {
    "name": "chunking",
    "description": (
        "Split text into pieces that fit a model-token budget, cutting on paragraph and sentence boundaries first, "
        "then words. Counts with the embedding model's own tokenizer, so the budget is a hard limit, not an estimate. "
        "`POST /embeddings/passages` chunks for you; call this to see or control the cuts."
    ),
}
router = APIRouter(prefix="/chunking", tags=["chunking"])


MAX_TEXT_CHARS = 200_000
MAX_BUDGET = 512  # the model's window; the tokenizer, not this number, is what makes it exact

# The examples are one real exchange with the default model: a budget of 12 tokens cuts this text in four.
_TEXT = "Vertical slices keep a feature in one folder. Its contract is the only door. Consumers dispatch messages on the bus and get a Result back."
_SPLIT_OUT: JsonDict = {
    "tokenizer": "intfloat/multilingual-e5-large",
    "budget": 12,
    "chunks": [
        "Vertical slices keep a feature in one folder.",
        "Its contract is the only door.",
        "Consumers dispatch messages on the bus and get a",
        "Result back.",
    ],
    "token_counts": [11, 8, 12, 3],
}

Text = text(MAX_TEXT_CHARS)  # bound outside the model: inside it, ``text`` is the field


class SplitRequest(StrictRequest):
    model_config = ConfigDict(json_schema_extra={"examples": [{"text": _TEXT, "budget": 12}]})

    text: Text = Field(description="The text to split; paragraph and sentence boundaries are the preferred cut points")  # type: ignore[valid-type]
    budget: int = Field(
        default=500, ge=1, le=MAX_BUDGET, description="Max model tokens per chunk; a hard limit, counted with the model's tokenizer"
    )


class SplitOut(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [_SPLIT_OUT]})

    tokenizer: str = Field(description="The tokenizer that counted: the embedding model's own")
    budget: int = Field(description="The budget the chunks were cut to")
    chunks: list[str] = Field(description="The pieces in order, each stripped of surrounding whitespace")
    token_counts: list[int] = Field(description="Model tokens in each chunk, never above `budget`")


@router.post(
    "/split",
    summary="Split text into chunks within a token budget",
    response_model=SplitOut,
    response_description="The chunks in order, with the token count of each",
    responses={422: domain_rejections("/chunking/split", "empty text")},
)
def split(body: SplitRequest, request: Request) -> SplitOut:
    """Split text into chunks that each fit the token budget, cutting on sentence boundaries.
    Counts with the model's own tokenizer, so the budget is a hard limit."""
    result = request.app.state.bus.dispatch(SplitText(text=body.text, budget=body.budget))
    if not result.ok or result.value is None:
        raise Problem.domain_rejection(result.error)
    c = result.value
    return SplitOut(tokenizer=c.tokenizer, budget=c.budget, chunks=list(c.chunks), token_counts=list(c.token_counts))
