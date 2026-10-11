# Logging

✅ **Do** marks the expected approach. ❌ **Don't** marks a prohibited shortcut. ⚠️ **Check** marks a condition to resolve before continuing.

| ✅ Do | ❌ Don't |
|---|---|
| Log a message id and fields: `log.info("what happened", extra={"msgid": "encode", "texts": 3, "encode_ms": 41.2})`. | Format values into the message string. |
| Log sizes, counts, durations and ids. | Log a passage, a question, a vector, a query string or a secret, at any level. |
| Use `logging.getLogger(__name__)`, stdlib only, inside a feature. | Import the entry point's logging module, or a framework, from a feature (EIT006). |
| Pick the severity from the table below. | Log an expected rejection (a `422`) as an error. |

The record format, the trace id and the transports are decided once, in [ADR 0001](../../docs/decisions/0001-logging.md), and implemented in `src/pfa/api/logs.py`. A call site only says what happened and with which numbers. The formatter turns that into an RFC 5424 line or a JSON object, and a filter adds the request's W3C trace id, including on worker threads. Never add the trace id yourself.

| Severity | For |
|---|---|
| `debug` | probe requests (`/health`, `/ready`) |
| `info` | a request, an inference, a load |
| `warning` | a request the service turned away because it was busy, a failed warm-up |
| `error` | an unhandled exception (a `500`) |
| `critical` | a component that failed and will not retry |

⚠️ **Check:** a new `msgid` is a new kind of line an operator will search for. Add it to the `MSGID` table in [the service reference](../../docs/service.md#observability-the-log) with its fields.

⚠️ **Check:** test the line, not the format: capture the record (`tests/api/test_logging.py` shows how) and assert its `msgid` and fields, and that no field holds the user's text.
