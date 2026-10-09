"""Logging follows RFC 5424 (record, severity, structured data), RFC 3339 (timestamps) and W3C
Trace Context (correlation) — ``docs/decisions/0001-logging.md``. One test per goal, over the
fake engine and a captured stream; nothing loads the model."""

from __future__ import annotations

import io
import json
import logging
import re
import threading
import time
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pfa.api import logs
from pfa.api.app import create_app
from pfa.api.logs import JsonFormatter, SyslogFormatter, configure_logging
from pfa.api.trace import start_span
from pfa.features.embeddings.contract import EmbedQuery, EngineBusy
from pfa.features.embeddings.internal.e5_engine import E5Engine
from pfa.features.embeddings.internal.handlers import EmbedQueryHandler

TRACEPARENT = re.compile(r"^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$")
# RFC 5424 §6: <PRI>VERSION SP TIMESTAMP SP HOSTNAME SP APP-NAME SP PROCID SP MSGID SP STRUCTURED-DATA [SP MSG]
SYSLOG = re.compile(r"^<(\d{1,3})>1 (\S+) (\S+) (\S+) (\S+) (\S+) (-|\[.*?(?<!\\)\])(?: (.*))?$", re.DOTALL)
SD_PARAM = re.compile(r'(\w+)="((?:[^"\\]|\\.)*)"')
RFC3339 = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


class _Stream:
    """The handler's stream, swapped in for the test: every record the service writes."""

    def __init__(self) -> None:
        self.buffer = io.StringIO()

    def entries(self) -> list[str]:
        """One string per record: a traceback continues the record's message on following lines."""
        out: list[str] = []
        for ln in self.buffer.getvalue().splitlines():
            if ln.startswith(("<", "{")) or not out:
                out.append(ln)
            else:
                out[-1] += chr(10) + ln
        return out

    def records(self, fmt: str) -> list[dict]:
        if fmt == "json":
            return [json.loads(e) for e in self.entries() if e.startswith("{")]
        return [parse_syslog(e) for e in self.entries() if e.startswith("<")]


def parse_syslog(line: str) -> dict:
    m = SYSLOG.match(line)
    assert m, line
    pri, ts, host, app, procid, msgid, sd, msg = m.groups()
    params: dict[str, str] = {}
    if sd != "-":
        sd_id, _, rest = sd[1:-1].partition(" ")
        params = {k: v.replace('\\"', '"').replace("\\]", "]").replace("\\\\", "\\") for k, v in SD_PARAM.findall(rest)}
        params["sd_id"] = sd_id
    return {"pri": int(pri), "timestamp": ts, "hostname": host, "app": app, "procid": procid, "msgid": msgid, "msg": msg, **params}


@pytest.fixture
def stream(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Stream]:
    monkeypatch.setenv("PFA_LOG_LEVEL", "debug")
    s = _Stream()
    configure_logging(stream=s.buffer, force=True)
    yield s
    monkeypatch.delenv("PFA_LOG_LEVEL")
    configure_logging(force=True)


@pytest.fixture
def app(words_app: FastAPI) -> FastAPI:
    class Engine:
        model_id = "fake/e5"
        max_tokens = 500

        def encode_queries(self, texts):
            return [[1.0, 0.0] for _ in texts]

        def encode_passages(self, texts):
            return [[0.0, 1.0] for _ in texts]

    words_app.state.bus.register(EmbedQuery, EmbedQueryHandler(Engine(), words_app.state.bus), replace=True)
    return words_app


# ---------------------------------------------------------------- 1–2: W3C Trace Context


def test_a_request_without_traceparent_starts_a_trace_and_the_request_line_carries_it(app: FastAPI, stream: _Stream) -> None:
    r = TestClient(app).post("/embeddings/query", json={"question": "hej"})
    m = TRACEPARENT.match(r.headers["traceparent"])
    assert r.status_code == 200 and m and m.group(1) != "0" * 32
    (line,) = [rec for rec in stream.records("syslog") if rec["msgid"] == "request"]
    assert line["trace"] == m.group(1) and line["sd_id"] == f"pfa@{logs.DOCUMENTATION_PEN}"


def test_a_valid_client_traceparent_keeps_its_trace_id_and_gets_a_new_span(app: FastAPI, stream: _Stream) -> None:
    client = TestClient(app)
    incoming = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    r = client.get("/health", headers={"traceparent": incoming, "tracestate": "vendor=1"})
    m = TRACEPARENT.match(r.headers["traceparent"])
    assert m and m.group(1) == "4bf92f3577b34da6a3ce929d0e0e4736" and m.group(2) != "00f067aa0ba902b7" and m.group(3) == "01"
    assert r.headers["tracestate"] == "vendor=1"  # passed through, never read
    for bad in (
        "00-" + "0" * 32 + "-00f067aa0ba902b7-01",
        "00-4bf92f3577b34da6a3ce929d0e0e4736-" + "0" * 16 + "-01",
        "garbage",
        "00-abc-def-01",
        "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01" + chr(10) + "X: y",
        "00-4BF92F3577B34DA6A3CE929D0E0E4736-00f067aa0ba902b7-01",  # the spec is lowercase hex
    ):
        assert start_span(bad).trace_id != "4bf92f3577b34da6a3ce929d0e0e4736"
    (line,) = [rec for rec in stream.records("syslog") if rec["msgid"] == "request"]
    assert line["trace"] == "4bf92f3577b34da6a3ce929d0e0e4736"


# ---------------------------------------------------------------- 3: the request line


def test_the_request_line_has_method_path_status_and_duration(app: FastAPI, stream: _Stream) -> None:
    client = TestClient(app)
    client.post("/embeddings/query", json={"question": "hej"})
    client.get("/health")
    client.post("/embeddings/query", json={"question": ""})
    lines = [rec for rec in stream.records("syslog") if rec["msgid"] == "request"]
    assert [(ln["method"], ln["path"], ln["status"]) for ln in lines] == [
        ("POST", "/embeddings/query", "200"),
        ("GET", "/health", "200"),
        ("POST", "/embeddings/query", "422"),
    ]
    assert all(float(ln["ms"]) >= 0 for ln in lines) and all(RFC3339.match(ln["timestamp"]) for ln in lines)
    assert lines[0]["pri"] == 16 * 8 + 6 and lines[1]["pri"] == 16 * 8 + 7  # info; a probe is debug
    assert lines[2]["pri"] == 16 * 8 + 6  # a 4xx is the client's problem: info
    assert lines[0]["client"] == "testclient" and all(ln["app"] == "pfa-api" for ln in lines)


# ---------------------------------------------------------------- 4: the inference line, in the request's trace


class _Model:
    device = "cpu"

    def __init__(self, hold: float = 0.0) -> None:
        self.hold = hold

    def encode(self, texts, normalize_embeddings=True, convert_to_numpy=True):
        import numpy as np

        time.sleep(self.hold)
        return np.ones((len(texts), 2), dtype="float32")


def _engine(hold: float = 0.0, concurrency: int = 1, timeout: float = 5.0) -> E5Engine:
    engine = E5Engine(model_id="fake", device="cpu", max_tokens=500, concurrency=concurrency, queue_timeout=timeout)
    engine._model = _Model(hold)
    return engine


def test_the_inference_line_sizes_the_work_and_carries_the_requests_trace_id(words_app: FastAPI, stream: _Stream) -> None:
    bus = words_app.state.bus
    bus.register(EmbedQuery, EmbedQueryHandler(_engine(), bus), replace=True)
    r = TestClient(words_app).post("/embeddings/query", json={"question": "hvad er en vektor"})
    assert r.status_code == 200
    trace = TRACEPARENT.match(r.headers["traceparent"]).group(1)  # type: ignore[union-attr]
    (enc,) = [rec for rec in stream.records("syslog") if rec["msgid"] == "encode"]
    assert enc["trace"] == trace  # made on the worker thread, still in the request's context
    assert enc["side"] == "query" and enc["texts"] == "1" and enc["chars"] == str(len("query: hvad er en vektor"))
    assert float(enc["wait_ms"]) >= 0 and float(enc["encode_ms"]) >= 0 and enc["device"] == "cpu"
    assert "vektor" not in stream.buffer.getvalue()  # never the text


# ---------------------------------------------------------------- 5: the 500, logged where it is answered


def test_an_unhandled_error_is_logged_with_its_traceback_and_the_trace_id_the_client_got(stream: _Stream) -> None:
    app = create_app()

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("secret internals")

    r = TestClient(app, raise_server_exceptions=False).get("/boom")
    trace = TRACEPARENT.match(r.headers["traceparent"]).group(1)  # type: ignore[union-attr]
    assert r.status_code == 500 and r.json()["trace_id"] == trace and "detail" not in r.json()
    assert "secret" not in r.text
    recs = stream.records("syslog")
    (err,) = [rec for rec in recs if rec["msgid"] == "error"]
    assert err["pri"] == 16 * 8 + 3 and err["trace"] == trace and err["method"] == "GET" and err["path"] == "/boom"
    assert "RuntimeError: secret internals" in err["msg"] and "Traceback" in err["msg"]
    (line,) = [rec for rec in recs if rec["msgid"] == "request"]
    assert line["status"] == "500" and line["pri"] == 16 * 8 + 3 and line["trace"] == trace


# ---------------------------------------------------------------- 6: busy is a warning


def test_a_gate_timeout_is_a_warning_with_the_wait(stream: _Stream) -> None:
    engine = _engine(hold=0.3, concurrency=1, timeout=0.05)
    holder = threading.Thread(target=lambda: engine.encode_passages(["hold the slot"]))
    holder.start()
    time.sleep(0.05)
    try:
        with pytest.raises(EngineBusy):
            engine.encode_queries(["b"])
    finally:
        holder.join(5)
    (busy,) = [rec for rec in stream.records("syslog") if rec["msgid"] == "busy"]
    assert busy["pri"] == 16 * 8 + 4 and busy["side"] == "query" and float(busy["wait_ms"]) >= 40
    assert "hold the slot" not in stream.buffer.getvalue()


# ---------------------------------------------------------------- 7: both formats, one record


def _record(**fields: object) -> logging.LogRecord:
    rec = logging.LogRecord("pfa.test", logging.WARNING, __file__, 1, "x", None, None)
    for k, v in fields.items():
        setattr(rec, k, v)
    return rec


def test_the_syslog_line_is_rfc_5424_with_escaped_params() -> None:
    line = SyslogFormatter(16, 32473).format(_record(msgid="request", trace="a" * 32, path='/x"y]z\\', status=200))
    rec = parse_syslog(line)
    assert rec["pri"] == 16 * 8 + 4 and rec["msgid"] == "request" and rec["sd_id"] == "pfa@32473"
    assert rec["trace"] == "a" * 32 and rec["path"] == '/x"y]z\\' and rec["status"] == "200" and rec["msg"] == "x"
    assert RFC3339.match(rec["timestamp"]) and rec["app"] == "pfa-api"
    bare = parse_syslog(SyslogFormatter(16, 32473).format(_record()))
    assert bare["sd_id" if "sd_id" in bare else "msgid"] in ("-",) and bare["msg"] == "x"  # no fields: nil structured data


def test_timestamps_truncate_to_the_millisecond_and_never_roll_the_second() -> None:
    rec = _record()
    rec.created = 1_791_471_599.9996  # 2026-10-08T14:59:59.9996Z: rounding would print ".1000Z" inside second 59
    rec.msecs = 999.6
    assert parse_syslog(SyslogFormatter(16, 32473).format(rec))["timestamp"] == "2026-10-08T14:59:59.999Z"
    assert json.loads(JsonFormatter(16, 32473).format(rec))["timestamp"] == "2026-10-08T14:59:59.999Z"


def test_the_json_line_is_the_same_record_under_the_rfc_names(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    doc = json.loads(JsonFormatter(16, 32473).format(_record(msgid="encode", trace="b" * 32, texts=3, encode_ms=1.5)))
    assert doc["pri"] == 16 * 8 + 4 and doc["severity"] == 4 and doc["facility"] == 16 and doc["msgid"] == "encode"
    assert doc["trace"] == "b" * 32 and doc["texts"] == 3 and doc["encode_ms"] == 1.5 and doc["msg"] == "x"
    assert RFC3339.match(doc["timestamp"]) and doc["app"] == "pfa-api"
    # through the real app, with the image's setting
    monkeypatch.setenv("PFA_LOG_FORMAT", "json")
    s = _Stream()
    configure_logging(stream=s.buffer, force=True)
    try:
        TestClient(app).post("/embeddings/query", json={"question": "hemmelig tekst"})
        (line,) = [rec for rec in s.records("json") if rec["msgid"] == "request"]
        assert line["status"] == 200 and isinstance(line["ms"], float) and line["msg"] is None
        assert "hemmelig" not in s.buffer.getvalue()
    finally:
        monkeypatch.delenv("PFA_LOG_FORMAT")
        configure_logging(force=True)


# ---------------------------------------------------------------- 8: one handler


def test_configure_logging_twice_installs_one_handler() -> None:
    root = logging.getLogger()
    first = configure_logging(force=True)
    assert configure_logging() is None  # no-op
    ours = [h for h in root.handlers if isinstance(h.formatter, (SyslogFormatter, JsonFormatter))]
    assert ours == [first]
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        assert logging.getLogger(name).handlers == [] and logging.getLogger(name).propagate
