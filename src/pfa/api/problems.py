"""RFC 9457 — Problem Details for HTTP APIs.

Every error the service emits leaves as ``application/problem+json`` with the members the
RFC defines: ``type`` (a URI naming the kind of problem), ``title`` (a short human summary
of that kind), ``status``, ``detail`` (what went wrong this time) and ``instance`` (the
request path unless the raiser names a more specific one). Extension members ride
alongside; validation problems add ``errors``.

Four sources of errors are covered, so a client never has to parse two formats:

- a route's domain rejection — ``raise Problem.domain_rejection(result.error)``;
- any other ``Problem`` a route raises (``Problem(409, "…", type=…, **extensions)``);
- FastAPI's request validation failure (``RequestValidationError``) and its ``HTTPException``
  (404 for an unknown path, 405, anything a dependency raises);
- an unhandled exception → 500 with no detail, so internals never leak (the traceback is logged).

Every document carries ``trace_id`` — the W3C Trace Context id the log lines have — so a
client can quote what the operator can grep (``docs/decisions/0001-logging.md``).

``install_problem_details(app)`` wires all of it and rewrites the OpenAPI document so error
responses are documented as ``application/problem+json`` ``ProblemDetails`` with examples:
the ``422`` of every operation with a body shows a validation failure (built from the
body's required fields) next to the domain rejections the route declared with
``domain_rejections(...)``; any other problem response a route declares goes through
``problem_response(...)``. Examples are rendered by the same code that answers requests,
so the document cannot drift from the wire.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from pydantic.json_schema import JsonDict
from starlette.exceptions import HTTPException as StarletteHTTPException

from .trace import current_trace

log = logging.getLogger("pfa.api.problems")

PROBLEM_MEDIA_TYPE = "application/problem+json"
ABOUT_BLANK = "about:blank"  # RFC 9457 §4.2.1: the type whose title is the HTTP status phrase

# Problem types this service defines. URNs rather than URLs: stable identifiers that do not
# pretend to be dereferenceable. ``about:blank`` covers everything that is just an HTTP status.
VALIDATION_ERROR = "urn:pfa-api:problem:validation-error"
DOMAIN_REJECTION = "urn:pfa-api:problem:domain-rejection"
NOT_READY = "urn:pfa-api:problem:not-ready"  # /ready while heavy components load (503)
MODEL_BUSY = "urn:pfa-api:problem:model-busy"  # inference gate did not open in time (503)

# Statuses whose response has no body (RFC 9110 §15.3.5, §15.4.5) — never a problem document.
_BODILESS = frozenset({204, 304})
# The RFC members a ``Problem`` extension may not shadow; they are named constructor arguments.
_RESERVED = frozenset({"type", "title", "status", "detail", "instance"})

EXAMPLE_TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"  # the W3C Trace Context example id, in every documented example

_EXAMPLE: JsonDict = {
    "type": DOMAIN_REJECTION,
    "title": "Request rejected",
    "status": 422,
    "detail": "empty question",
    "instance": "/embeddings/query",
    "trace_id": EXAMPLE_TRACE_ID,
}


class ProblemDetails(BaseModel):
    """The response body — the RFC 9457 §3.1 members. ``extra="allow"`` is how extension
    members (``errors`` on a validation problem) ride along and are documented."""

    model_config = ConfigDict(extra="allow", json_schema_extra={"examples": [_EXAMPLE]})

    type: str = Field(default=ABOUT_BLANK, description="URI identifying the problem type")
    title: str = Field(description="Short, human-readable summary of the problem type")
    status: int = Field(ge=100, le=599, description="HTTP status code of this response")
    detail: str | None = Field(default=None, description="Human-readable explanation of this occurrence")
    instance: str | None = Field(default=None, description="URI reference identifying this occurrence (the request path)")
    trace_id: str | None = Field(default=None, description="W3C Trace Context trace id of the request; quote it when reporting")


class ProblemResponse(JSONResponse):
    media_type = PROBLEM_MEDIA_TYPE


class Problem(Exception):
    """Raise from a route to answer with a problem document. The route still decides the
    status; this is the one shape every error takes on the wire.

    ``type`` defaults to ``about:blank`` and ``title`` to the status phrase; ``instance``
    defaults to the request path. Keyword arguments beyond the named ones become extension
    members of the document. Programmer errors (a status outside 100–599, an extension
    named like an RFC member) fail here, at raise time, rather than inside the handler.
    """

    def __init__(
        self,
        status: int,
        detail: str | None = None,
        *,
        type: str = ABOUT_BLANK,
        title: str | None = None,
        instance: str | None = None,
        headers: Mapping[str, str] | None = None,
        **extensions: Any,
    ) -> None:
        if not 100 <= int(status) <= 599:
            raise ValueError(f"Problem status must be an HTTP status code (100–599), got {status!r}")
        clash = _RESERVED.intersection(extensions)
        if clash:
            raise TypeError(f"extension members may not shadow RFC 9457 members: {sorted(clash)}")
        self.status = int(status)
        self.detail = detail
        self.type = type
        self.title = title or _phrase(self.status)
        self.instance = instance
        self.headers = headers
        self.extensions = extensions
        super().__init__(detail or self.title)

    @classmethod
    def domain_rejection(cls, detail: str | None) -> Problem:
        """A handler said the request did not make sense (``Result.ok`` is false): 422."""
        return cls(422, detail, type=DOMAIN_REJECTION, title="Request rejected")

    def to_details(self, instance: str | None, trace_id: str | None = None) -> ProblemDetails:
        """The document; ``instance`` is the fallback (the request path) if the raiser set none."""
        return ProblemDetails(
            type=self.type,
            title=self.title,
            status=self.status,
            detail=self.detail,
            instance=self.instance or instance,
            trace_id=trace_id,
            **self.extensions,
        )

    def to_body(self, instance: str | None, trace_id: str | None = None) -> dict[str, Any]:
        """The JSON body: RFC members that are ``None`` are omitted (they are optional), as is
        ``trace_id`` outside a request; extension members are passed through exactly as given,
        ``None`` included."""
        details = self.to_details(instance, trace_id)
        omit = {name for name in ("detail", "instance", "trace_id") if getattr(details, name) is None}
        return details.model_dump(exclude=omit)


def _phrase(status: int) -> str:
    try:
        return HTTPStatus(status).phrase
    except ValueError:
        return "Error"


def _respond(request: Request, problem: Problem) -> Response:
    trace = current_trace.get()
    if problem.status in _BODILESS:
        return Response(status_code=problem.status, headers=problem.headers)
    body = problem.to_body(request.url.path, trace.trace_id if trace else None)
    return ProblemResponse(body, status_code=problem.status, headers=problem.headers)


# ---------------------------------------------------------------- exception handlers


async def _on_problem(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, Problem)
    return _respond(request, exc)


async def _on_http_exception(request: Request, exc: Exception) -> Response:
    """Starlette/FastAPI ``HTTPException`` → an ``about:blank`` problem. A string detail is
    the RFC ``detail`` (dropped when it merely repeats the status phrase); anything
    structured goes under ``errors``, because ``detail`` must be text. 204/304 stay bodiless,
    as Starlette's own handler keeps them."""
    assert isinstance(exc, StarletteHTTPException)
    extensions: dict[str, Any] = {}
    detail: str | None = None
    raw: Any = exc.detail  # typed ``str`` by Starlette, but FastAPI lets routes pass any JSON-able value
    if isinstance(raw, str):
        detail = raw if raw != _phrase(exc.status_code) else None
    elif raw is not None:
        extensions["errors"] = jsonable_encoder(raw)
    return _respond(request, Problem(exc.status_code, detail, headers=exc.headers, **extensions))


async def _on_validation_error(request: Request, exc: Exception) -> Response:
    """FastAPI's 422 for a request body/query/path that does not fit the models. ``errors``
    carries each failure's location, message and pydantic type — never the offending input,
    which may be a 200 kB document."""
    assert isinstance(exc, RequestValidationError)
    errors = [{"loc": list(e.get("loc", ())), "msg": e.get("msg", ""), "type": e.get("type", "")} for e in exc.errors()]
    return _respond(request, _validation_problem(errors))


def _validation_problem(errors: list[dict[str, Any]]) -> Problem:
    """``detail`` names the fields; ``errors`` carries each failure. Also what the document shows."""
    where = sorted({".".join(str(p) for p in e["loc"]) for e in errors})
    noun = "field" if len(where) == 1 else "fields"
    detail = f"{len(where)} invalid {noun}: {', '.join(where)}" if where else "request is invalid"
    return Problem(422, detail, type=VALIDATION_ERROR, title="Request validation failed", errors=errors)


async def _on_unhandled(request: Request, exc: Exception) -> Response:
    """A bug. No ``detail``: the traceback belongs in the server log, not on the wire. This
    handler runs above the trace middleware (Starlette's server-error layer), so it adds the
    ``traceparent`` header itself; the trace id is still in the context."""
    log.error("unhandled error", exc_info=exc, extra={"msgid": "error", "method": request.method, "path": request.url.path})
    trace = current_trace.get()
    headers = {"traceparent": trace.traceparent} if trace else None
    return _respond(request, Problem(500, headers=headers))


# ---------------------------------------------------------------- installation


def install_problem_details(app: FastAPI) -> None:
    """Make every error response of ``app`` an RFC 9457 problem document, and document it so.
    Idempotent: installing twice neither double-registers nor double-wraps."""
    if getattr(app.state, "problem_details_installed", False):
        return
    app.add_exception_handler(Problem, _on_problem)
    app.add_exception_handler(StarletteHTTPException, _on_http_exception)
    app.add_exception_handler(RequestValidationError, _on_validation_error)
    app.add_exception_handler(Exception, _on_unhandled)
    app.openapi = _documenting(app, app.openapi)  # type: ignore[method-assign]
    app.state.problem_details_installed = True


_REF = {"$ref": "#/components/schemas/ProblemDetails"}
_REJECTION_DESCRIPTION = (
    "Validation error (the body does not fit the model: `errors` names each field) "
    "or domain rejection (it fits, but made no sense to the handler: `detail` says why)"
)


def problem_response(
    description: str, examples: Mapping[str, Problem], *, instance: str, headers: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """An OpenAPI response entry for a route's ``responses={<status>: ...}``: a problem document
    with one named example per ``Problem`` in ``examples``, rendered exactly as the wire would
    carry it for a request to ``instance`` (the route's path). ``headers`` maps a response header
    to its description (``{"Retry-After": "seconds to wait"}``)."""
    rendered = {name: _example(problem, instance) for name, problem in examples.items()}
    response: dict[str, Any] = {"description": description, "content": {PROBLEM_MEDIA_TYPE: {"schema": _REF, "examples": rendered}}}
    if headers:
        response["headers"] = {name: _header(about) for name, about in headers.items()}
    return response


def _example(problem: Problem, instance: str) -> dict[str, Any]:
    return {"summary": problem.title, "value": problem.to_body(instance, EXAMPLE_TRACE_ID)}


def _header(about: str) -> dict[str, Any]:
    return {"description": about, "schema": {"type": "string"}}


def domain_rejections(instance: str, *details: str) -> dict[str, Any]:
    """The ``422`` entry of the route at ``instance``: one example per reason its handler may
    reject a request (``Result.failure("empty question")`` → ``"empty question"``). The
    validation example is added when the document is built, so a route declares only its own."""
    return problem_response(_REJECTION_DESCRIPTION, {_example_name(d): Problem.domain_rejection(d) for d in details}, instance=instance)


def _example_name(detail: str) -> str:
    """OpenAPI example keys are ``[A-Za-z0-9._-]+``: ``"empty question"`` → ``empty_question``."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", detail).strip("_") or "rejected"


def _documenting(app: FastAPI, original: Callable[[], dict[str, Any]]) -> Callable[[], dict[str, Any]]:
    """Wrap ``app.openapi``: the document is rewritten once, on first use, and cached as FastAPI caches its own."""

    def openapi() -> dict[str, Any]:
        if not app.openapi_schema:
            app.openapi_schema = _with_problem_responses(original())
        return app.openapi_schema

    return openapi


def _with_problem_responses(schema: dict[str, Any]) -> dict[str, Any]:
    """Error responses described as problem documents: FastAPI's auto-generated 422
    (``HTTPValidationError`` as ``application/json``) becomes a ``ProblemDetails`` as
    ``application/problem+json`` with a validation example next to the route's domain
    rejections, and every operation gets a ``default`` problem response for everything else."""
    schemas = schema.setdefault("components", {}).setdefault("schemas", {})
    schemas["ProblemDetails"] = ProblemDetails.model_json_schema()
    for path, operations in schema.get("paths", {}).items():
        for operation in operations.values():
            if isinstance(operation, dict) and "responses" in operation:
                _document_errors(path, operation, schemas)
    _drop_if_unreferenced(schema, "HTTPValidationError", "ValidationError")
    return schema


def _document_errors(path: str, operation: dict[str, Any], schemas: dict[str, Any]) -> None:
    """One operation's error responses: its ``422`` and a ``default`` for the rest."""
    responses = operation["responses"]
    if "422" in responses:
        responses["422"] = _rejections_after_validation(path, responses["422"], _required_body_fields(operation, schemas))
    responses.setdefault(
        "default", problem_response("Any other error (RFC 9457 problem details)", {"internal_error": Problem(500)}, instance=path)
    )


def _rejections_after_validation(path: str, declared: Any, required: list[str]) -> dict[str, Any]:
    """The ``422`` entry: the validation example (an empty body, so each required field is
    missing) ahead of the rejections the route declared with ``domain_rejections``."""
    missing = [{"loc": ["body", name], "msg": "Field required", "type": "missing"} for name in required]
    response = problem_response(_REJECTION_DESCRIPTION, {"validation_error": _validation_problem(missing)}, instance=path)
    response["content"][PROBLEM_MEDIA_TYPE]["examples"].update(_problem_examples(declared))
    return response


def _problem_examples(response: Any) -> dict[str, Any]:
    """The named examples of a problem response; ``{}`` for anything else (FastAPI's own 422)."""
    if not isinstance(response, dict):
        return {}
    media = response.get("content", {}).get(PROBLEM_MEDIA_TYPE, {})
    examples = media.get("examples", {})
    return examples if isinstance(examples, dict) else {}


def _required_body_fields(operation: dict[str, Any], schemas: dict[str, Any]) -> list[str]:
    """The required properties of the operation's JSON body model, in declaration order."""
    body = operation.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema", {})
    name = str(body.get("$ref", "")).rpartition("/")[2]
    required = schemas.get(name, {}).get("required", [])
    return [str(field) for field in required]


def _drop_if_unreferenced(schema: dict[str, Any], *names: str) -> None:
    """FastAPI's validation-error schemas go once nothing points at them: the outer one first,
    then the inner one it referenced."""
    for name in names:
        if _referenced(schema, name):
            return
        schema["components"]["schemas"].pop(name, None)


def _referenced(schema: dict[str, Any], name: str) -> bool:
    """Is ``#/components/schemas/<name>`` still pointed at from anywhere but its own definition?"""
    ref = f"#/components/schemas/{name}"
    others = {k: v for k, v in schema.get("components", {}).get("schemas", {}).items() if k != name}
    stack: list[Any] = [schema.get("paths", {}), others]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if node.get("$ref") == ref:
                return True
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return False
