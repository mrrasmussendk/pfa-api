"""``tools/openapi_compat.py``: which changes to the HTTP API break a client written against the old spec."""

from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from openapi_compat import breaking_changes, compare, major

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


# ---------------------------------------------------------------- against the merge base


def _repo_with_snapshot(root: Path) -> Path:
    """A repository whose committed docs/openapi.json is ``SPEC``."""
    (root / "docs").mkdir()
    (root / "docs" / "openapi.json").write_text(json.dumps(SPEC), encoding="utf-8")
    for args in (["init", "-q"], ["add", "-A"], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "base"]):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    return root


def _working(root: Path, edit) -> None:
    new = copy.deepcopy(SPEC)
    edit(new)
    (root / "docs" / "openapi.json").write_text(json.dumps(new), encoding="utf-8")


def test_a_breaking_change_against_the_base_fails_and_is_listed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _repo_with_snapshot(tmp_path)
    _working(root, lambda s: s["paths"].pop("/split"))

    assert compare("HEAD", root) == 1
    assert "- POST /split: removed" in capsys.readouterr().out


def test_a_higher_major_version_declares_the_break(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _repo_with_snapshot(tmp_path)
    _working(root, lambda s: (s["paths"].pop("/split"), s["info"].update(version="2.0.0")))

    assert compare("HEAD", root) == 0
    assert "declared by the major version" in capsys.readouterr().out


def test_an_additive_change_against_the_base_passes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _repo_with_snapshot(tmp_path)
    _working(root, lambda s: s["paths"].update({"/count": {"post": {"responses": {}}}}))

    assert compare("HEAD", root) == 0 and "compatible with HEAD" in capsys.readouterr().out


def test_a_base_without_a_snapshot_has_nothing_to_compare(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _repo_with_snapshot(tmp_path)
    subprocess.run(["git", "rm", "-q", "docs/openapi.json"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "drop"], cwd=root, check=True)
    (root / "docs").mkdir(exist_ok=True)
    (root / "docs" / "openapi.json").write_text(json.dumps(SPEC), encoding="utf-8")

    assert compare("HEAD", root) == 0 and "nothing to compare" in capsys.readouterr().out


def test_a_base_that_git_would_read_as_an_option_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="not an option"):
        compare("--output=/tmp/x", tmp_path)


def test_a_change_inside_a_schema_referenced_through_all_of_is_found() -> None:
    old = copy.deepcopy(SPEC)
    _schemas(old)["Mode"] = {"type": "string", "enum": ["fast", "exact"]}
    _schemas(old)["SplitRequest"]["properties"]["mode"] = {"allOf": [{"$ref": "#/components/schemas/Mode"}], "description": "How to cut"}
    new = copy.deepcopy(old)
    _schemas(new)["Mode"]["enum"] = ["fast"]

    assert breaking_changes(old, new) == ["POST /split request.mode: no longer accepts ['exact']"]


def test_a_response_that_may_return_a_new_enum_value_breaks_its_readers_and_one_that_returns_fewer_does_not() -> None:
    old = copy.deepcopy(SPEC)
    _schemas(old)["SplitOut"]["properties"]["state"] = {"type": "string", "enum": ["ok", "partial"]}
    wider, narrower = copy.deepcopy(old), copy.deepcopy(old)
    _schemas(wider)["SplitOut"]["properties"]["state"]["enum"] = ["ok", "partial", "failed"]
    _schemas(narrower)["SplitOut"]["properties"]["state"]["enum"] = ["ok"]

    assert breaking_changes(old, wider) == ["POST /split 200.state: may now return ['failed']"]
    assert breaking_changes(old, narrower) == []
