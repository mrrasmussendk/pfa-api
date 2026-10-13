"""What Jev is asked about: each function the change touches, read from the working tree, so every question
is about one named function and a failing answer says where. Routes are marked, tests left out, and the
sources fit a budget."""

from __future__ import annotations

from pathlib import Path

from review_support import sample_map, touching

from heimdall.commands.review.items import changed_functions

ROUTES = "def list_items():\n    return []\n\n\ndef add_item(name):\n    return name\n"
NESTED = "def outer(xs):\n    def inner(x):\n        return x + 1\n    return [inner(x) for x in xs]\n"


def _write(root: Path, path: str, text: str) -> None:
    (root / path).parent.mkdir(parents=True, exist_ok=True)
    (root / path).write_text(text, encoding="utf-8")


def test_only_the_functions_the_change_touches_are_items(tmp_path: Path) -> None:
    _write(tmp_path, "src/pfa/api/routes/kvad.py", ROUTES)

    items, skipped = changed_functions(str(tmp_path), [touching("src/pfa/api/routes/kvad.py", 6)], sample_map())

    assert [(i.id, i.name, i.line, i.route) for i in items] == [("f1", "add_item", 5, True)] and skipped == 0
    assert items[0].source == "def add_item(name):\n    return name"


def test_a_touched_inner_function_is_read_as_part_of_its_outer_one(tmp_path: Path) -> None:
    _write(tmp_path, "src/pfa/features/kvad/internal/engine.py", NESTED)

    items, _ = changed_functions(str(tmp_path), [touching("src/pfa/features/kvad/internal/engine.py", 3)], sample_map())

    assert [(i.name, i.route) for i in items] == [("outer", False)]


def test_tests_deleted_files_and_other_languages_are_not_items(tmp_path: Path) -> None:
    _write(tmp_path, "tests/api/test_kvad.py", "def test_it():\n    assert True\n")
    _write(tmp_path, "web/app.js", "function f() {}\n")
    changes = [
        touching("tests/api/test_kvad.py", 2),
        touching("web/app.js", 1),
        touching("src/pfa/gone.py", 1, status="deleted"),
    ]

    assert changed_functions(str(tmp_path), changes, sample_map()) == ([], 0)


def test_the_application_comes_before_the_tooling_and_the_budget_skips_the_rest(tmp_path: Path) -> None:
    big = "def tool():\n" + "    x = 1\n" * 400
    _write(tmp_path, "tools/t.py", big)
    _write(tmp_path, "src/pfa/api/routes/kvad.py", ROUTES)
    changes = [touching("tools/t.py", 2), touching("src/pfa/api/routes/kvad.py", 2)]

    items, skipped = changed_functions(str(tmp_path), changes, sample_map(), budget=120)

    assert [i.name for i in items] == ["list_items"] and skipped == 1


def test_a_function_over_its_own_budget_is_cut_at_a_line_and_says_so(tmp_path: Path) -> None:
    body = "".join(f"    total_{i} = compute_value_number_{i}(previous_{i})\n" for i in range(400))
    _write(tmp_path, "src/pfa/features/kvad/internal/engine.py", "def big(previous):\n" + body)

    items, skipped = changed_functions(str(tmp_path), [touching("src/pfa/features/kvad/internal/engine.py", 2)], sample_map())

    source = items[0].source
    assert skipped == 0 and source.startswith("def big(previous):\n")
    assert source.endswith("\n# … the rest of the function is not shown") and len(source) < len(body)
    assert all(line.startswith(("def ", "    total_", "# …")) for line in source.splitlines())  # cut between lines, never inside one
