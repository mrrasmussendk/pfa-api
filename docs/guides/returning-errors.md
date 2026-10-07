# Returning errors

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
| `instance` | URI reference for this occurrence: the request path. | `http_common`, always |
| anything else | Extension members (`errors` on validation problems, `retry_after`, …). | keyword arguments to `Problem` |

The machinery lives in **`src/http_common/problems.py`** and is installed once, in `create_app()`, by `install_problem_details(app)`. It covers four sources so a client never meets a second format:

| Source | Status | `type` | `title` |
|---|---|---|---|
| `raise Problem.domain_rejection(result.error)` in a route | 422 | `urn:pfa-api:problem:domain-rejection` | Request rejected |
| `raise Problem(status, detail, type=…, title=…, **ext)` in a route | yours | yours | yours |
| Request validation — unknown field, length or count out of bounds, control characters, total text over the per-request budget (`http_common.validation`) | 422 | `urn:pfa-api:problem:validation-error` | Request validation failed — plus `errors: [{loc, msg, type}]`, never the input |
| Starlette/FastAPI `HTTPException` (unknown path, wrong method, a dependency) | as raised | `about:blank` | the status phrase |
| any unhandled exception | 500 | `about:blank` | Internal Server Error — **no `detail`**: internals never leak |
| `GET /ready` while components load | 503 | `urn:pfa-api:problem:not-ready` | Service not ready — `components` extension, `Retry-After: 5` |
| the inference gate did not open in `PFA_EMBED_QUEUE_TIMEOUT` | 503 | `urn:pfa-api:problem:model-busy` | Embedding model busy — `retry_after` extension and header |

## The shape every route follows

```python
# src/slices/<slice>/internal/routes.py
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from http_common import Problem                # the HTTP-edge helper package — not the kernel
from slices.<slice>.contract import <Message>


@router.post("/<verb>", response_model=<Noun>Out)
def <verb>(body: <Verb>Request, request: Request) -> <Noun>Out:
    result = request.app.state.bus.dispatch(<Message>(...))
    if not result.ok or result.value is None:
        raise Problem.domain_rejection(result.error)   # 422, typed, detail = the handler's reason
    return <Noun>Out(...)
```

The rules this encodes:

- **Handlers never know about HTTP.** They return `Result.failure("empty question")`; the *route* turns that into a problem. The handler's error string is the `detail`, so write it for the client, not for the log.
- **The route decides the status; `Problem` decides the shape.** A domain rejection is `Problem.domain_rejection(...)`. A missing entity is `Problem(404, f"no document {id}")`. A conflict is `Problem(409, "...", type="urn:pfa-api:problem:<kind>", title="...")`.
- **Never `HTTPException` inside a slice.** Eitri rule **EIT005** fails `eitri check` on any `HTTPException` imported from `fastapi` or `starlette` under `src/slices/`. The conversion handler for the framework's own exceptions exists so 404/405 come out right — not as an escape hatch for routes.
- **New problem types are URNs** of the form `urn:pfa-api:problem:<kebab-kind>`, declared as constants in `http_common/problems.py` next to the two that exist, with a fixed `title`. One `type` ⇒ one `title`; `detail` varies per occurrence.
- **`detail` is text and must not leak.** Do not echo request bodies, file paths or stack traces. Structured information goes in an extension member.
- **OpenAPI follows.** `install_problem_details` rewrites the document: the `422` response of every operation with a body is `ProblemDetails` as `application/problem+json`, and every operation gets a `default` problem response. You do not add `responses=` to routes for errors.

## Testing an error

Assert the three things a client relies on: the status, the media type, and the `type`. The `detail` is the handler's message.

```python
# tests/api/test_<slice>.py
def test_blank_question_is_a_domain_rejection(fake) -> None:
    client, _ = fake
    r = client.post("/embeddings/query", json={"question": "   "})
    assert r.status_code == 422
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["type"] == "urn:pfa-api:problem:domain-rejection"
    assert r.json()["detail"] == "empty question"
```

`tests/api/test_problems.py` covers the shared machinery (validation, 404/405, 500, extensions, OpenAPI) once for the whole service; slice tests only need the slice's own rejections.

## Checklist

- [ ] Route raises `Problem` (or `Problem.domain_rejection`), never `HTTPException` — `eitri check --root .` is green on EIT005.
- [ ] A new `type` is a `urn:pfa-api:problem:…` constant in `http_common/problems.py` with one fixed `title`.
- [ ] `detail` is a human sentence with nothing sensitive in it; structured data is an extension member.
- [ ] The slice test asserts status, `application/problem+json`, and `type`.
