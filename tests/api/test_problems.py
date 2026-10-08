"""Every error the service emits is an RFC 9457 problem document: ``application/problem+json``
with ``type`` / ``title`` / ``status`` / ``detail`` / ``instance``. Exercised through the real
app over the chunking route, whose blank-text rejection happens before the tokenizer loads."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from pfa.api.app import create_app
from pfa.api.problems import DOMAIN_REJECTION, PROBLEM_MEDIA_TYPE, VALIDATION_ERROR, Problem


def _is_problem(r) -> dict:
    assert r.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE), r.headers["content-type"]
    body = r.json()
    assert body["status"] == r.status_code
    assert set(body) >= {"type", "title", "status", "instance"}
    return body


def test_domain_rejection_is_a_typed_problem(client: TestClient) -> None:
    r = client.post("/chunking/split", json={"text": "   ", "budget": 4})
    assert r.status_code == 422
    body = _is_problem(r)
    assert body["type"] == DOMAIN_REJECTION
    assert body["title"] == "Request rejected"
    assert body["detail"] == "empty text"
    assert body["instance"] == "/chunking/split"


def test_request_validation_failure_lists_the_offending_fields_but_not_the_input(client: TestClient) -> None:
    r = client.post("/chunking/split", json={"text": "", "budget": 0})
    assert r.status_code == 422
    body = _is_problem(r)
    assert body["type"] == VALIDATION_ERROR
    assert body["title"] == "Request validation failed"
    assert body["detail"] == "2 invalid fields: body.budget, body.text"
    locs = sorted(tuple(e["loc"]) for e in body["errors"])
    assert locs == [("body", "budget"), ("body", "text")]
    assert all(set(e) == {"loc", "msg", "type"} for e in body["errors"])


def test_unknown_path_and_wrong_method_are_about_blank_problems(client: TestClient) -> None:
    r = client.get("/nope")
    assert r.status_code == 404
    body = _is_problem(r)
    assert body == {"type": "about:blank", "title": "Not Found", "status": 404, "instance": "/nope"}

    r = client.get("/chunking/split")
    assert r.status_code == 405
    assert _is_problem(r)["title"] == "Method Not Allowed"


def test_unhandled_exception_is_a_500_problem_without_detail() -> None:
    app = create_app()

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("secret internals")

    r = TestClient(app, raise_server_exceptions=False).get("/boom")
    assert r.status_code == 500
    body = _is_problem(r)
    assert body == {"type": "about:blank", "title": "Internal Server Error", "status": 500, "instance": "/boom"}
    assert "secret" not in r.text


def test_a_route_can_raise_any_problem_with_extensions_and_headers() -> None:
    app = create_app()

    @app.get("/busy")
    def busy() -> None:
        raise Problem(
            429,
            "slow down",
            type="urn:pfa-api:problem:rate-limited",
            title="Too many requests",
            headers={"Retry-After": "7"},
            retry_after=7,
        )

    r = TestClient(app).get("/busy")
    assert r.status_code == 429 and r.headers["retry-after"] == "7"
    body = _is_problem(r)
    assert body["type"] == "urn:pfa-api:problem:rate-limited"
    assert body["detail"] == "slow down" and body["retry_after"] == 7


def test_openapi_documents_errors_as_problem_details(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    problem = spec["components"]["schemas"]["ProblemDetails"]
    assert set(problem["properties"]) == {"type", "title", "status", "detail", "instance"}
    assert problem.get("additionalProperties", True) is not False  # extension members are allowed
    assert "HTTPValidationError" not in spec["components"]["schemas"]

    split = spec["paths"]["/chunking/split"]["post"]["responses"]
    assert list(split["422"]["content"]) == [PROBLEM_MEDIA_TYPE]
    assert split["422"]["content"][PROBLEM_MEDIA_TYPE]["schema"] == {"$ref": "#/components/schemas/ProblemDetails"}
    assert list(split["default"]["content"]) == [PROBLEM_MEDIA_TYPE]
    health = spec["paths"]["/health"]["get"]["responses"]
    assert "422" not in health and PROBLEM_MEDIA_TYPE in health["default"]["content"]


def test_bodiless_statuses_stay_bodiless() -> None:
    from fastapi import HTTPException

    app = create_app()

    @app.get("/gone")
    def gone() -> None:
        raise HTTPException(status_code=204)

    @app.get("/same")
    def same() -> None:
        raise Problem(304, headers={"ETag": '"v1"'})

    client = TestClient(app)
    r = client.get("/gone")
    assert r.status_code == 204 and r.content == b"" and "content-type" not in r.headers
    r = client.get("/same")
    assert r.status_code == 304 and r.content == b"" and r.headers["etag"] == '"v1"'


def test_a_route_may_name_a_more_specific_instance() -> None:
    app = create_app()

    @app.get("/documents/{doc_id}")
    def doc(doc_id: str) -> None:
        raise Problem(404, f"no document {doc_id}", instance=f"/documents/{doc_id}#missing")

    body = _is_problem(TestClient(app).get("/documents/42"))
    assert body["instance"] == "/documents/42#missing" and body["detail"] == "no document 42"


def test_programmer_errors_fail_at_raise_time_not_in_the_handler() -> None:
    import pytest

    with pytest.raises(ValueError):
        Problem(42)
    with pytest.raises(TypeError):
        Problem(409, "x", status=500)
    with pytest.raises(TypeError):
        Problem(409, "x", title="a", detail="b")


def test_extension_members_pass_through_even_when_none() -> None:
    app = create_app()

    @app.get("/limited")
    def limited() -> None:
        raise Problem(429, "slow down", retry_after=None)

    body = _is_problem(TestClient(app).get("/limited"))
    assert "retry_after" in body and body["retry_after"] is None


def test_validation_detail_counts_locations_not_errors(client: TestClient) -> None:
    # one location (body) with one error: the sentence and the list agree
    r = client.post("/chunking/split", content=b"not json", headers={"content-type": "application/json"})
    body = _is_problem(r)
    assert body["detail"].startswith("1 invalid field: body")
    assert len({tuple(e["loc"]) for e in body["errors"]}) == 1


def test_installing_twice_is_a_no_op(client: TestClient) -> None:
    from pfa.api.problems import install_problem_details

    app = client.app
    before = app.openapi
    install_problem_details(app)
    assert app.openapi is before


# ---------------------------------------------------------------- edge validation (shape, size, hygiene)


def _errors(r) -> dict[tuple, str]:
    return {tuple(e["loc"]): e["type"] for e in r.json()["errors"]}


def test_unknown_fields_are_rejected_not_ignored(client: TestClient) -> None:
    r = client.post("/chunking/split", json={"text": "hello", "budget": 4, "bugdet": 9})
    assert r.status_code == 422 and r.json()["type"] == VALIDATION_ERROR
    assert _errors(r) == {("body", "bugdet"): "extra_forbidden"}
    r = client.post("/embeddings/query", json={"questions": "typo"})
    assert set(_errors(r)) == {("body", "question"), ("body", "questions")}


def test_control_characters_are_rejected_with_their_position(client: TestClient, words_app: FastAPI) -> None:
    r = client.post("/chunking/split", json={"text": "ok\u0000bad", "budget": 4})
    assert r.status_code == 422
    (err,) = r.json()["errors"]
    assert err["loc"] == ["body", "text"] and "U+0000" in err["msg"] and "position 2" in err["msg"]
    # tabs and newlines are text, not control noise — checked over the fake chunking, never the real tokenizer
    assert TestClient(words_app).post("/chunking/split", json={"text": "line one\n\tline two", "budget": 400}).status_code == 200


def test_list_items_are_validated_individually(client: TestClient) -> None:
    r = client.post("/embeddings/passages", json={"texts": ["fine", "", "x\u0007y"]})
    assert r.status_code == 422
    errs = _errors(r)
    assert ("body", "texts", 1) in errs and ("body", "texts", 2) in errs and ("body", "texts", 0) not in errs
    assert r.json()["detail"] == "2 invalid fields: body.texts.1, body.texts.2"


def test_total_work_per_request_is_bounded(client: TestClient) -> None:
    eleven = ["y" * 190_000] * 11  # each under the per-text cap, together over the request cap
    r = client.post("/embeddings/passages", json={"texts": eleven})
    assert r.status_code == 422 and r.json()["type"] == VALIDATION_ERROR
    (err,) = r.json()["errors"]
    assert err["loc"] == ["body"] and "limit is 2,000,000" in err["msg"]
    r = client.post("/embeddings/similarity", json={"question": "q", "candidates": eleven})
    assert r.status_code == 422 and "question and candidates" in r.json()["errors"][0]["msg"]


def test_openapi_advertises_the_bounds(client: TestClient) -> None:
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    assert schemas["QuestionRequest"]["additionalProperties"] is False
    assert schemas["QuestionRequest"]["properties"]["question"]["maxLength"] == 20_000
    texts = schemas["PassagesRequest"]["properties"]["texts"]
    assert texts["maxItems"] == 256 and texts["items"]["maxLength"] == 200_000 and texts["items"]["minLength"] == 1
