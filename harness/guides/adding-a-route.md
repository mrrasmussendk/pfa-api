# Adding a route

✅ **Do** marks the expected approach. ❌ **Don't** marks a prohibited shortcut. ⚠️ **Check** marks a condition to resolve before continuing.

| ✅ Do | ❌ Don't |
|---|---|
| Put the route in `src/pfa/api/routes/<feature>.py`, the feature's HTTP edge. | Put a route, a pydantic model or a status code inside `src/pfa/features/`. |
| Validate the HTTP request, dispatch a contract message, and map the result. | Call handlers or engines directly from the route. |
| Consume the feature through its contract, exactly as another feature would. | Import the feature's `internal/` or its `module.py` from a route. |
| Test through fake components registered on the bus. | Download or load the real model in ordinary route tests. |

A route is a feature's HTTP edge, and it lives **in the entry point**, not in the feature: `src/pfa/api/routes/<feature>.py`, one module per feature, one `APIRouter` with `prefix="/<feature>"`, mounted through `ROUTERS` in `src/pfa/api/routes/__init__.py`. Service-level routes that belong to no feature (health, metadata) live beside them in `src/pfa/api/routes/health.py`.

A route never does work itself. It converts the HTTP request into a **message** from the feature's contract, dispatches it on the kernel `Bus`, and converts the `Result` back into HTTP. The handler behind the message is what knows the domain, and the feature knows nothing of HTTP or of the API (EIT006): the same contract message serves a route today and another feature tomorrow. Heimdall treats `pfa/api/routes/<feature>.py` as part of the feature, so editing it is not a cross-feature change. Before you start, decide which of the three cases you are in. They have different working sets.

| Case | You need | Working set |
|---|---|---|
| **A. The contract already has the message** | request/response models + a route | `src/pfa/api/routes/<feature>.py`, `src/pfa/features/<feature>/contract/`, the feature's `AGENTS.md`, `tests/api/test_<feature>.py` |
| **B. The message does not exist yet** | first a contract change, then a handler, then the route | A + `contract/queries.py` (or `commands.py`), `contract/results.py`, `internal/handlers.py`, `module.py` |
| **C. The handler needs another feature's capability** | that feature's **contract** and a declared dependency | A or B + `features/<dep>/contract/`, `feature.json` |

⚠️ If none fits — the use case is a new concept with its own data and rules — follow [Adding a feature](../../AGENTS.md#adding-a-feature).

## The shape every route follows

```python
# src/pfa/api/routes/<feature>.py
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from pfa.api.problems import Problem, domain_rejections  # RFC 9457 errors, and how a route documents its own
from pfa.api.validation import StrictRequest, text, with_example   # request hygiene, and the one example /docs shows
from pfa.features.<feature>.contract import <Message>    # the feature's contract: the message to dispatch — nothing else

TAG = {"name": "<feature>", "description": "..."}             # the group's blurb in /docs; listed in routes/__init__.py
router = APIRouter(prefix="/<feature>", tags=["<feature>"])   # exactly one router per feature


class <Verb><Noun>Request(StrictRequest):   # unknown fields are a 422; models never appear in a feature (EIT006)
    model_config = with_example({"<field>": "..."})   # what /docs offers under Try it out

    <field>: text(MAX_CHARS) = Field(description="...")   # length-bounded, no control characters


class <Noun>Out(BaseModel):
    model_config = with_example({...})   # a real response, captured once

    ...


@router.post(
    "/<verb>",
    summary="<what the operation does, one line>",
    response_model=<Noun>Out,
    response_description="<what a 200 carries>",
    responses={422: domain_rejections("/<feature>/<verb>", "<each reason the handler may give>")},
)
def <verb>(body: <Verb><Noun>Request, request: Request) -> <Noun>Out:
    result = request.app.state.bus.dispatch(<Message>(...))   # pydantic in -> contract message
    if not result.ok or result.value is None:
        raise Problem.domain_rejection(result.error)          # 422 problem document (EIT005)
    return <Noun>Out(...)                                     # result type -> pydantic out
```

The rules this encodes:

- ✅ **One router per feature**, `prefix="/<feature>"`, `tags=["<feature>"]`, in a module named after the feature. `heimdall map` reads that file to inventory the feature's routes.
- ✅ **Dispatch, never call.** The route builds a message from the contract and hands it to `request.app.state.bus`. It never imports a handler, an engine, `internal/` or `module.py` (EIT001). `grep <Message>` therefore finds every caller of that use case, routes and features alike.
- ✅ **Validate shape at the edge, meaning in the handler.** Request models subclass `StrictRequest` (unknown fields are rejected), text fields use `text(max)` (length bounds, no control characters), and a list of texts gets a `check_total_chars` model validator so one call cannot ask for unbounded work. Whether the *content* makes sense — a blank question, an empty passage — is the handler's `Result.failure`, not a pydantic rule. A validation failure names the field; a domain rejection names the reason.
- ✅ **Convert at the edge.** Pydantic models are the HTTP representation and stay in `pfa/api/routes/`. Messages and result types are frozen dataclasses from `contract/`; tuples in, lists out. The route is the only place that knows both.
- ✅ **Status codes are decided here, not in the handler.** `Result.ok=False` maps to `Problem.domain_rejection(result.error)`: a `422` RFC 9457 problem document with the handler's reason as `detail`. A missing entity would be `Problem(404, ...)`. Request-body validation failures are FastAPI's own `422`, converted to the same shape. The handler never raises for domain reasons; the route never raises `HTTPException` (EIT005). An **infrastructure** failure the feature cannot express as a `Result` (a saturated model) is a plain exception declared in the feature's `contract/` — `EngineBusy` in embeddings — that the route catches and maps to a `Problem`. See [Returning errors](returning-errors.md).
- ✅ **`response_model` is always set.** It is the contract of the route for OpenAPI consumers, the same way `contract/` is the contract of the feature for code consumers.
- ✅ **The document is part of the route.** `summary=` says what the operation does in one line, every field has a `description`, every request and response model carries one real example as `model_config = with_example(...)` (what `/docs` offers under *Try it out*; capture it from a real exchange, do not invent numbers), and `responses={422: domain_rejections(...)}` names each reason the handler may reject with. The module's `TAG` describes the group and is listed in `TAGS` in `routes/__init__.py`. `tests/api/test_openapi.py` validates every example against its model and every error example against the wire. See [Returning errors](returning-errors.md) for documenting any other problem a route raises.

## Case A — worked example: `POST /chunking/count`

Say the API should tell a caller how many model tokens a text costs. The chunking contract already has `CountTokens(text) -> int`; only `SplitText` has a route. The whole change is in two files, and neither is inside the feature.

```python
# src/pfa/api/routes/chunking.py  (add to the existing file)
class CountRequest(StrictRequest):
    model_config = with_example({"text": "Its contract is the only door."})

    text: Text = Field(description="The text to count")  # ``Text`` is the module's ``text(MAX_TEXT_CHARS)``


class CountOut(BaseModel):
    model_config = with_example({"tokens": 8})

    tokens: int = Field(description="Model tokens, counted with the model's own tokenizer")


@router.post(
    "/count",
    summary="Count the model tokens in a text",
    response_model=CountOut,
    responses={422: domain_rejections("/chunking/count", "empty text")},
)
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

⚠️ This is a worked example of how the existing similarity use case was added. `RankCandidates` and `/embeddings/similarity` already exist; do not add duplicates. For a genuinely new capability, change the **contract first, additively**, following the same sequence.

1. **Check the fan-in** of the contract in the root `AGENTS.md` feature table. Below 10: add freely. 10 or more: the contract is frozen — additive only, and Heimdall will warn you in-loop when you edit it. Never change or remove an existing member of a frozen contract; add the new one and leave the old.

2. **Add the message and its result type** — kernel types, stdlib, same-contract types only (EIT003). A message is a frozen dataclass whose docstring says what comes back:

```python
# src/pfa/features/embeddings/contract/queries.py
@dataclass(frozen=True)
class RankCandidates(Query):
    """Score every candidate against the question -> ``Ranking``, best first."""

    question: str
    candidates: tuple[str, ...]
```

```python
# src/pfa/features/embeddings/contract/results.py
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

3. **Write the handler** in `internal/handlers.py`: one callable per message, `__call__(self, q: RankCandidates) -> Result[Ranking]`. Validate the domain preconditions and return `Result.failure("empty question")` — never raise for a domain reason, never import FastAPI or the API (EIT006). The handler is built around the engine (and the bus, if it dispatches other features' messages — Case C).

4. **Register it** in the feature's `module.py` with one line: `bus.register(RankCandidates, RankCandidatesHandler(engine, bus))`. Two handlers for one message is an error; the bus refuses.

5. **Add the route** exactly as in the shape above, with `SimilarityRequest` / `SimilarityOut` models in `src/pfa/api/routes/embeddings.py`.

6. **Test at two levels**: the handler directly (construct it over a fake engine, call it with a message, assert on the `Result`) and the route (`tests/api/test_embeddings.py`, through the `fake` fixture, which registers the real handlers over fakes with `bus.register(..., replace=True)`). Assert the domain rejection as a problem document: status `422`, media type `application/problem+json`, `type` `urn:pfa-api:problem:domain-rejection`.

7. `eitri check --root .` — watch the EIT101 line for embeddings: a bigger contract raises the working set of **every consumer** of embeddings, not just embeddings itself. If a consumer tips over its budget, the new message may belong in a smaller contract.

## Case C — the handler needs another feature

This is how `embeddings` uses `chunking` today: a passage longer than the model's window must be split before it is embedded. The route stays in `pfa/api/routes/embeddings.py` and knows only embeddings' contract; embeddings' *handler* dispatches `SplitText` and `CountTokens` from `pfa.features.chunking.contract` on the same bus; `feature.json` declares `"chunking"`. The route never learns the dependency exists.

- Declare the dependency in `feature.json` **before** importing (EIT004), then `heimdall map --root .`.
- Import only `pfa.features.<dep>.contract` (EIT001). If the message you need is not in the dependency's contract, that is a Case B change **in the dependency** — do it there, through its own contract, with its own tests.
- Dispatch from the handler, not from the route. The handler receives the bus in its constructor (`module.py` passes it); the route only knows its own feature's message.
- A dependency's failure inside your handler is a bug, not a domain outcome: `embeddings` raises `RuntimeError` if `SplitText` fails, because text that passed its own validation cannot be unsplittable. Return `Result.failure` only for what *your* domain rejects.

## Service-level routes

Health, readiness, build info and similar cross-cutting endpoints go in `src/pfa/api/routes/<name>.py` with their own `APIRouter`, listed in `ROUTERS`. They may dispatch on the bus but, like every route, must not import any feature's `internal/` or `module.py` — Eitri (EIT001) and `tests/api/test_architecture.py` both enforce that only `pfa/application.py` plugs modules in.

## Checklist before you finish

✅ **Verify each item.** The unchecked boxes below are requirements, not a record of checks already run.

- [ ] Route is in `src/pfa/api/routes/<feature>.py`; one `APIRouter` per feature with `prefix="/<feature>"`; the router is in `ROUTERS`.
- [ ] Pydantic models live in that routes module, subclass `StrictRequest`, bound every text with `text(max)` and every list with `max_length` + `check_total_chars`; nothing under `src/pfa/features/` imports fastapi, starlette, pydantic, `pfa.application` or `pfa.api`.
- [ ] The route dispatches a contract message on `request.app.state.bus`; no handler, engine, `internal/` or `module.py` is imported.
- [ ] `response_model` set; `Result.ok=False` mapped to `Problem.domain_rejection(result.error)`; no `HTTPException` anywhere (EIT005).
- [ ] `summary=` written, every field described, request and response models carry a real example, `responses={422: domain_rejections(...)}` names the handler's reasons; `tests/api/test_openapi.py` green.
- [ ] New messages and result types are additive; frozen contracts untouched except for additions; one `bus.register` line per new message.
- [ ] Tests in `tests/api/` for the happy path and for the domain rejection, over fakes registered with `replace=True` — never the real model.
- [ ] `pytest` green, `eitri check --root .` green, `heimdall map --root .` run so the route inventories are current.
