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

Spec = dict[str, Any]


@pytest.fixture(scope="module")
def spec(client: TestClient) -> Spec:
    return client.get("/openapi.json").json()


def _operations(spec: Spec) -> list[tuple[str, Spec]]:
    """Every operation with its path."""
    return [(path, operation) for path, operations in spec["paths"].items() for operation in operations.values()]


def _responses(spec: Spec, operation: str) -> Spec:
    """The responses of ``"POST /chunking/split"``."""
    method, path = operation.split(" ")
    return spec["paths"][path][method.lower()]["responses"]


def _examples(response: Spec) -> dict[str, Spec]:
    """Each named example of a problem response, by name."""
    return {name: example["value"] for name, example in response["content"][PROBLEM_MEDIA_TYPE]["examples"].items()}


def _without_trace(problem: Spec) -> Spec:
    """A problem document minus the one member that differs per request."""
    return {member: value for member, value in problem.items() if member != "trace_id"}


def _route_models() -> list[type[BaseModel]]:
    """Every request and response model behind a mounted route."""
    models: list[type[BaseModel]] = []
    for route in (route for router in ROUTERS for route in router.routes):
        assert isinstance(route, APIRoute)
        if route.body_field is not None:
            models.append(route.body_field.field_info.annotation)
        if route.response_model is not None:
            models.append(route.response_model)
    return models


def test_operations_are_named_after_their_route_functions(spec: Spec) -> None:
    names = {operation["operationId"] for _, operation in _operations(spec)}
    assert names == {"health", "ready", "split", "embed_question", "embed_passages", "similarity"}


def test_every_operation_has_a_written_summary_and_a_described_200(spec: Spec) -> None:
    for _, operation in _operations(spec):
        name = operation["operationId"]
        fastapi_default = name.replace("_", " ").title()
        assert operation["summary"] != fastapi_default, name
        assert operation["responses"]["200"]["description"] != "Successful Response", name


def test_every_tag_is_described_in_mount_order(spec: Spec) -> None:
    used = {tag for _, operation in _operations(spec) for tag in operation["tags"]}
    described = {tag["name"]: tag["description"] for tag in spec["tags"]}
    assert used == set(described)
    assert all(described.values())
    assert list(described) == [tag["name"] for tag in TAGS]


def test_the_document_introduces_the_service(spec: Spec) -> None:
    info = spec["info"]
    assert info["summary"]
    assert "/embeddings/passages" in info["description"]
    assert "trace_id" in info["description"]
    assert info["license"] == {"name": "MIT"}


@pytest.mark.parametrize("model", _route_models(), ids=lambda model: model.__name__)
def test_every_route_model_carries_an_example_it_accepts(model: type[BaseModel]) -> None:
    examples = model.model_json_schema().get("examples")
    assert examples, f"{model.__name__} documents no example"
    for example in examples:
        model.model_validate(example)


def test_request_models_still_reject_unknown_fields_with_an_example_configured(spec: Spec) -> None:
    """A subclass ``model_config`` merges with ``StrictRequest``'s; the example must not loosen it."""
    for name in ("SplitRequest", "QuestionRequest", "PassagesRequest", "SimilarityRequest"):
        schema = spec["components"]["schemas"][name]
        assert schema["additionalProperties"] is False, name
        assert schema["examples"], name


def test_documented_validation_example_is_what_the_wire_sends(client: TestClient, spec: Spec) -> None:
    """The 422 example (an empty body) is rendered by the code that answers one."""
    for path, operation in _operations(spec):
        if "requestBody" not in operation:
            continue
        example = _examples(operation["responses"]["422"])["validation_error"]
        actual = client.post(path, json={}).json()
        assert example["trace_id"] == EXAMPLE_TRACE_ID
        assert example["instance"] == path
        assert _without_trace(actual) == _without_trace(example), path


def test_documented_domain_rejection_is_what_the_wire_sends(client: TestClient, spec: Spec) -> None:
    examples = _examples(_responses(spec, "POST /chunking/split")["422"])
    assert list(examples) == ["validation_error", "empty_text"]  # the validation example leads; the route's rejections follow
    actual = client.post("/chunking/split", json={"text": "   "}).json()
    assert _without_trace(actual) == _without_trace(examples["empty_text"])
    assert examples["empty_text"]["type"] == DOMAIN_REJECTION


def test_every_embeddings_route_documents_the_busy_response(spec: Spec) -> None:
    for path, operation in _operations(spec):
        if not path.startswith("/embeddings/"):
            continue
        busy = operation["responses"]["503"]
        assert list(busy["headers"]) == ["Retry-After"], path
        assert list(busy["content"]) == [PROBLEM_MEDIA_TYPE], path
        (example,) = _examples(busy).values()
        assert example["type"] == MODEL_BUSY
        assert example["retry_after"] == 5
        assert example["instance"] == path


def test_documented_not_ready_example_has_the_wire_shape(client: TestClient, spec: Spec) -> None:
    not_ready = _responses(spec, "GET /ready")["503"]
    assert set(not_ready["headers"]) == {"Retry-After", "Cache-Control"}
    example = _examples(not_ready)["loading"]
    actual = client.get("/ready").json()  # a cold app: nothing is loaded
    assert set(example) == set(actual)
    assert example["type"] == actual["type"] == NOT_READY
    assert example["instance"] == "/ready"
    assert example["components"] == {"tokenizer": True, "embedding_model": False}


def test_every_operation_has_a_default_problem_example(spec: Spec) -> None:
    for path, operation in _operations(spec):
        (example,) = _examples(operation["responses"]["default"]).values()
        assert example["status"] == 500
        assert example["instance"] == path
        assert "detail" not in example  # a bug never explains itself to the client


def test_swagger_ui_is_served_with_try_it_out_on(client: TestClient) -> None:
    page = client.get("/docs")
    assert page.status_code == 200
    assert "tryItOutEnabled" in page.text
