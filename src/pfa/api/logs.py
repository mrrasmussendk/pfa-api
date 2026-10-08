"""Logging: RFC 5424 records, RFC 3339 timestamps, W3C Trace Context correlation.

See ``docs/decisions/0001-logging.md``. The process writes one stream to stdout, one record per
line, in one of two shapes of the same record:

- ``syslog`` (default): an RFC 5424 line — ``<PRI>1 TIMESTAMP HOSTNAME APP-NAME PROCID MSGID
  [pfa@PEN key="value" …] MSG`` — readable in a terminal, parseable by any syslog receiver;
- ``json`` (the image): one object per line with the same fields under the RFC's names.

Call sites never format: they log a message id and fields (``log.info("request", extra={...})``);
the formatter lays them out and escapes them. The trace id reaches every record through a
filter on the handler, in whichever thread the record was made — the features only ever call
``logging.getLogger``.

``configure_logging()`` is idempotent and is called from ``create_app()``.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import sys
import time
from datetime import datetime, timezone
from typing import Any, TextIO

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .trace import current_trace, start_span

APP_NAME = "pfa-api"
DOCUMENTATION_PEN = 32473  # RFC 5612: reserved for documentation — replace with the IANA-assigned number
PROBE_PATHS = frozenset({"/health", "/ready"})

# RFC 5424 §6.2.1 severities for Python's levels; anything in between rounds up to the next worse.
_SEVERITY = ((logging.CRITICAL, 2), (logging.ERROR, 3), (logging.WARNING, 4), (logging.INFO, 6), (logging.DEBUG, 7))
_FACILITIES = {"kern": 0, "user": 1, "daemon": 3, "syslog": 5, **{f"local{i}": 16 + i for i in range(8)}}
# LogRecord's own attributes: everything else on a record is a field the call site passed in ``extra``.
_RECORD_ATTRS = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {"message", "asctime", "taskName"}
_RESERVED_FIELDS = frozenset({"msgid", "trace"})

log = logging.getLogger("pfa.api.request")


def severity_of(level: int) -> int:
    for threshold, severity in _SEVERITY:
        if level >= threshold:
            return severity
    return 7


def fields_of(record: logging.LogRecord) -> dict[str, Any]:
    """The call site's ``extra`` fields, in insertion order, without the record's own attributes."""
    return {k: v for k, v in record.__dict__.items() if k not in _RECORD_ATTRS and k not in _RESERVED_FIELDS and not k.startswith("_")}


def _timestamp(record: logging.LogRecord) -> str:
    """RFC 3339, UTC, milliseconds — truncated, never rounded, so 59.9996 s stays within its second."""
    dt = datetime.fromtimestamp(record.created, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _message(record: logging.LogRecord) -> str:
    """The free-text part: the formatted message unless it is just the message id, plus the traceback."""
    text = record.getMessage()
    if text == getattr(record, "msgid", None):
        text = ""
    if record.exc_info and not record.exc_text:
        record.exc_text = logging.Formatter().formatException(record.exc_info)
    if record.exc_text:
        text = f"{text}\n{record.exc_text}" if text else record.exc_text
    return text


class _Base(logging.Formatter):
    def __init__(self, facility: int, enterprise_number: int) -> None:
        super().__init__()
        self.facility = facility
        self.sd_id = f"pfa@{enterprise_number}"
        self.hostname = socket.gethostname() or "-"
        self.procid = str(os.getpid())


class SyslogFormatter(_Base):
    """RFC 5424 §6. ``-`` is the nil value; param values escape ``"``, ``\\`` and ``]`` (§6.3.3)."""

    @staticmethod
    def _escape(value: Any) -> str:
        return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("]", "\\]")

    def format(self, record: logging.LogRecord) -> str:
        pri = self.facility * 8 + severity_of(record.levelno)
        msgid = getattr(record, "msgid", None) or "-"
        params = dict(fields_of(record))
        trace = getattr(record, "trace", None)
        if trace:
            params = {"trace": trace, **params}
        sd = "[" + " ".join([self.sd_id, *(f'{k}="{self._escape(v)}"' for k, v in params.items())]) + "]" if params else "-"
        return f"<{pri}>1 {_timestamp(record)} {self.hostname} {APP_NAME} {self.procid} {msgid} {sd} {_message(record) or '-'}"


class JsonFormatter(_Base):
    """The same record as one JSON object per line, fields under the RFC's names."""

    def format(self, record: logging.LogRecord) -> str:
        severity = severity_of(record.levelno)
        doc: dict[str, Any] = {
            "pri": self.facility * 8 + severity,
            "severity": severity,
            "facility": self.facility,
            "timestamp": _timestamp(record),
            "hostname": self.hostname,
            "app": APP_NAME,
            "procid": int(self.procid),
            "msgid": getattr(record, "msgid", None),
            "logger": record.name,
            "trace": getattr(record, "trace", None),
            **fields_of(record),
            "msg": _message(record) or None,
        }
        return json.dumps(doc, ensure_ascii=False, default=str)


class TraceFilter(logging.Filter):
    """Copies the current trace id onto every record the handler sees."""

    def filter(self, record: logging.LogRecord) -> bool:
        t = current_trace.get()
        record.trace = t.trace_id if t else None
        return True


_configured: dict[str, Any] = {}


def configure_logging(*, stream: TextIO | None = None, force: bool = False) -> logging.Handler | None:
    """One handler on the root logger, the format and level from the environment; uvicorn's loggers
    write through it too. A second call is a no-op unless ``force`` (tests swap the stream)."""
    if _configured and not force:
        return None
    fmt = os.environ.get("PFA_LOG_FORMAT", "syslog").strip().lower()
    facility = _FACILITIES.get(os.environ.get("PFA_LOG_FACILITY", "local0").strip().lower(), 16)
    pen = int(os.environ.get("PFA_LOG_ENTERPRISE_NUMBER", DOCUMENTATION_PEN))
    level = os.environ.get("PFA_LOG_LEVEL", "info").strip().upper()
    formatter: logging.Formatter = JsonFormatter(facility, pen) if fmt == "json" else SyslogFormatter(facility, pen)

    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(formatter)
    handler.addFilter(TraceFilter())
    root = logging.getLogger()
    old = _configured.get("handler")
    if old is not None:
        root.removeHandler(old)
    root.addHandler(handler)
    known = logging.getLevelName(level)  # an int when the name is known, a string otherwise
    root.setLevel(known if isinstance(known, int) else logging.INFO)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv = logging.getLogger(name)
        uv.handlers.clear()
        uv.propagate = True
    _configured["handler"] = handler
    return handler


class TraceContext:
    """Pure ASGI middleware: starts the span, echoes ``traceparent`` (and passes ``tracestate``
    through), and logs the request line when the response is done. The unhandled-error path is
    the one case the response is sent outside this middleware (Starlette's server-error handler
    sits above it): the line is still logged, as a 500, and ``pfa.api.problems`` adds the header."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        trace = start_span(headers.get("traceparent"))
        tracestate = headers.get("tracestate")
        current_trace.set(trace)  # per-request task: nothing to reset, and the error handler above still needs it
        status = 500
        started = time.perf_counter()

        async def send_with_trace(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                out = MutableHeaders(scope=message)
                out.append("traceparent", trace.traceparent)
                if tracestate:
                    out.append("tracestate", tracestate)
            await send(message)

        try:
            await self.app(scope, receive, send_with_trace)
        finally:
            self._log(scope, status, (time.perf_counter() - started) * 1000.0)

    @staticmethod
    def _log(scope: Scope, status: int, ms: float) -> None:
        path = scope.get("path", "")
        client = scope.get("client")
        level = logging.ERROR if status >= 500 else logging.DEBUG if path in PROBE_PATHS else logging.INFO
        log.log(
            level,
            "request",
            extra={
                "msgid": "request",
                "method": scope.get("method", "-"),
                "path": path,
                "status": status,
                "ms": round(ms, 1),
                "client": client[0] if client else "-",
            },
        )
