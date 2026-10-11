from __future__ import annotations

import json

import pytest
from conftest import TempRepo


def _write_sample_tree(repo: TempRepo) -> None:
    repo.write_file("src/pfa/features/kvad/feature.json", '{"depends_on": ["rune", "kernel"]}\n')
    repo.write_file("src/pfa/features/rune/feature.json", '{"depends_on": []}\n')
    repo.write_file("src/pfa/features/kvad/AGENTS.md", "# kvad\nhand-written notes stay.\n")


def test_map_emits_indent2_map_json(repo: TempRepo) -> None:
    _write_sample_tree(repo)
    code, stdout, _ = repo.run("", "map", "--root", "src", "--budget", "15000")
    assert code == 0
    assert "heimdall map: 2 features -> .heimdall/map.json; AGENTS.md deps regenerated" in stdout
    expected = {
        "kernel": "kernel",
        "kernel_dir": "src/pfa/kernel",
        "slices_dir": "src/pfa/features",
        "app_dir": "src/pfa",
        "routes_dir": "src/pfa/api/routes",
        "slices": {
            "kvad": {"path": "src/pfa/features/kvad", "depends_on": ["rune"], "budget": 15000, "fan_in": 0, "routes": []},
            "rune": {"path": "src/pfa/features/rune", "depends_on": [], "budget": 15000, "fan_in": 1, "routes": []},
        },
    }
    assert repo.read_file(".heimdall/map.json") == json.dumps(expected, indent=2)


def test_the_map_records_each_feature_s_routes(repo: TempRepo) -> None:
    _write_sample_tree(repo)
    repo.write_file(
        "src/pfa/api/routes/kvad.py",
        'router = APIRouter(prefix="/kvad")\n\n\n@router.post("/squares")\ndef squares(): ...\n\n\n@router.get("/{id}")\ndef one(): ...\n',
    )

    repo.run("", "map", "--root", "src")

    assert json.loads(repo.read_file(".heimdall/map.json"))["slices"]["kvad"]["routes"] == ["POST /kvad/squares", "GET /kvad/{id}"]


def _generated(name: str, deps: str) -> str:
    """The lines ``heimdall map`` writes for a feature with no routes, no scenarios and no code."""
    return (
        f"<!--heimdall:deps-->depends on: {deps} + kernel<!--/heimdall:deps-->\n"
        "<!--heimdall:routes-->routes: (none)<!--/heimdall:routes-->\n"
        f"<!--heimdall:scenarios-->scenarios: (none) — add them with `heimdall e2e new {name}/<name>`<!--/heimdall:scenarios-->\n"
        "<!--heimdall:budget-->budget: ≈0% of 15,000 tokens used — `eitri check` has the exact count<!--/heimdall:budget-->\n"
    )


def test_map_injects_and_refreshes_agents_md_markers_idempotently(repo: TempRepo) -> None:
    _write_sample_tree(repo)
    repo.run("", "map", "--root", "src")
    assert repo.read_file("src/pfa/features/kvad/AGENTS.md") == "# kvad\nhand-written notes stay.\n" + _generated("kvad", "rune")
    # rune had no AGENTS.md -> created from scratch
    assert repo.read_file("src/pfa/features/rune/AGENTS.md") == "# rune\n" + _generated("rune", "(none)")
    # rerun: markers replaced in place, no duplication
    repo.run("", "map", "--root", "src")
    assert repo.read_file("src/pfa/features/kvad/AGENTS.md") == "# kvad\nhand-written notes stay.\n" + _generated("kvad", "rune")


def test_a_new_marker_goes_after_the_ones_before_it_and_the_author_s_text_stays_below(repo: TempRepo) -> None:
    _write_sample_tree(repo)
    repo.write_file(
        "src/pfa/features/rune/AGENTS.md",
        "# rune\n<!--heimdall:deps-->old<!--/heimdall:deps-->\n<!--heimdall:routes-->old<!--/heimdall:routes-->\n\nNotes.\n",
    )

    repo.run("", "map", "--root", "src")

    assert repo.read_file("src/pfa/features/rune/AGENTS.md") == "# rune\n" + _generated("rune", "(none)") + "\nNotes.\n"


def test_a_feature_names_its_scenarios_and_its_budget_headroom(repo: TempRepo) -> None:
    _write_sample_tree(repo)
    repo.write_file(
        "tests/e2e/kvad.feature", "@API\nFeature: kvad\n\n  Scenario: squares\n    Given a\n\n  Scenario: rounds\n    Given b\n"
    )
    repo.write_file("src/pfa/features/kvad/internal/big.py", "x = 'word ' * 1\n" * 650)

    repo.run("", "map", "--root", ".", "--budget", "15000")

    text = repo.read_file("src/pfa/features/kvad/AGENTS.md")
    assert "scenarios: `tests/e2e/kvad.feature` — squares, rounds" in text
    assert "budget: ≈30% of 15,000 tokens used" in text


def test_map_creates_and_refreshes_the_root_agents_md_block(repo: TempRepo) -> None:
    _write_sample_tree(repo)
    # no root AGENTS.md -> created with the generated block
    repo.run("", "map", "--root", "src")
    txt = repo.read_file("AGENTS.md")
    assert txt.startswith("# Agents\n\n## Feature map\n\n<!--heimdall:map-->\n")
    assert "| kvad | rune | 0 | ≈0% | `src/pfa/features/kvad/contract/` | (none) |" in txt
    assert "| rune | (none) | 1 | ≈0% | `src/pfa/features/rune/contract/` | (none) |" in txt
    assert txt.count("<!--heimdall:map-->") == 1 and txt.endswith("<!--/heimdall:map-->\n")
    # hand-written prose around the markers survives, the block is replaced in place
    repo.write_file("AGENTS.md", "# Mine\nintro stays.\n\n<!--heimdall:map-->\nstale\n<!--/heimdall:map-->\n\noutro stays.\n")
    repo.write_file("src/pfa/features/galdr/feature.json", '{"depends_on": ["rune"]}\n')
    repo.run("", "map", "--root", "src")
    txt = repo.read_file("AGENTS.md")
    assert txt.startswith("# Mine\nintro stays.\n\n<!--heimdall:map-->\n") and txt.endswith("<!--/heimdall:map-->\n\noutro stays.\n")
    assert "stale" not in txt and "| galdr | rune | 0 |" in txt and "| rune | (none) | 2 |" in txt
    # an AGENTS.md without markers gets the block appended under its own heading
    repo.write_file("AGENTS.md", "# Mine\nno markers yet.\n")
    repo.run("", "map", "--root", "src")
    assert repo.read_file("AGENTS.md").startswith("# Mine\nno markers yet.\n\n## Feature map\n\n<!--heimdall:map-->\n")


def test_map_honors_budget_and_kernel_flags(repo: TempRepo) -> None:
    _write_sample_tree(repo)
    repo.run("", "map", "--root", "src", "--budget", "9000", "--kernel", "rune")
    m = json.loads(repo.read_file(".heimdall/map.json"))
    assert m["kernel"] == "rune"
    assert m["slices"]["kvad"]["budget"] == 9000
    # rune is now the kernel -> dropped from kvad's deps; the explicit "kernel" entry stays
    assert m["slices"]["kvad"]["depends_on"] == ["kernel"]
    assert m["slices"]["rune"]["fan_in"] == 0


def test_map_ignores_directories_without_manifest(repo: TempRepo) -> None:
    _write_sample_tree(repo)
    repo.write_file("src/pfa/features/__init__.py", "")
    repo.write_file("src/pfa/features/scratch/notes.md", "no manifest here")
    _, stdout, _ = repo.run("", "map", "--root", "src")
    assert "2 features" in stdout


def test_map_no_slices_dir_exit1(repo: TempRepo) -> None:
    code, _, stderr = repo.run("", "map", "--root", "nowhere")
    assert code == 1
    assert "no features/ under nowhere" in stderr


def test_map_usage_errors_exit2(repo: TempRepo) -> None:
    assert repo.run("", "map")[0] == 2
    assert repo.run("", "map", "--root", "src", "--budget", "lots")[0] == 2
    assert repo.run("", "map", "--bogus")[0] == 2


def test_map_inventories_routes_from_the_app_routes_module_without_importing_it(repo: TempRepo) -> None:
    _write_sample_tree(repo)
    repo.write_file(
        "src/pfa/api/routes/kvad.py",
        "import this_module_does_not_exist\n"  # proves the file is parsed, not executed
        "from fastapi import APIRouter\n"
        'router = APIRouter(prefix="/kvad", tags=["kvad"])\n'
        'other = APIRouter(prefix="/other")\n'
        '@router.post("/compose")\n'
        "def compose(): ...\n"
        '@router.get("/{stave_id}")\n'
        "async def get_one(stave_id: str): ...\n"
        '@other.delete("/x")\n'
        "def gone(): ...\n"
        "@router.on_event('startup')\n"
        "def not_a_route(): ...\n",
    )
    code, _, _ = repo.run("", "map", "--root", "src")
    assert code == 0
    assert (
        "<!--heimdall:routes-->routes: POST /kvad/compose, GET /kvad/{stave_id}, DELETE /other/x<!--/heimdall:routes-->"
        in repo.read_file("src/pfa/features/kvad/AGENTS.md")
    )
    assert "<!--heimdall:routes-->routes: (none)<!--/heimdall:routes-->" in repo.read_file("src/pfa/features/rune/AGENTS.md")
    assert (
        "| kvad | rune | 0 | ≈0% | `src/pfa/features/kvad/contract/` | `POST /kvad/compose`<br>`GET /kvad/{stave_id}`<br>`DELETE /other/x` |"
        in repo.read_file("AGENTS.md")
    )
    # a slice AGENTS.md that predates the routes marker gets the line inserted right after deps, once
    repo.write_file("src/pfa/features/rune/AGENTS.md", "# rune\n<!--heimdall:deps-->stale<!--/heimdall:deps-->\nnotes stay.\n")
    repo.run("", "map", "--root", "src")
    assert repo.read_file("src/pfa/features/rune/AGENTS.md") == "# rune\n" + _generated("rune", "(none)") + "notes stay.\n"


def test_map_records_shared_packages_from_pyproject(repo: TempRepo) -> None:
    import json

    repo.write_file("src/pfa/features/rune/feature.json", '{"depends_on": []}\n')
    repo.write_file("pyproject.toml", '[tool.other]\nx = 1\n\n[tool.heimdall]\nshared = ["http_common", "auth_common"]\n')
    assert repo.run("", "map", "--root", ".")[0] == 0
    assert json.loads(repo.read_file(".heimdall/map.json"))["shared"] == ["auth_common", "http_common"]


def test_map_takes_the_layout_from_pyproject_and_reads_routes_there(repo: TempRepo) -> None:
    import json

    repo.write_file("src/svc/slices/rune/feature.json", '{"depends_on": []}\n')
    repo.write_file("src/svc/core/__init__.py", "")
    repo.write_file(
        "pyproject.toml",
        '[tool.heimdall]\nfeatures = "src/svc/slices"\nkernel = "src/svc/core"\napp = "src/svc"\nroutes = "src/svc/http"\n',
    )
    repo.write_file(
        "src/svc/http/rune.py", 'from fastapi import APIRouter\nrouter = APIRouter(prefix="/rune")\n@router.get("/x")\ndef x(): ...\n'
    )
    repo.write_file(
        "src/svc/api/routes/rune.py",
        'from fastapi import APIRouter\nrouter = APIRouter(prefix="/wrong")\n@router.get("/y")\ndef y(): ...\n',
    )
    assert repo.run("", "map", "--root", ".")[0] == 0
    m = json.loads(repo.read_file(".heimdall/map.json"))
    assert (m["kernel"], m["kernel_dir"], m["slices_dir"], m["app_dir"], m["routes_dir"]) == (
        "core",
        "src/svc/core",
        "src/svc/slices",
        "src/svc",
        "src/svc/http",
    )
    assert "depends on: (none) + core<!--" in repo.read_file("src/svc/slices/rune/AGENTS.md")
    assert "routes: GET /rune/x<!--" in repo.read_file("src/svc/slices/rune/AGENTS.md")
    assert "routes: `src/svc/http/<feature>.py`" in repo.read_file("AGENTS.md")


def test_an_unreadable_manifest_is_named_and_stops_the_map(repo: TempRepo) -> None:
    _write_sample_tree(repo)
    repo.write_file("src/pfa/features/rune/feature.json", "{not json")

    code, _, stderr = repo.run("", "map", "--root", "src")

    assert code == 1 and stderr.startswith("heimdall map: unreadable src/pfa/features/rune/feature.json:")


@pytest.mark.parametrize(
    "args", [["--budget", "9000"], ["--root", "src", "--budget", "lots"], ["--root"], ["--root", "src", "--colour", "x"]]
)
def test_map_arguments_it_cannot_use_are_a_usage_error(repo: TempRepo, args: list[str]) -> None:
    _write_sample_tree(repo)

    code, _, stderr = repo.run("", "map", *args)

    assert code == 2 and stderr.startswith("usage: heimdall map")


def test_when_eitri_cannot_measure_the_tree_the_map_leaves_the_budget_out(repo: TempRepo) -> None:
    # Eitri refuses a manifest whose depends_on is not a list; the map reads it as no dependencies and goes on.
    _write_sample_tree(repo)
    repo.write_file("src/pfa/features/rune/feature.json", '{"depends_on": "kvad"}\n')

    code, _, stderr = repo.run("", "map", "--root", "src")

    assert code == 0 and "budget:" not in repo.read_file("src/pfa/features/kvad/AGENTS.md")
    assert stderr.startswith("heimdall map: budget lines left out: Eitri could not measure the features (")
    assert "| kvad | rune | 0 | ? |" in repo.read_file("AGENTS.md")
