"""The OpenAPI document is the contract for ``/docs`` consumers: every operation is named and
summarised, every tag is described, every request and response model carries an example the
model itself accepts, and every error example is what the wire actually sends. Exercised over
the real app; no model is loaded (an empty body and a blank text are rejected before one is)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import BaseModel

from pfa.api.problems import DOMAIN_REJECTION, EXAMPLE_TRACE_ID, MODEL_BUSY, NOT_READY, PROBLEM_MEDIA_TYPE
from pfa.api.routes import ROUTERS, TAGS


def _operations(spec: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [(path, op) for path, ops in spec["paths"].items() for op in ops.values()]


def _examples(response: dict[str, Any]) -> dict[str, Any]:
    """Each named example's value in a problem response."""
    return {name: ex["value"] for name, ex in response["content"][PROBLEM_MEDIA_TYPE]["examples"].items()}


def _route_models() -> list[type[BaseModel]]:
    """Every request and response model behind a mounted route."""
    found: list[type[BaseModel]] = []
    for router in ROUTERS:
        for route in router.routes:
            assert isinstance(route, APIRoute)
            if route.body_field is not None:
                found.append(route.body_field.field_info.annotation)
            if route.response_model is not None:
                found.append(route.response_model)
    return found


def test_every_operation_has_a_stable_id_and_a_written_summary(client: TestClient) -> None:
    ops = _operations(client.get("/openapi.json").json())
    assert {op["operationId"] for _, op in ops} == {"health", "ready", "split", "embed_question", "embed_passages", "similarity"}
    for _, op in ops:
        auto = op["operationId"].replace("_", " ").title()  # what FastAPI would have written
        assert op["summary"] and op["summary"] != auto, op["operationId"]
        assert op["responses"]["200"]["description"] != "Successful Response", op["operationId"]


def test_every_tag_is_described_in_mount_order(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    used = {tag for _, op in _operations(spec) for tag in op["tags"]}
    described = {tag["name"]: tag["description"] for tag in spec["tags"]}
    assert used == set(described) and all(described.values())
    assert list(described) == [tag["name"] for tag in TAGS]


def test_the_document_introduces_the_service(client: TestClient) -> None:
    info = client.get("/openapi.json").json()["info"]
    assert info["summary"] and "/embeddings/passages" in info["description"] and "trace_id" in info["description"]
    assert info["license"] == {"name": "MIT"}


@pytest.mark.parametrize("model", _route_models(), ids=lambda m: m.__name__)
def test_every_route_model_carries_an_example_it_accepts(model: type[BaseModel]) -> None:
    examples = model.model_json_schema().get("examples")
    assert examples, f"{model.__name__} documents no example"
    for example in examples:
        model.model_validate(example)


def test_request_models_still_reject_unknown_fields_with_an_example_configured(client: TestClient) -> None:
    """A subclass ``model_config`` merges with ``StrictRequest``'s; the example must not loosen it."""
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    for name in ("SplitRequest", "QuestionRequest", "PassagesRequest", "SimilarityRequest"):
        assert schemas[name]["additionalProperties"] is False and schemas[name]["examples"], name


def test_documented_validation_example_is_what_the_wire_sends(client: TestClient) -> None:
    """The 422 example (an empty body) is rendered by the code that answers one."""
    for path, op in _operations(client.get("/openapi.json").json()):
        if "requestBody" not in op:
            continue
        example = _examples(op["responses"]["422"])["validation_error"]
        actual = client.post(path, json={}).json()
        assert example["trace_id"] == EXAMPLE_TRACE_ID and example["instance"] == path
        assert actual == {**example, "trace_id": actual["trace_id"]}, path


def test_documented_domain_rejection_is_what_the_wire_sends(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    examples = _examples(spec["paths"]["/chunking/split"]["post"]["responses"]["422"])
    assert list(examples) == ["validation_error", "empty_text"]  # the validation example leads; the route's rejections follow
    actual = client.post("/chunking/split", json={"text": "   "}).json()
    assert actual == {**examples["empty_text"], "trace_id": actual["trace_id"]}
    assert examples["empty_text"]["type"] == DOMAIN_REJECTION and examples["empty_text"]["instance"] == "/chunking/split"


def test_every_embeddings_route_documents_the_busy_response(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    for path, op in _operations(spec):
        if not path.startswith("/embeddings/"):
            continue
        busy = op["responses"]["503"]
        assert list(busy["headers"]) == ["Retry-After"] and list(busy["content"]) == [PROBLEM_MEDIA_TYPE], path
        (example,) = _examples(busy).values()
        assert example["type"] == MODEL_BUSY and example["retry_after"] == 5 and example["instance"] == path


def test_documented_not_ready_example_has_the_wire_shape(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    not_ready = spec["paths"]["/ready"]["get"]["responses"]["503"]
    assert set(not_ready["headers"]) == {"Retry-After", "Cache-Control"}
    example = _examples(not_ready)["loading"]
    actual = client.get("/ready").json()  # cold app: nothing is loaded
    assert set(example) == set(actual) and example["type"] == actual["type"] == NOT_READY
    assert example["instance"] == "/ready" and example["components"] == {"tokenizer": True, "embedding_model": False}


def test_every_operation_has_a_default_problem_example(client: TestClient) -> None:
    for path, op in _operations(client.get("/openapi.json").json()):
        (example,) = _examples(op["responses"]["default"]).values()
        assert example["status"] == 500 and example["instance"] == path and "detail" not in example


def test_swagger_ui_is_served_with_try_it_out_on(client: TestClient) -> None:
    page = client.get("/docs")
    assert page.status_code == 200 and "tryItOutEnabled" in page.text
