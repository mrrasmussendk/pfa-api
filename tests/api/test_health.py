from __future__ import annotations

import inspect
import threading

import pytest
from fastapi.testclient import TestClient

from pfa_api import app as app_module
from pfa_api import composition
from pfa_api.app import create_app
from pfa_api.routes import health


def test_health(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_openapi_lists_every_slice_router(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert {"/health", "/embeddings/query"} <= set(paths)


def test_probes_run_on_the_event_loop_not_the_threadpool() -> None:
    """A cold model load can occupy the threadpool; the probes must not share it."""
    assert inspect.iscoroutinefunction(health.health)
    assert inspect.iscoroutinefunction(health.ready)


def test_ready_is_503_not_ready_until_every_component_is_loaded(client: TestClient) -> None:
    r = client.get("/ready")
    assert r.status_code == 503 and r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["type"] == "urn:pfa-api:problem:not-ready" and r.headers["retry-after"] == "5"
    assert body["components"] == {"tokenizer": False, "embedding_model": False}
    assert "tokenizer" in body["detail"] and "embedding_model" in body["detail"]


def test_ready_is_200_when_components_report_loaded() -> None:
    app = create_app()
    app.state.readiness = lambda: {"tokenizer": True, "embedding_model": True}
    r = TestClient(app).get("/ready")
    assert r.status_code == 200 and r.json() == {"status": "ready", "components": {"tokenizer": True, "embedding_model": True}}


def test_readiness_reads_flags_without_loading(client: TestClient) -> None:
    client.get("/ready")
    assert composition.readiness() == {"tokenizer": False, "embedding_model": False}


def test_warmup_runs_on_a_background_thread_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    done = threading.Event()
    seen: list[str] = []

    def fake_warmup_all() -> None:
        seen.append(threading.current_thread().name)
        done.set()

    monkeypatch.setattr(composition, "warmup_all", fake_warmup_all)
    with TestClient(create_app(warmup=True)) as c:
        assert c.get("/health").status_code == 200  # liveness answers while warm-up runs
        assert done.wait(5)
    assert seen == ["pfa-warmup"]


def test_warmup_is_off_by_default_and_env_driven(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr(composition, "warmup_all", lambda: calls.append(1))
    with TestClient(create_app()):
        pass
    assert calls == []
    monkeypatch.setenv("PFA_WARMUP", "true")
    with TestClient(create_app()):
        pass
    assert app_module._truthy("yes") and not app_module._truthy("0")
