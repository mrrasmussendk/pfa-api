from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.json_schema import JsonDict, JsonValue

from pfa.api.problems import MODEL_BUSY, Problem, domain_rejections, problem_response
from pfa.api.validation import StrictRequest, check_total_chars, text
from pfa.features.embeddings.contract import EmbedPassages, EmbedQuery, EngineBusy, RankCandidates
from pfa.kernel import Message, Result

TAG = {
    "name": "embeddings",
    "description": (
        "Unit-normalised text embeddings in 100+ languages. Embed documents once as *passage* vectors, embed each "
        "question as a *query* vector and rank by dot product (cosine, since both are normalised), or let "
        "`/embeddings/similarity` rank in one call. Cross-lingual: a Danish question ranks an English answer on meaning. "
        "The model embeds; it does not answer."
    ),
}
router = APIRouter(prefix="/embeddings", tags=["embeddings"])

MAX_QUESTION_CHARS = 20_000
MAX_TEXT_CHARS = 200_000  # per passage/candidate; the model window is enforced by chunking
MAX_TEXTS = 256  # per request
MAX_TOTAL_CHARS = 2_000_000  # per request, all texts together: the work budget one call may ask for

# The examples are one real exchange with the default model, request beside response
# (vectors cut to their first four values).
_MODEL = "intfloat/multilingual-e5-large"
_QUESTION = "Hvordan nulstiller jeg min adgangskode?"
_PASSAGES: list[JsonValue] = ["To reset your password, open Settings and choose Security.", "Opening hours: Monday to Friday, 9 to 17."]
_QUERY_OUT: JsonDict = {"model": _MODEL, "dimensions": 1024, "embedding": [0.0093, 0.0194, -0.0111, -0.0416]}
_PASSAGES_OUT: JsonDict = {
    "model": _MODEL,
    "dimensions": 1024,
    "chunks": [
        {"source_index": 0, "chunk_index": 0, "chunk_count": 1, "text": _PASSAGES[0], "embedding": [0.042, -0.0136, -0.0274, -0.0513]},
        {"source_index": 1, "chunk_index": 0, "chunk_count": 1, "text": _PASSAGES[1], "embedding": [0.0354, -0.0374, -0.0323, -0.0333]},
    ],
}
_SIMILARITY_OUT: JsonDict = {
    "model": _MODEL,
    "matches": [
        {"source_index": 0, "text": _PASSAGES[0], "score": 0.8112, "chunk_index": 0, "chunk_count": 1},
        {"source_index": 1, "text": _PASSAGES[1], "score": 0.7306, "chunk_index": 0, "chunk_count": 1},
    ],
}
_VECTOR = "Unit-normalised vector of `dimensions` floats (1024 for the default model); the example shows the first four"


def _busy(e: EngineBusy) -> Problem:
    """A saturated engine is a 503 with Retry-After: the request was fine, the service is busy."""
    return Problem(
        503, str(e), type=MODEL_BUSY, title="Embedding model busy", headers={"Retry-After": str(e.retry_after)}, retry_after=e.retry_after
    )


def _errors(path: str, *rejections: str) -> dict[int | str, dict[str, Any]]:
    """What the route at ``path`` documents beyond its 200: each reason its handler rejects with,
    and the busy 503 every embedding route can answer."""
    busy = problem_response(
        "The inference gate did not open in time; the request was fine, retry after `Retry-After` seconds",
        {"model_busy": _busy(EngineBusy(waited=30, retry_after=5))},
        instance=path,
        headers={"Retry-After": "Seconds to wait before retrying"},
    )
    return {422: domain_rejections(path, *rejections), 503: busy}


def _dispatch(request: Request, message: Message) -> Result[Any]:
    """Dispatch on the bus; a saturated engine becomes a 503 problem instead of an opaque 500."""
    try:
        return request.app.state.bus.dispatch(message)
    except EngineBusy as e:
        raise _busy(e) from e


class QuestionRequest(StrictRequest):
    model_config = ConfigDict(json_schema_extra={"examples": [{"question": _QUESTION}]})

    question: text(MAX_QUESTION_CHARS) = Field(description="Natural-language question, any of the model's 100+ languages")  # type: ignore[valid-type]


class EmbeddingOut(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [_QUERY_OUT]})

    model: str = Field(description="The model that embedded")
    dimensions: int = Field(description="Length of `embedding`")
    embedding: list[float] = Field(description=_VECTOR)


class PassagesRequest(StrictRequest):
    model_config = ConfigDict(json_schema_extra={"examples": [{"texts": _PASSAGES}]})

    texts: list[text(MAX_TEXT_CHARS)] = Field(  # type: ignore[valid-type]
        min_length=1, max_length=MAX_TEXTS, description="Documents; long ones are chunked on sentence boundaries"
    )

    @model_validator(mode="after")
    def _within_work_budget(self) -> PassagesRequest:
        check_total_chars(self.texts, MAX_TOTAL_CHARS, what="texts")
        return self


class PassageEmbeddingOut(BaseModel):
    source_index: int = Field(description="Index in `texts` of the document this chunk came from")
    chunk_index: int = Field(description="Position of this chunk within its document, from 0")
    chunk_count: int = Field(description="How many chunks the document became; 1 when it fit the model's window")
    text: str = Field(description="The chunk that was embedded")
    embedding: list[float] = Field(description=_VECTOR)


class PassagesOut(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [_PASSAGES_OUT]})

    model: str = Field(description="The model that embedded")
    dimensions: int = Field(description="Length of every `embedding`")
    chunks: list[PassageEmbeddingOut] = Field(description="One entry per chunk: documents in order, each document's chunks in order")


class SimilarityRequest(StrictRequest):
    model_config = ConfigDict(json_schema_extra={"examples": [{"question": _QUESTION, "candidates": _PASSAGES}]})

    question: text(MAX_QUESTION_CHARS) = Field(description="Natural-language question, any of the model's 100+ languages")  # type: ignore[valid-type]
    candidates: list[text(MAX_TEXT_CHARS)] = Field(  # type: ignore[valid-type]
        min_length=1, max_length=MAX_TEXTS, description="Texts to rank against the question, any language"
    )

    @model_validator(mode="after")
    def _within_work_budget(self) -> SimilarityRequest:
        check_total_chars([self.question, *self.candidates], MAX_TOTAL_CHARS, what="question and candidates")
        return self


class MatchOut(BaseModel):
    source_index: int = Field(description="Index in `candidates` of this text")
    text: str = Field(description="The candidate, or the best-scoring chunk of a long one")
    score: float = Field(
        description=(
            "Cosine similarity between the question and `text`; higher is better. e5 scores cluster in a narrow band, "
            "so compare within one response rather than against a fixed threshold"
        )
    )
    chunk_index: int = Field(description="Position of `text` within its candidate, from 0")
    chunk_count: int = Field(description="How many chunks the candidate became; 1 when it fit the model's window")


class SimilarityOut(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [_SIMILARITY_OUT]})

    model: str = Field(description="The model that scored")
    matches: list[MatchOut] = Field(description="Every candidate, best first")


@router.post(
    "/query",
    summary="Embed a question as a query vector",
    response_model=EmbeddingOut,
    response_description="The query vector, ready to rank passage vectors against",
    responses=_errors("/embeddings/query", "empty question"),
)
def embed_question(body: QuestionRequest, request: Request) -> EmbeddingOut:
    """Embed a question as a *query* vector (unit-normalised), ready to be ranked against
    passage vectors. The model embeds; it does not answer."""
    result = _dispatch(request, EmbedQuery(text=body.question))
    if not result.ok or result.value is None:
        raise Problem.domain_rejection(result.error)
    e = result.value
    return EmbeddingOut(model=e.model, dimensions=e.dimensions, embedding=list(e.vector))


@router.post(
    "/passages",
    summary="Embed documents as passage vectors",
    response_model=PassagesOut,
    response_description="One vector per chunk, tagged with its document and position",
    responses=_errors("/embeddings/passages", "every passage must be non-empty"),
)
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


@router.post(
    "/similarity",
    summary="Rank candidate texts against a question",
    response_model=SimilarityOut,
    response_description="Every candidate scored, best first",
    responses=_errors("/embeddings/similarity", "empty question", "every candidate must be non-empty"),
)
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
