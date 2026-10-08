# RFC 0001 — Logging

**Status:** proposed · **Owner:** Marc Rasmussen · **Date:** 2026-10-08 · **Scope:** `src/pfa/api`, `src/pfa/features/embeddings/internal/e5_engine.py`, `docs/service.md`

## Why

The service emits two log lines in its whole life, both during warm-up. An operator cannot answer the questions a production incident starts with:

- *Which request was slow, and where did the time go — queueing for the model or encoding?*
- *What did the 500 at 14:02 look like?* The handler answers `500` with no detail (correct: internals must not leave on the wire), but it does not log either. The traceback reaches the console only because Starlette re-raises and uvicorn prints it — with no path, no method, no way to tie it to a client's report.
- *Is the model loaded, on which device, and how long did the load take?*
- *How much work is this instance doing?* Requests, texts, chunks, encode time.

Uvicorn's access log does not help: it has no request id, no duration and no knowledge of the model. The case this service answers names logging next to validation and error handling; those two are strong, this one is not.

## Goals

1. **Every request leaves one line** with method, path, status, duration and a request id, from our code, not uvicorn's.
2. **Every inference leaves one line** with how much work it was and how long it took, split into queue wait and encode time.
3. **A request id ties the two together** and reaches the client, so a bug report can quote it and the operator can `grep` it.
4. **Errors are logged where they are answered.** An unhandled exception is logged with its traceback and the request id; a `503 model-busy` is logged as a warning.
5. **One format switch:** human-readable text for a terminal, one JSON object per line for a log shipper. Stdlib only.
6. **Nothing the user sent is logged.** Passages are up to 200 kB and may be personal data. Sizes and counts only, never text.

## Non-goals

- Tracing (OpenTelemetry spans). The request id is laid out so a `traceparent` can replace it later without changing the lines.
- Metrics (`/metrics`, Prometheus). The log lines carry the numbers a first dashboard needs; a metrics endpoint is a separate RFC.
- Log shipping, rotation, retention. The service writes to stdout; the platform does the rest.
- A logging library (`structlog`, `python-json-logger`). The stdlib does what is needed in about a hundred lines, and the image stays small.

## Design

### Where it lives, and the walls

| Piece | Module | Why there |
|---|---|---|
| Configuration, formatter, request-id filter | `src/pfa/api/logging.py` (new) | The entry point owns process concerns. May import Starlette. |
| Request-id middleware and the request line | `src/pfa/api/logging.py` | ASGI middleware is HTTP; it belongs in `pfa/api` (EIT006). |
| Inference and load lines | `src/pfa/features/embeddings/internal/e5_engine.py` | The engine knows the numbers. It uses `logging.getLogger` only — stdlib, framework-free, so the feature stays inside its walls. |
| Error lines | `src/pfa/api/problems.py` (`_on_unhandled`), `src/pfa/api/routes/embeddings.py` (`_dispatch`) | Logged where the error is turned into a response. |

The request id travels in a `contextvars.ContextVar` set by the middleware. A `logging.Filter` installed on the handler copies it onto every record as `record.request_id`. The engine never sees the middleware or the context variable; its records get the id because the filter runs on the handler, in whichever thread the record was made. FastAPI runs sync routes on a worker thread through anyio, which copies the context into the thread, so the id is present there. A test asserts it rather than assuming it.

### Configuration

`configure_logging()` in `pfa/api/logging.py` is called once from `create_app()` and is idempotent (guarded like `install_problem_details`), so tests that build many apps configure once.

| Setting | Default | Meaning |
|---|---|---|
| `PFA_LOG_LEVEL` | `info` | Level for the `pfa` loggers and uvicorn's. Already exists; keeps its name. |
| `PFA_LOG_FORMAT` | `text` (`json` in the image) | `text`: one readable line. `json`: one object per line. |
| `PFA_ACCESS_LOG` | `0` (was `1`) | Uvicorn's access log. Off by default because the request line replaces it; `1` turns it back on for comparison. |

The root logger gets one `StreamHandler` on stdout with the chosen formatter and the request-id filter. Uvicorn's `uvicorn`, `uvicorn.error` and `uvicorn.access` loggers are pointed at the same handler (their own handlers removed, `propagate = True`) so the process writes one stream in one format. `python -m pfa.api` passes `log_config=None` to uvicorn so it does not install its own.

### The request id

Middleware `RequestId` (pure ASGI, not `BaseHTTPMiddleware`, so streaming and background tasks are unaffected):

1. Read `X-Request-ID` from the request. Accept it when it is 1–128 characters of printable ASCII without spaces; otherwise ignore it. A client cannot inject newlines or control characters into the logs.
2. Otherwise generate `uuid4().hex`.
3. Set the context variable for the duration of the request; echo the id in the `X-Request-ID` response header on every response, including problem documents.
4. `Problem.to_body` adds `request_id` as an extension member on every problem document. A client that gets a `500` has something to quote, and the OpenAPI example shows the field.

### The request line

The same middleware records `time.perf_counter()` at start and logs on the response's completion, at the `pfa.api.request` logger:

| Field | Example | Note |
|---|---|---|
| `method`, `path` | `POST`, `/embeddings/query` | Path only, never the query string (it could carry text). |
| `status` | `200` | |
| `ms` | `41.3` | Wall time, start of request to last byte sent. |
| `request_id` | `3f2c…` | From the filter. |
| `client` | `10.0.0.5` | The address uvicorn resolved, honouring `PFA_FORWARDED_ALLOW_IPS`. |

Level: `INFO` for everything except `/health` and `/ready`, which log at `DEBUG` so probes every few seconds do not drown the stream. A `5xx` logs at `ERROR`, a `4xx` at `INFO` (a validation failure is the client's problem and the status says so).

### The inference line

`E5Engine._encode` measures the gate wait and the encode separately and logs at `pfa.embeddings.engine`:

| Field | Example | Note |
|---|---|---|
| `side` | `passage` | `query` or `passage`. |
| `texts` | `12` | Chunks encoded in this call. |
| `chars` | `18 402` | Sum of lengths. Never the text. |
| `wait_ms` | `0.1` | Time blocked on `PFA_EMBED_CONCURRENCY`. Rising wait with flat encode time means "add a replica". |
| `encode_ms` | `388.0` | Time inside `model.encode`. |
| `device` | `cpu` | |

The one-time load logs `model loaded` with `model_id`, `device`, `max_seq_length` and `load_ms` at `INFO`. A gate timeout logs `WARNING` with `wait_ms` before raising `EngineBusy`; the route then answers `503`.

### Errors

- `_on_unhandled` calls `log.exception("unhandled error", extra={"method": ..., "path": ...})` before answering. The traceback goes to the log, the request id goes to both the log and the problem document, and the wire still says only `500`.
- `_dispatch` in the embeddings routes logs `WARNING` with `retry_after` when it maps `EngineBusy` to `503`.
- Warm-up keeps its two lines; `warm-up complete` gains `load_ms`.
- A `4xx` is not logged separately. The request line carries the status, and the problem document already told the client why.

### The two formats

Both formatters read the same records; extras are passed with `extra={...}` and the formatter picks them up by excluding the standard `LogRecord` attributes.

Text, for a terminal:

```
14:02:03.117 INFO  pfa.api.request     rid=3f2c9a… POST /embeddings/passages 200 ms=412.5 client=127.0.0.1
14:02:03.116 INFO  pfa.embeddings.engine rid=3f2c9a… encode side=passage texts=12 chars=18402 wait_ms=0.1 encode_ms=388.0 device=cpu
14:02:07.900 ERROR pfa.api.problems    rid=8b11e0… unhandled error method=POST path=/embeddings/query
Traceback (most recent call last):
  ...
```

JSON, for a shipper, one object per line:

```json
{"ts": "2026-10-08T14:02:03.117Z", "level": "INFO", "logger": "pfa.api.request", "msg": "request", "request_id": "3f2c9a…", "method": "POST", "path": "/embeddings/passages", "status": 200, "ms": 412.5, "client": "127.0.0.1"}
{"ts": "2026-10-08T14:02:07.900Z", "level": "ERROR", "logger": "pfa.api.problems", "msg": "unhandled error", "request_id": "8b11e0…", "method": "POST", "path": "/embeddings/query", "exc": "Traceback (most recent call last): ..."}
```

Timestamps are UTC with milliseconds in both. `exc` is the formatted traceback as one string, so a line is always one JSON object.

## Tests (`tests/api/test_logging.py`)

Each goal has a test, over the fake engine and `caplog`; nothing loads the model.

1. A request without `X-Request-ID` gets one back and the request line carries the same id.
2. A valid client id is echoed and used; an id with a newline or 200 characters is replaced.
3. The request line has method, path, status and a numeric `ms`; `/health` logs at `DEBUG`, a `500` at `ERROR`.
4. The inference line carries `side`, `texts`, `chars`, `wait_ms`, `encode_ms`, and the request id of the request that caused it (this is the thread-propagation check).
5. An unhandled exception is logged with a traceback and the request id, and the problem document carries `request_id` while `detail` stays absent.
6. A `503 model-busy` logs a warning with `retry_after`.
7. The JSON formatter's output parses line by line and contains no key from the request body.
8. `configure_logging()` twice installs one handler.

## Rollout

One PR, in this order, each step green on its own:

1. `pfa/api/logging.py`: configuration, formatters, filter, middleware, request line. `create_app()` calls `configure_logging()` and adds the middleware. `__main__` passes `log_config=None`, `PFA_ACCESS_LOG` defaults to `0`.
2. `Problem.to_body` adds `request_id`; `_on_unhandled` logs; the OpenAPI example gains the field.
3. The engine's load and inference lines; the busy warning in `_dispatch`.
4. `docs/service.md`: an **Observability** section with the two formats and the field tables; the settings table gains `PFA_LOG_FORMAT` and the new `PFA_ACCESS_LOG` default. `Dockerfile` sets `PFA_LOG_FORMAT=json`. `CHANGELOG.md` entry. `README.md` gets one line under Quick start: where the log goes and how to read a request id back.

Behaviour change for existing operators: uvicorn's access line disappears unless `PFA_ACCESS_LOG=1`. The replacement carries strictly more, so this is noted in the changelog rather than kept behind a deprecation.

## Alternatives considered

- **Keep uvicorn's access log and add a request-id header only.** Cheapest, but the access line has no duration and the inference time is still invisible. Rejected: it does not answer the first incident question.
- **`BaseHTTPMiddleware`.** Simpler to write, but it buffers responses and breaks background tasks and streaming. Pure ASGI is thirty more lines once.
- **`structlog`.** Nicer API, one more dependency in the image and a second way to log next to the stdlib loggers uvicorn and sentence-transformers already use. Revisit if the number of fields grows past what `extra=` reads well with.
- **Log the request text at `DEBUG`.** Tempting for debugging chunking; rejected outright, because a `DEBUG` run in production would write customer documents to disk. Reproduce chunking from the `chars` count and the `/chunking/split` endpoint instead.

## Open questions

1. Should a `503 not-ready` from `/ready` log at `WARNING` during the first minute? Proposal: no; the warm-up lines already say the model is loading, and probes are expected to see `503` then.
2. Does the inference line want the token count? The engine does not know it (chunking does). Proposal: no; `chars` and `texts` are enough to size the work, and adding a bus call for a log line is backwards.
3. `request_id` in every problem document, or only on `5xx`? Proposal: every one. The field is cheap, the schema stays uniform, and a client-side bug report with a `422` benefits just as much.
