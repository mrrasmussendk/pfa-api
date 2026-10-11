"""``tools/openapi_compat.py``: which changes to the HTTP API break a client written against the old spec."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from openapi_compat import breaking_changes, major

SPEC: dict[str, Any] = {
    "info": {"version": "1.2.0"},
    "paths": {
        "/split": {
            "post": {
                "requestBody": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/SplitRequest"}}}},
                "responses": {"200": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/SplitOut"}}}}},
            }
        }
    },
    "components": {
        "schemas": {
            "SplitRequest": {
                "type": "object",
                "additionalProperties": False,
                "required": ["text"],
                "properties": {
                    "text": {"type": "string", "maxLength": 1000},
                    "budget": {"type": "integer", "minimum": 1, "maximum": 512},
                    "mode": {"type": "string", "enum": ["fast", "exact"]},
                },
            },
            "SplitOut": {
                "type": "object",
                "required": ["chunks", "budget"],
                "properties": {"chunks": {"type": "array", "items": {"type": "string"}}, "budget": {"type": "integer"}},
            },
        }
    },
}


def _changed(edit) -> list[str]:
    new = copy.deepcopy(SPEC)
    edit(new)
    return breaking_changes(SPEC, new)


def _schemas(spec: dict[str, Any]) -> dict[str, Any]:
    return spec["components"]["schemas"]


def test_an_unchanged_spec_breaks_nothing() -> None:
    assert breaking_changes(SPEC, copy.deepcopy(SPEC)) == []


@pytest.mark.parametrize(
    ("edit", "expected"),
    [
        (lambda s: s["paths"].pop("/split"), "POST /split: removed"),
        (lambda s: _schemas(s)["SplitRequest"]["required"].append("budget"), "POST /split request.budget: now required"),
        (
            lambda s: _schemas(s)["SplitRequest"]["properties"].pop("mode"),
            "POST /split request.mode: removed, and unknown fields are rejected",
        ),
        (
            lambda s: _schemas(s)["SplitRequest"]["properties"]["text"].update(maxLength=500),
            "POST /split request.text: maxLength lowered to 500",
        ),
        (
            lambda s: _schemas(s)["SplitRequest"]["properties"]["budget"].update(minimum=8),
            "POST /split request.budget: minimum raised to 8",
        ),
        (
            lambda s: _schemas(s)["SplitRequest"]["properties"]["budget"].update(type="string"),
            "POST /split request.budget: type integer became string",
        ),
        (
            lambda s: _schemas(s)["SplitRequest"]["properties"]["mode"].update(enum=["fast"]),
            "POST /split request.mode: no longer accepts ['exact']",
        ),
        (lambda s: _schemas(s)["SplitOut"]["properties"].pop("budget"), "POST /split 200.budget: removed"),
        (lambda s: _schemas(s)["SplitOut"]["required"].remove("budget"), "POST /split 200.budget: no longer always present"),
        (
            lambda s: _schemas(s)["SplitOut"]["properties"]["chunks"]["items"].update(type="object"),
            "POST /split 200.chunks[]: type string became object",
        ),
        (lambda s: s["paths"]["/split"]["post"]["responses"].pop("200"), "POST /split: success response 200 removed"),
    ],
    ids=[
        "operation-removed",
        "field-now-required",
        "strict-field-removed",
        "max-lowered",
        "min-raised",
        "request-type",
        "enum-narrowed",
        "response-field-removed",
        "response-field-optional",
        "response-item-type",
        "success-response-removed",
    ],
)
def test_a_breaking_change_is_named_where_it_happened(edit, expected: str) -> None:
    assert _changed(edit) == [expected]


@pytest.mark.parametrize(
    "edit",
    [
        lambda s: _schemas(s)["SplitRequest"]["properties"].update(model={"type": "string"}),
        lambda s: _schemas(s)["SplitRequest"]["properties"]["text"].update(maxLength=2000),
        lambda s: _schemas(s)["SplitRequest"]["properties"]["mode"].update(enum=["fast", "exact", "lazy"]),
        lambda s: _schemas(s)["SplitOut"]["properties"].update(model={"type": "string"}),
        lambda s: s["paths"].update({"/count": {"post": {"responses": {}}}}),
        lambda s: s["paths"]["/split"]["post"]["responses"].update({"422": {}}),
    ],
    ids=["optional-request-field", "max-raised", "enum-widened", "response-field-added", "operation-added", "error-documented"],
)
def test_additions_break_nothing(edit) -> None:
    assert _changed(edit) == []


def test_the_major_version_is_read_from_info() -> None:
    assert major(SPEC) == 1 and major({"info": {"version": "x"}}) == 0
