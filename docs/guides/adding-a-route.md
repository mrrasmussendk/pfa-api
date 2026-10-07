# Adding a route

A route is the HTTP edge of a slice. It lives **inside the slice** that owns the use case, in `src/slices/<slice>/internal/routes.py`, and is mounted by the composition root through the slice's `module.py`. Service-level routes that belong to no slice (health, metadata) live in `src/pfa_api/routes/`.

A route never does work itself. It converts the HTTP request into a **message** from the slice's contract, dispatches it on the kernel `Bus`, and converts the `Result` back into HTTP. The handler behind the message is what knows the domain. Before you start, decide which of the three cases you are in. They have different working sets.

| Case | You need | Working set |
|---|---|---|
| **A. The contract already has the message** | request/response models + a route | `internal/routes.py`, `contract/`, the slice's `AGENTS.md`, `tests/api/test_<slice>.py` |
| **B. The message does not exist yet** | first a contract change, then a handler, then the route | A + `contract/queries.py` (or `commands.py`), `contract/results.py`, `internal/handlers.py`, `internal/module.py` |
| **C. The handler needs another slice's capability** | that slice's **contract** and a declared dependency | A or B + `slices/<dep>/contract/`, `slice.json` |

If none fits — the use case is a new concept with its own data and rules — you are adding a **slice**, not a route. See "Adding a slice" in the root `AGENTS.md`.

## The shape every route follows

```python
# src/slices/<slice>/internal/routes.py
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from http_common import Problem, StrictRequest, text  # the HTTP-edge helpers: RFC 9457 errors + request validation
from slices.<slice>.contract import <Message>         # the slice's OWN contract: the message to dispatch

router = APIRouter(prefix="/<slice>", tags=["<slice>"])   # exactly one router per slice


class <Verb><Noun>Request(StrictRequest):   # unknown fields are a 422; models never appear in contract/ (EIT003)
    <field>: text(MAX_CHARS)                 # length-bounded, no control characters (http_common.text)


class <Noun>Out(BaseModel):
    ...


@router.post("/<verb>", response_model=<Noun>Out)
def <verb>(body: <Verb><Noun>Request, request: Request) -> <Noun>Out:
    result = request.app.state.bus.dispatch(<Message>(...))   # pydantic in -> contract message
    if not result.ok or result.value is None:
        raise Problem.domain_rejection(result.error)          # 422 problem document (EIT005)
    return <Noun>Out(...)                                     # result type -> pydantic out
```

The rules this encodes:

- **One router per slice**, `prefix="/<slice>"`, `tags=["<slice>"]`. The prefix is the slice name; the path is the verb or noun of the use case.
- **Dispatch, never call.** The route builds a message from the contract and hands it to `request.app.state.bus`. It never imports a handler, an engine or anything from `internal/` of another slice (EIT001). `grep <Message>` therefore finds every caller of that use case.
- **Validate shape at the edge, meaning in the handler.** Request models subclass `StrictRequest` (unknown fields are rejected), text fields use `text(max)` (length bounds, no control characters), and a list of texts gets a `check_total_chars` model validator so one call cannot ask for unbounded work. Whether the *content* makes sense — a blank question, an empty passage — is the handler's `Result.failure`, not a pydantic rule. A validation failure names the field; a domain rejection names the reason.
- **Convert at the edge.** Pydantic models are the HTTP representation and stay in `routes.py`. Messages and result types are frozen dataclasses from `contract/`; tuples in, lists out. The route is the only place that knows both.
- **Status codes are decided here, not in the handler.** `Result.ok=False` maps to `Problem.domain_rejection(result.error)`: a `422` RFC 9457 problem document with the handler's reason as `detail`. A missing entity would be `Problem(404, ...)`. Request-body validation failures are FastAPI's own `422`, converted to the same shape. The handler never raises for domain reasons; the route never raises `HTTPException` (EIT005). Everything about errors is in `returning-errors.md`.
- **`response_model` is always set.** It is the contract of the route for OpenAPI consumers, the same way `contract/` is the contract of the slice for code consumers.

## Case A — worked example: `POST /chunking/count`

Say the API should tell a caller how many model tokens a text costs. The chunking contract already has `CountTokens(text) -> int`; only `SplitText` has a route. The whole change is in two files.

```python
# src/slices/chunking/internal/routes.py  (add to the existing file)
class CountRequest(BaseModel):
    text: str = Field(min_length=1, max_length=200_000)


class CountOut(BaseModel):
    tokens: int


@router.post("/count", response_model=CountOut)
def count(body: CountRequest, request: Request) -> CountOut:
    """How many model tokens the text costs, counted with the model's own tokenizer."""
    result = request.app.state.bus.dispatch(CountTokens(text=body.text))
    if not result.ok or result.value is None:
        raise Problem.domain_rejection(result.error)
    return CountOut(tokens=result.value)
```

```python
# tests/api/test_chunking.py  — against the real handler over a fake tokenizer, never the model
def test_count_route_counts_with_the_tokenizer() -> None:
    app = create_app()
    app.state.bus.register(CountTokens, CountTokensHandler(FakeTokenizer()), replace=True)
    r = TestClient(app).post("/chunking/count", json={"text": "one two three"})
    assert r.status_code == 200 and r.json() == {"tokens": 3}
```

Then: `pytest tests/api`, `eitri check --root .`, `heimdall map --root .` (so the route inventory in the `AGENTS.md` files picks it up).

## Case B — worked example: `POST /embeddings/similarity`, a message the contract lacks

Say the API should score how close a question is to each of a handful of candidate texts, without the caller handling vectors. No message in the embeddings contract does that, so the **contract changes first, additively**.

1. **Check the fan-in** of the contract in the root `AGENTS.md` slice table. Below 10: add freely. 10 or more: the contract is frozen — additive only, and Heimdall will warn you in-loop when you edit it. Never change or remove an existing member of a frozen contract; add the new one and leave the old.

2. **Add the message and its result type** — kernel types, stdlib, same-contract types only (EIT003). A message is a frozen dataclass whose docstring says what comes back:

```python
# src/slices/embeddings/contract/queries.py
@dataclass(frozen=True)
class RankCandidates(Query):
    """Score every candidate against the question -> ``Ranking``, best first."""

    question: str
    candidates: tuple[str, ...]
```

```python
# src/slices/embeddings/contract/results.py
@dataclass(frozen=True)
class Match:
    source_index: int
    text: str
    score: float
    chunk_index: int
    chunk_count: int


@dataclass(frozen=True)
class Ranking:
    model: str
    matches: tuple[Match, ...]
```

   Export both from `contract/__init__.py`. Use `Query` for a read or a computation, `Command` for something that changes state.

3. **Write the handler** in `internal/handlers.py`: one callable per message, `__call__(self, q: RankCandidates) -> Result[Ranking]`. Validate the domain preconditions and return `Result.failure("empty question")` — never raise for a domain reason, never import FastAPI. The handler is built around the engine (and the bus, if it dispatches other slices' messages — Case C).

4. **Register it** in `internal/module.py` with one line: `bus.register(RankCandidates, RankCandidatesHandler(engine, bus))`. Two handlers for one message is an error; the bus refuses.

5. **Add the route** exactly as in the shape above, with `SimilarityRequest` / `SimilarityOut` models in `routes.py`.

6. **Test at two levels**: the handler directly (construct it over a fake engine, call it with a message, assert on the `Result`) and the route (`tests/api/test_embeddings.py`, through the `fake` fixture, which registers the real handlers over fakes with `bus.register(..., replace=True)`). Assert the domain rejection as a problem document: status `422`, media type `application/problem+json`, `type` `urn:pfa-api:problem:domain-rejection`.

7. `eitri check --root .` — watch the EIT101 line for embeddings: a bigger contract raises the working set of **every consumer** of embeddings, not just embeddings itself. If a consumer tips over its budget, the new message may belong in a smaller contract.

## Case C — the handler needs another slice

This is how `embeddings` uses `chunking` today: a passage longer than the model's window must be split before it is embedded. The route stays in `embeddings`; its *handler* dispatches `SplitText` and `CountTokens` from `slices.chunking.contract` on the same bus; `slice.json` declares `"chunking"`. The route itself never learns the dependency exists.

- Declare the dependency in `slice.json` **before** importing (EIT004), then `heimdall map --root .`.
- Import only `slices.<dep>.contract` (EIT001). If the message you need is not in the dependency's contract, that is a Case B change **in the dependency** — do it there, through its own contract, with its own tests.
- Dispatch from the handler, not from the route. The handler receives the bus in its constructor (`module.py` passes it); the route only knows its own slice's message.
- A dependency's failure inside your handler is a bug, not a domain outcome: `embeddings` raises `RuntimeError` if `SplitText` fails, because text that passed its own validation cannot be unsplittable. Return `Result.failure` only for what *your* domain rejects.

## Service-level routes

Health, readiness, build info and similar cross-cutting endpoints go in `src/pfa_api/routes/<name>.py` with their own `APIRouter`, mounted in `create_app()`. They may dispatch on the bus but must not import any slice's `internal/` — `tests/api/test_architecture.py` enforces that only `composition.py` does.

## Checklist before you finish

- [ ] Route is in the slice that owns the use case; one `APIRouter` per slice with `prefix="/<slice>"`.
- [ ] Pydantic models live in `routes.py`, subclass `StrictRequest`, bound every text with `text(max)` and every list with `max_length` + `check_total_chars`; `contract/` still imports nothing from fastapi or pydantic.
- [ ] The route dispatches a contract message on `request.app.state.bus`; no handler, engine or foreign `internal/` is imported.
- [ ] `response_model` set; `Result.ok=False` mapped to `Problem.domain_rejection(result.error)`; no `HTTPException` anywhere in the slice (EIT005).
- [ ] New messages and result types are additive; frozen contracts untouched except for additions; one `bus.register` line per new message.
- [ ] Tests in `tests/api/` for the happy path and for the domain rejection, over fakes registered with `replace=True` — never the real model.
- [ ] `pytest` green, `eitri check --root .` green, `heimdall map --root .` run so the route inventories are current.
