"""The OpenAPI document is the contract for ``/docs`` consumers: every operation is named and
summarised, every tag is described, every operation shows one exchange its models accept with
the fields in written order, and every error example is what the wire actually sends. Exercised
over the real app; no model is loaded (an empty body and a blank text are rejected before one is)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import BaseModel

from pfa.api.problems import DOMAIN_REJECTION, EXAMPLE_TRACE_ID, MODEL_BUSY, NOT_READY, PROBLEM_MEDIA_TYPE
from pfa.api.routes import ROUTERS, TAGS
from pfa.api.validation import JSON

Spec = dict[str, Any]


@pytest.fixture(scope="module")
def spec(client: TestClient) -> Spec:
    return client.get("/openapi.json").json()


def _operations(spec: Spec) -> list[tuple[str, Spec]]:
    """Every operation with its path."""
    return [(path, operation) for path, operations in spec["paths"].items() for operation in operations.values()]


def _operations_with_a_body(spec: Spec) -> list[tuple[str, Spec]]:
    return [(path, operation) for path, operation in _operations(spec) if "requestBody" in operation]


def _operations_under(spec: Spec, prefix: str) -> list[tuple[str, Spec]]:
    return [(path, operation) for path, operation in _operations(spec) if path.startswith(prefix)]


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


def _routes() -> list[APIRoute]:
    """Every mounted route, with the models behind it."""
    routes = [route for router in ROUTERS for route in router.routes]
    assert all(isinstance(route, APIRoute) for route in routes)
    return routes  # type: ignore[return-value]


def _operation(spec: Spec, route: APIRoute) -> Spec:
    (method,) = route.methods
    return spec["paths"][route.path][method.lower()]


def _assert_example_fits(example: Spec, model: type[BaseModel]) -> None:
    """The model accepts the example, and the example writes the fields in the model's order:
    what ``/docs`` shows under *Try it out* reads like the model, not like a sorted dictionary."""
    model.model_validate(example)
    assert list(example) == [name for name in model.model_fields if name in example], model.__name__


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
    assert "license" not in info  # the repository carries no licence file, so the document must not advertise one
    assert not info["description"].startswith(info["summary"].split(",")[0])  # the description adds to the summary line


@pytest.mark.parametrize("route", _routes(), ids=lambda route: route.name)
def test_every_operation_shows_an_exchange_its_models_accept(spec: Spec, route: APIRoute) -> None:
    operation = _operation(spec, route)
    if route.body_field is not None:
        _assert_example_fits(operation["requestBody"]["content"][JSON]["example"], route.body_field.field_info.annotation)
    assert route.response_model is not None
    _assert_example_fits(operation["responses"]["200"]["content"][JSON]["example"], route.response_model)


def test_request_models_reject_unknown_fields(spec: Spec) -> None:
    for name in ("SplitRequest", "QuestionRequest", "PassagesRequest", "SimilarityRequest"):
        assert spec["components"]["schemas"][name]["additionalProperties"] is False, name


def test_documented_validation_example_is_what_the_wire_sends(client: TestClient, spec: Spec) -> None:
    """The 422 example (an empty body) is rendered by the code that answers one."""
    for path, operation in _operations_with_a_body(spec):
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
    for path, operation in _operations_under(spec, "/embeddings/"):
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


def test_swagger_ui_is_served_with_try_it_out_on_and_the_schema_list_hidden(client: TestClient) -> None:
    page = client.get("/docs")
    assert page.status_code == 200
    assert '"tryItOutEnabled": true' in page.text
    assert '"defaultModelsExpandDepth": -1' in page.text
