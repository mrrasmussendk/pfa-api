# ADR 0001 — Logging follows RFC 5424, RFC 3339 and W3C Trace Context

**Status:** proposed · **Owner:** Marc Rasmussen · **Date:** 2026-10-08 · **Scope:** `src/pfa/api`, `src/pfa/features/embeddings/internal/e5_engine.py`, `docs/service.md`

Errors already follow a standard: every error response is an RFC 9457 problem document, so a client parses one shape. Logging should be held to the same bar. This decision names the standards a log record follows, maps the service onto them, and lays out the lines, the tests and the rollout.

## Why

The service emits two log lines in its whole life, both during warm-up. An operator cannot answer the questions an incident starts with:

- *Which request was slow, and where did the time go — queueing for the model or encoding?*
- *What did the 500 at 14:02 look like?* The handler answers `500` with no detail (correct: internals must not leave on the wire), but it does not log either. The traceback reaches the console only because Starlette re-raises and uvicorn prints it, with no path, no method and nothing to tie it to a client's report.
- *Is the model loaded, on which device, and how long did the load take?*
- *How much work is this instance doing?* Requests, texts, chunks, encode time.

Uvicorn's access log does not help: no correlation id, no duration, no knowledge of the model. The case this service answers names logging next to validation and error handling; those two are strong, this one is not.

## The standards

| Concern | Standard | What it fixes for us |
|---|---|---|
| Severity | [RFC 5424 §6.2.1](https://www.rfc-editor.org/rfc/rfc5424#section-6.2.1) | Eight numeric levels every log pipeline understands. Python's levels map onto them below; `warning` means the same thing to rsyslog, Vector, Datadog and us. |
| Record layout | [RFC 5424 §6](https://www.rfc-editor.org/rfc/rfc5424#section-6) | `PRI`, `VERSION`, `TIMESTAMP`, `HOSTNAME`, `APP-NAME`, `PROCID`, `MSGID`, structured data, message. One line, one record, parseable by any syslog receiver without configuration. |
| Structured data | [RFC 5424 §6.3](https://www.rfc-editor.org/rfc/rfc5424#section-6.3) | Key-value pairs with a defined escape, grouped under an `SD-ID`, instead of a free-form message that each tool regexes differently. |
| Timestamp | [RFC 3339](https://www.rfc-editor.org/rfc/rfc3339) (required by RFC 5424 §6.2.3) | `2026-10-08T14:02:03.117Z`. UTC, milliseconds, sortable as text. |
| Correlation | [W3C Trace Context](https://www.w3.org/TR/trace-context/) | `traceparent` request and response header. The trace id is the correlation id; a tracer can pick the same id up later with no change to the lines. |
| Errors on the wire | RFC 9457 (already adopted) | The problem document carries the trace id, so a client can quote the id the log line has. |

What is deliberately **not** adopted: RFC 5424's transports (RFC 5425 TLS, RFC 6587 TCP). The service writes records to stdout; the platform ships them. The *format* is the standard, not the socket.

### Severity mapping

| Python | RFC 5424 severity | Name | Used for |
|---|---|---|---|
| `CRITICAL` | 2 | crit | model failed to load and will not retry |
| `ERROR` | 3 | err | unhandled exception (a `500`) |
| `WARNING` | 4 | warning | `503 model-busy`, gate timeout, warm-up failure |
| `INFO` | 6 | info | request line, inference line, model loaded |
| `DEBUG` | 7 | debug | probe requests (`/health`, `/ready`) |

Facility is `local0` (16) by default, `PFA_LOG_FACILITY` to change it. `PRI` is `facility × 8 + severity`: an info line opens with `<134>`.

### Record header

| Field | Value |
|---|---|
| `VERSION` | `1` |
| `TIMESTAMP` | RFC 3339, UTC, milliseconds |
| `HOSTNAME` | `socket.gethostname()` (the pod or container name) |
| `APP-NAME` | `pfa-api` |
| `PROCID` | the PID |
| `MSGID` | the kind of line: `request`, `encode`, `load`, `warmup`, `error`, `busy` |

### Structured data

Every field the service adds goes in one SD-ELEMENT, `[pfa@<PEN> key="value" …]`, where `PEN` is an IANA Private Enterprise Number. RFC 5424 §7.2.2 reserves un-suffixed SD-IDs for IANA-registered ones, so a private id must carry a PEN. Getting one is a free form at IANA and takes a few days; until it arrives the code uses `32473`, the number RFC 5612 reserves for documentation, behind `PFA_LOG_ENTERPRISE_NUMBER`. The correlation id rides in the same element as `trace="<32 hex>"`.

Values are escaped as the RFC says (`"`, `\` and `]` backslash-escaped); the formatter does it, call sites never see it.

## Design

### Where it lives, and the walls

| Piece | Module | Why there |
|---|---|---|
| Configuration, the RFC 5424 and JSON formatters, the trace filter | `src/pfa/api/logging.py` (new) | The entry point owns process concerns. May import Starlette. |
| Trace-context middleware and the request line | `src/pfa/api/logging.py` | ASGI middleware is HTTP; it belongs in `pfa/api` (EIT006). |
| Inference and load lines | `src/pfa/features/embeddings/internal/e5_engine.py` | The engine knows the numbers. It uses `logging.getLogger` only — stdlib, framework-free, so the feature stays inside its walls. |
| Error lines | `src/pfa/api/problems.py` (`_on_unhandled`), `src/pfa/api/routes/embeddings.py` (`_dispatch`) | Logged where the error is turned into a response. |

The trace id travels in a `contextvars.ContextVar` set by the middleware. A `logging.Filter` on the handler copies it onto every record. The engine never sees the middleware or the variable; its records get the id because the filter runs on the handler, in whichever thread the record was made. FastAPI runs sync routes on a worker thread through anyio, which copies the context into the thread. Verified on 2026-10-08 with a context variable set in middleware and read inside a sync route on the "AnyIO worker thread"; a test keeps it true.

### Configuration

`configure_logging()` in `pfa/api/logging.py` is called once from `create_app()` and is idempotent, guarded like `install_problem_details`, so tests that build many apps configure once.

| Setting | Default | Meaning |
|---|---|---|
| `PFA_LOG_LEVEL` | `info` | Level for the `pfa` loggers and uvicorn's. Exists today; keeps its name. |
| `PFA_LOG_FORMAT` | `syslog` (`json` in the image) | `syslog`: one RFC 5424 line per record. `json`: one object per line with the same fields. |
| `PFA_LOG_FACILITY` | `local0` | RFC 5424 facility for `PRI`. |
| `PFA_LOG_ENTERPRISE_NUMBER` | `32473` | The PEN in the SD-ID. Set to the registered number once IANA assigns it. |
| `PFA_ACCESS_LOG` | `0` (was `1`) | Uvicorn's access log. Off by default because the request line replaces it; `1` turns it back on for comparison. |

The root logger gets one `StreamHandler` on stdout with the chosen formatter and the trace filter. Uvicorn's `uvicorn`, `uvicorn.error` and `uvicorn.access` loggers are pointed at the same handler (their own handlers removed) so the process writes one stream in one format. `python -m pfa.api` passes `log_config=None` to uvicorn so it does not install its own.

### Correlation: `traceparent`

Middleware `TraceContext` (pure ASGI, not `BaseHTTPMiddleware`, so streaming and background tasks are unaffected):

1. Read `traceparent`. Accept it when it parses per W3C Trace Context (`00-<32 hex trace-id>-<16 hex parent-id>-<2 hex flags>`, trace id not all zeros); otherwise ignore it. A client cannot inject newlines or control characters into the logs.
2. Otherwise start a trace: a random 16-byte trace id.
3. Either way, this request is a new span: generate an 8-byte span id. Set the context variable (trace id) for the duration of the request.
4. Respond with `traceparent: 00-<trace-id>-<span-id>-<flags>` on every response, including problem documents.
5. `Problem.to_body` adds `trace_id` as an extension member on every problem document. A client that gets a `500` has something to quote, and the OpenAPI example shows the field.

`X-Request-ID` is not read or written. One correlation scheme, the standard one.

### The request line (`MSGID=request`)

The same middleware records `time.perf_counter()` at start and logs on the response's completion, at the `pfa.api.request` logger:

| SD-PARAM | Example | Note |
|---|---|---|
| `method`, `path` | `POST`, `/embeddings/query` | Path only, never the query string (it could carry text). |
| `status` | `200` | |
| `ms` | `41.3` | Wall time, start of request to last byte sent. |
| `client` | `10.0.0.5` | The address uvicorn resolved, honouring `PFA_FORWARDED_ALLOW_IPS`. |

Severity: info for everything except `/health` and `/ready`, which log at debug so probes every few seconds do not drown the stream. A `5xx` logs at err, a `4xx` at info (a validation failure is the client's problem and the status says so).

### The inference line (`MSGID=encode`)

`E5Engine._encode` measures the gate wait and the encode separately and logs at `pfa.embeddings.engine`:

| SD-PARAM | Example | Note |
|---|---|---|
| `side` | `passage` | `query` or `passage`. |
| `texts` | `12` | Chunks encoded in this call. |
| `chars` | `18402` | Sum of lengths. Never the text. |
| `wait_ms` | `0.1` | Time blocked on `PFA_EMBED_CONCURRENCY`. Rising wait with flat encode time means "add a replica". |
| `encode_ms` | `388.0` | Time inside `model.encode`. |
| `device` | `cpu` | |

The one-time load logs `MSGID=load` with `model_id`, `device`, `max_seq_length` and `load_ms` at info. A gate timeout logs `MSGID=busy` at warning with `wait_ms` before raising `EngineBusy`; the route then answers `503`.

### Errors

- `_on_unhandled` logs `MSGID=error` at err with `method`, `path` and the traceback as the message before answering. The traceback goes to the log, the trace id goes to both the log and the problem document, and the wire still says only `500`.
- `_dispatch` in the embeddings routes logs `MSGID=busy` at warning with `retry_after` when it maps `EngineBusy` to `503`.
- Warm-up keeps its two lines as `MSGID=warmup`; `warm-up complete` gains `load_ms`.
- A `4xx` is not logged separately. The request line carries the status, and the problem document already told the client why.

### Privacy

Nothing the user sent is logged. Passages are up to 200 kB and may be personal data. Sizes and counts only, never text, never the query string. This is a goal, not a level: there is no `DEBUG` that writes documents.

### The two formats, same record

RFC 5424 line, the default, readable in a terminal and parseable by any syslog receiver:

```
<134>1 2026-10-08T14:02:03.117Z api-7c9d pfa-api 1 request [pfa@32473 trace="4bf92f3577b34da6a3ce929d0e0e4736" method="POST" path="/embeddings/passages" status="200" ms="412.5" client="127.0.0.1"] -
<134>1 2026-10-08T14:02:03.116Z api-7c9d pfa-api 1 encode [pfa@32473 trace="4bf92f3577b34da6a3ce929d0e0e4736" side="passage" texts="12" chars="18402" wait_ms="0.1" encode_ms="388.0" device="cpu"] -
<131>1 2026-10-08T14:02:07.900Z api-7c9d pfa-api 1 error [pfa@32473 trace="8b11e0f4a2c74d0b9e1f3a5c7d9e0b12" method="POST" path="/embeddings/query"] Traceback (most recent call last): ...
```

`-` is RFC 5424's nil value for an empty message; a traceback is the message, with newlines kept (the receiver's line framing handles it, as rsyslog and Vector do; `json` is the choice when the shipper wants strictly one line).

JSON, one object per line, the same fields under the RFC's names, for a shipper that prefers it:

```json
{"pri": 134, "severity": 6, "facility": 16, "timestamp": "2026-10-08T14:02:03.117Z", "hostname": "api-7c9d", "app": "pfa-api", "procid": 1, "msgid": "request", "trace": "4bf92f3577b34da6a3ce929d0e0e4736", "method": "POST", "path": "/embeddings/passages", "status": 200, "ms": 412.5, "client": "127.0.0.1", "msg": null}
{"pri": 131, "severity": 3, "facility": 16, "timestamp": "2026-10-08T14:02:07.900Z", "hostname": "api-7c9d", "app": "pfa-api", "procid": 1, "msgid": "error", "trace": "8b11e0f4a2c74d0b9e1f3a5c7d9e0b12", "method": "POST", "path": "/embeddings/query", "msg": "Traceback (most recent call last): ..."}
```

Numbers are numbers in JSON and strings in the syslog line, as each format requires. Both formatters read the same `LogRecord`; call sites pass fields with `extra={...}` and never format anything.

## Tests (`tests/api/test_logging.py`)

Each goal has a test, over the fake engine and `caplog`; nothing loads the model.

1. A request without `traceparent` gets a valid one back, and the request line carries the same trace id.
2. A valid client `traceparent` keeps its trace id and gets a new span id; a malformed one (wrong length, all-zero trace id, a newline) is replaced.
3. The request line has `method`, `path`, `status` and a numeric `ms`; `/health` logs at debug, a `500` at err.
4. The inference line carries `side`, `texts`, `chars`, `wait_ms`, `encode_ms`, and the trace id of the request that caused it (the thread-propagation check).
5. An unhandled exception is logged with a traceback and the trace id, and the problem document carries `trace_id` while `detail` stays absent.
6. A `503 model-busy` logs at warning with `retry_after`.
7. The syslog formatter's output parses with a small RFC 5424 parser in the test (`PRI`, header, SD-ELEMENT with escaped values) and `PRI` equals `facility × 8 + severity`; the JSON formatter's output parses line by line. Neither contains any key from a request body.
8. `configure_logging()` twice installs one handler.

## Rollout

One PR, in this order, each step green on its own:

1. `pfa/api/logging.py`: configuration, both formatters, the trace filter, the middleware, the request line. `create_app()` calls `configure_logging()` and adds the middleware. `__main__` passes `log_config=None`; `PFA_ACCESS_LOG` defaults to `0`.
2. `Problem.to_body` adds `trace_id`; `_on_unhandled` logs; the OpenAPI example gains the field.
3. The engine's load and inference lines; the busy warning in `_dispatch`.
4. `docs/service.md`: an **Observability** section naming the standards, the severity mapping and the field tables; the settings table gains the new settings and the new `PFA_ACCESS_LOG` default. `Dockerfile` sets `PFA_LOG_FORMAT=json`. `CHANGELOG.md` entry. `README.md` gets one line under Quick start: where the log goes and how to read a trace id back.
5. Outside the code: request the IANA Private Enterprise Number; set `PFA_LOG_ENTERPRISE_NUMBER` in the image when it arrives.

Behaviour change for existing operators: uvicorn's access line disappears unless `PFA_ACCESS_LOG=1`. The replacement carries strictly more, so this is noted in the changelog rather than kept behind a deprecation.

## Alternatives considered

- **Keep uvicorn's access log and add a correlation header only.** Cheapest, but the access line has no duration and the inference time is still invisible. Rejected: it does not answer the first incident question.
- **A home-grown line format with `X-Request-ID`.** What the first draft of this document proposed. Rejected for the same reason problem documents follow RFC 9457: a format we invent is one every tool has to be taught; RFC 5424 and `traceparent` are already understood.
- **OpenTelemetry's log data model instead of RFC 5424.** It is the newer standard and its severity numbers are aligned with syslog's. Rejected for now: it brings the SDK and an exporter into the image for the same fields. The RFC 5424 record carries everything an OTel collector's syslog receiver turns into that model, and `traceparent` is already the OTel correlation key, so the step up later is configuration, not a rewrite.
- **`BaseHTTPMiddleware`.** Simpler to write, but it buffers responses and breaks background tasks and streaming. Pure ASGI is thirty more lines once.
- **`structlog` or `python-json-logger`.** One more dependency in the image and a second way to log next to the stdlib loggers uvicorn and sentence-transformers already use. The stdlib does this in about a hundred and fifty lines including the escaping.
- **Log the request text at debug.** Tempting for debugging chunking; rejected outright, because a debug run in production would write customer documents to disk. Reproduce chunking from the `chars` count and the `/chunking/split` endpoint instead.

## Open questions

1. Should a `503 not-ready` from `/ready` log at warning during the first minute? Proposal: no; the warm-up lines already say the model is loading, and probes are expected to see `503` then.
2. Does the inference line want the token count? The engine does not know it (chunking does). Proposal: no; `chars` and `texts` are enough to size the work, and adding a bus call for a log line is backwards.
3. `trace_id` in every problem document, or only on `5xx`? Proposal: every one. The field is cheap, the schema stays uniform, and a client-side bug report with a `422` benefits just as much.
4. Should the service honour `tracestate` (the second W3C header)? Proposal: pass it through untouched on the response, never read it. It belongs to the tracing vendors, not to us.
