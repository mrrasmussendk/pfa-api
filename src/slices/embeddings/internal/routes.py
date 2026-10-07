from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, model_validator

from http_common import MODEL_BUSY, Problem, StrictRequest, check_total_chars, text
from shared_kernel import Message, Result
from slices.embeddings.contract import EmbedPassages, EmbedQuery, RankCandidates

from .e5_engine import EngineBusy

router = APIRouter(prefix="/embeddings", tags=["embeddings"])

MAX_QUESTION_CHARS = 20_000
MAX_TEXT_CHARS = 200_000  # per passage/candidate; the model window is enforced by chunking
MAX_TEXTS = 256  # per request
MAX_TOTAL_CHARS = 2_000_000  # per request, all texts together: the work budget one call may ask for


def _dispatch(request: Request, message: Message) -> Result[Any]:
    """Dispatch on the bus; a saturated engine becomes a 503 problem with Retry-After
    (the request was fine, the service is busy) instead of an opaque 500."""
    try:
        return request.app.state.bus.dispatch(message)
    except EngineBusy as e:
        raise Problem(
            503,
            str(e),
            type=MODEL_BUSY,
            title="Embedding model busy",
            headers={"Retry-After": str(e.retry_after)},
            retry_after=e.retry_after,
        ) from e


class QuestionRequest(StrictRequest):
    question: text(MAX_QUESTION_CHARS) = Field(description="Natural-language question, any of the model's 100+ languages")  # type: ignore[valid-type]


class EmbeddingOut(BaseModel):
    model: str
    dimensions: int
    embedding: list[float]


class PassagesRequest(StrictRequest):
    texts: list[text(MAX_TEXT_CHARS)] = Field(  # type: ignore[valid-type]
        min_length=1, max_length=MAX_TEXTS, description="Documents; long ones are chunked on sentence boundaries"
    )

    @model_validator(mode="after")
    def _within_work_budget(self) -> PassagesRequest:
        check_total_chars(self.texts, MAX_TOTAL_CHARS, what="texts")
        return self


class PassageEmbeddingOut(BaseModel):
    source_index: int
    chunk_index: int
    chunk_count: int
    text: str
    embedding: list[float]


class PassagesOut(BaseModel):
    model: str
    dimensions: int
    chunks: list[PassageEmbeddingOut]


class SimilarityRequest(StrictRequest):
    question: text(MAX_QUESTION_CHARS)  # type: ignore[valid-type]
    candidates: list[text(MAX_TEXT_CHARS)] = Field(  # type: ignore[valid-type]
        min_length=1, max_length=MAX_TEXTS, description="Texts to rank against the question, any language"
    )

    @model_validator(mode="after")
    def _within_work_budget(self) -> SimilarityRequest:
        check_total_chars([self.question, *self.candidates], MAX_TOTAL_CHARS, what="question and candidates")
        return self


class MatchOut(BaseModel):
    source_index: int
    text: str
    score: float
    chunk_index: int
    chunk_count: int


class SimilarityOut(BaseModel):
    model: str
    matches: list[MatchOut]


@router.post("/query", response_model=EmbeddingOut)
def embed_question(body: QuestionRequest, request: Request) -> EmbeddingOut:
    """Embed a question as a *query* vector (unit-normalised), ready to be ranked against
    passage vectors. The model embeds; it does not answer."""
    result = _dispatch(request, EmbedQuery(text=body.question))
    if not result.ok or result.value is None:
        raise Problem.domain_rejection(result.error)
    e = result.value
    return EmbeddingOut(model=e.model, dimensions=e.dimensions, embedding=list(e.vector))


@router.post("/passages", response_model=PassagesOut)
def embed_passages(body: PassagesRequest, request: Request) -> PassagesOut:
    """Embed documents as *passage* vectors. A document over the model's window comes back
    as several chunks, each tagged with its source index and chunk position."""
    result = _dispatch(request, EmbedPassages(texts=tuple(body.texts)))
    if not result.ok or result.value is None:
        raise Problem.domain_rejection(result.error)
    chunks = result.value
    return PassagesOut(
        model=chunks[0].embedding.model,
        dimensions=chunks[0].embedding.dimensions,
        chunks=[
            PassageEmbeddingOut(
                source_index=c.source_index,
                chunk_index=c.chunk_index,
                chunk_count=c.chunk_count,
                text=c.text,
                embedding=list(c.embedding.vector),
            )
            for c in chunks
        ],
    )


@router.post("/similarity", response_model=SimilarityOut)
def similarity(body: SimilarityRequest, request: Request) -> SimilarityOut:
    """Rank candidate texts by how well they answer the question, best first. Cross-lingual:
    a Danish question ranks an English answer on meaning, not on shared words. A long
    candidate is scored by its best chunk, which is returned as ``text``."""
    result = _dispatch(request, RankCandidates(question=body.question, candidates=tuple(body.candidates)))
    if not result.ok or result.value is None:
        raise Problem.domain_rejection(result.error)
    r = result.value
    return SimilarityOut(
        model=r.model,
        matches=[
            MatchOut(source_index=m.source_index, text=m.text, score=m.score, chunk_index=m.chunk_index, chunk_count=m.chunk_count)
            for m in r.matches
        ],
    )
