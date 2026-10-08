"""W3C Trace Context: the one correlation id, carried by the ``traceparent`` header.

``00-<32 hex trace-id>-<16 hex parent-id>-<2 hex flags>``. The trace id is the id every log
line and every problem document carries; this request is a new span under it. The current
trace lives in a context variable set by the middleware in ``pfa.api.logs`` and read by the
logging filter and by ``pfa.api.problems`` — stdlib only, so both can import it without a cycle.
"""

from __future__ import annotations

import re
import secrets
from contextvars import ContextVar
from dataclasses import dataclass

_TRACEPARENT = re.compile(r"^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$")
_ZERO_TRACE = "0" * 32
_ZERO_SPAN = "0" * 16


@dataclass(frozen=True)
class Trace:
    trace_id: str
    span_id: str
    flags: str = "00"

    @property
    def traceparent(self) -> str:
        return f"00-{self.trace_id}-{self.span_id}-{self.flags}"


current_trace: ContextVar[Trace | None] = ContextVar("pfa_trace", default=None)


def start_span(traceparent: str | None) -> Trace:
    """The trace this request belongs to: the client's when its ``traceparent`` parses, a new one
    otherwise. Either way the span id is fresh — this request is its own span."""
    span_id = secrets.token_hex(8)
    m = _TRACEPARENT.match(traceparent.strip()) if traceparent else None
    if m and m.group(1) != _ZERO_TRACE and m.group(2) != _ZERO_SPAN:
        return Trace(m.group(1), span_id, m.group(3))
    return Trace(secrets.token_hex(16), span_id)


def current_trace_id() -> str | None:
    t = current_trace.get()
    return t.trace_id if t else None
