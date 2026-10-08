# Returning errors

| ✅ Do | ❌ Don't |
|---|---|
| Return `Result.failure(...)` for a handler's domain rejection. | Make handlers depend on HTTP or raise HTTP errors. |
| Raise `Problem` in routes and keep one consistent error format. | Raise `HTTPException` anywhere, or any HTTP error inside a feature. |
| Use a stable `type` and fixed `title`; write safe, human-readable `detail`. | Expose request bodies, secrets, file paths, or stack traces. |
| Assert status, media type, and problem `type` in tests. | Check only the status code and miss a broken response format. |

⚠️ **Check:** framework-generated 404/405 exceptions are converted by the shared error handler. That does not permit `HTTPException` in route code.

Every error the PFA API sends is an **RFC 9457 problem document**: status code in the header, `Content-Type: application/problem+json`, and a body with the five standard members plus optional extensions.

```json
{
  "type": "urn:pfa-api:problem:domain-rejection",
  "title": "Request rejected",
  "status": 422,
  "detail": "empty question",
  "instance": "/embeddings/query"
}
```

| Member | What it is | Who sets it |
|---|---|---|
| `type` | URI naming the *kind* of problem. `about:blank` means "just the HTTP status". | the `Problem` you raise (defaults to `about:blank`) |
| `title` | Short summary of the kind — the same for every occurrence of that `type`. | `Problem` (defaults to the status phrase) |
| `status` | The HTTP status code, repeated in the body. | `Problem` |
| `detail` | What went wrong *this time*, for a human. Text, never an object. | `Problem` — usually `result.error` |
| `instance` | URI reference for this occurrence: the request path. | `pfa.api.problems`, always |
| anything else | Extension members (`errors` on validation problems, `retry_after`, …). | keyword arguments to `Problem` |

The machinery lives in **`src/pfa/api/problems.py`** and is installed once, in `create_app()`, by `install_problem_details(app)`. It covers the following sources so a client receives one consistent format:

| Source | Status | `type` | `title` |
|---|---|---|---|
| `raise Problem.domain_rejection(result.error)` in a route | 422 | `urn:pfa-api:problem:domain-rejection` | Request rejected |
| `raise Problem(status, detail, type=…, title=…, **ext)` in a route | yours | yours | yours |
| Request validation — unknown field, length or count out of bounds, control characters, total text over the per-request budget (`pfa.api.validation`) | 422 | `urn:pfa-api:problem:validation-error` | Request validation failed — plus `errors: [{loc, msg, type}]`, never the input |
| Starlette/FastAPI `HTTPException` (unknown path, wrong method, a dependency) | as raised | `about:blank` | the status phrase |
| any unhandled exception | 500 | `about:blank` | Internal Server Error — **no `detail`**: internals never leak |
| `GET /ready` while components load | 503 | `urn:pfa-api:problem:not-ready` | Service not ready — `components` extension, `Retry-After: 5` |
| the inference gate did not open in `PFA_EMBED_QUEUE_TIMEOUT` (`EngineBusy`, declared in embeddings' `contract/`, caught by its route) | 503 | `urn:pfa-api:problem:model-busy` | Embedding model busy — `retry_after` extension and header |

## The shape every route follows

```python
# src/pfa/api/routes/<feature>.py
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from pfa.api.problems import Problem, domain_rejections   # the edge's error type — never imported by a feature
from pfa.features.<feature>.contract import <Message>


@router.post("/<verb>", summary="...", response_model=<Noun>Out, responses={422: domain_rejections("<the handler's reason>")})
def <verb>(body: <Verb>Request, request: Request) -> <Noun>Out:
    result = request.app.state.bus.dispatch(<Message>(...))
    if not result.ok or result.value is None:
        raise Problem.domain_rejection(result.error)   # 422, typed, detail = the handler's reason
    return <Noun>Out(...)
```

The rules this encodes:

- ✅ **Handlers never know about HTTP.** They return `Result.failure("empty question")`; the *route* turns that into a problem. The handler's error string is the `detail`, so write it for the client, not for the log. A feature may not even import the framework or the API (EIT006), so there is no way to answer HTTP from inside one.
- ✅ **Infrastructure failures are exceptions in the contract.** When a feature cannot serve a valid request right now (a saturated model), it raises a plain exception declared in its `contract/` — embeddings' `EngineBusy` — and the route maps it: `raise Problem(503, str(e), type=MODEL_BUSY, headers={"Retry-After": ...})`. The exception carries the facts; the route decides the wire.
- ✅ **The route decides the status; `Problem` decides the shape.** A domain rejection is `Problem.domain_rejection(...)`. A missing entity is `Problem(404, f"no document {id}")`. A conflict is `Problem(409, "...", type="urn:pfa-api:problem:<kind>", title="...")`.
- ❌ **Never `HTTPException`.** Eitri rule **EIT005** fails `eitri check` on any `HTTPException` imported from `fastapi` or `starlette` under `src/pfa/` outside the features (except in `api/problems.py`, where the conversion lives). The conversion handler for the framework's own exceptions exists so 404/405 come out right — not as an escape hatch for routes.
- ✅ **New problem types are URNs** of the form `urn:pfa-api:problem:<kebab-kind>`, declared as constants in `pfa/api/problems.py` alongside the existing problem types, with a fixed `title`. One `type` ⇒ one `title`; `detail` varies per occurrence.
- ❌ **`detail` is text and must not leak.** Do not echo request bodies, file paths or stack traces. Structured information goes in an extension member.
- ✅ **OpenAPI follows, with examples.** `install_problem_details` rewrites the document: the `422` of every operation with a body is `ProblemDetails` as `application/problem+json`, showing a validation example (built from the body's required fields) next to the route's domain rejections, and every operation gets a `default` problem response. A route declares its rejections with `responses={422: domain_rejections("empty question")}`, one reason per `Result.failure` its handler may return, and any other problem it raises with `problem_response(description, {name: Problem(...)}, headers=...)`, as embeddings does for its 503 busy response. Examples are rendered by `Problem.to_body`, the code that answers requests, and `tests/api/test_openapi.py` compares them with the wire.

## Testing an error

Assert the three things a client relies on: the status, the media type, and the `type`. The `detail` is the handler's message.

```python
# tests/api/test_<feature>.py
def test_blank_question_is_a_domain_rejection(fake) -> None:
    client, _ = fake
    r = client.post("/embeddings/query", json={"question": "   "})
    assert r.status_code == 422
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["type"] == "urn:pfa-api:problem:domain-rejection"
    assert r.json()["detail"] == "empty question"
```

`tests/api/test_problems.py` covers the shared machinery (validation, 404/405, 500, extensions, OpenAPI) once for the whole service; feature tests only need the feature's own rejections.

## Checklist

✅ **Verify each item.** Leave boxes unchecked until the corresponding behavior has been confirmed.

- [ ] Route raises `Problem` (or `Problem.domain_rejection`), never `HTTPException` — `eitri check --root .` is green on EIT005.
- [ ] A new `type` is a `urn:pfa-api:problem:…` constant in `pfa/api/problems.py` with one fixed `title`.
- [ ] `detail` is a human sentence with nothing sensitive in it; structured data is an extension member.
- [ ] The feature test asserts status, `application/problem+json`, and `type`.
- [ ] The route's `responses=` names each rejection reason (`domain_rejections`) and any other problem it raises (`problem_response`); `tests/api/test_openapi.py` is green.
