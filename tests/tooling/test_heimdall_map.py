from __future__ import annotations

import json

from conftest import TempRepo


def _write_sample_tree(repo: TempRepo) -> None:
    repo.write_file("src/slices/kvad/slice.json", '{"depends_on": ["rune", "shared_kernel"]}\n')
    repo.write_file("src/slices/rune/slice.json", '{"depends_on": []}\n')
    repo.write_file("src/slices/kvad/AGENTS.md", "# kvad\nhand-written notes stay.\n")


def test_map_emits_indent2_map_json(repo: TempRepo) -> None:
    _write_sample_tree(repo)
    code, stdout, _ = repo.run("", "map", "--root", "src", "--budget", "15000")
    assert code == 0
    assert "heimdall map: 2 slices -> .heimdall/map.json; AGENTS.md deps regenerated" in stdout
    expected = {
        "kernel": "shared_kernel",
        "slices_dir": "src/slices",
        "slices": {
            "kvad": {"path": "src/slices/kvad", "depends_on": ["rune"], "budget": 15000, "fan_in": 0},
            "rune": {"path": "src/slices/rune", "depends_on": [], "budget": 15000, "fan_in": 1},
        },
    }
    assert repo.read_file(".heimdall/map.json") == json.dumps(expected, indent=2)


def test_map_injects_and_refreshes_agents_md_markers_idempotently(repo: TempRepo) -> None:
    _write_sample_tree(repo)
    repo.run("", "map", "--root", "src")
    assert repo.read_file("src/slices/kvad/AGENTS.md") == (
        "# kvad\nhand-written notes stay.\n<!--heimdall:deps-->depends on: rune + shared_kernel<!--/heimdall:deps-->\n"
        "<!--heimdall:routes-->routes: (none)<!--/heimdall:routes-->\n"
    )
    # rune had no AGENTS.md -> created from scratch
    assert repo.read_file("src/slices/rune/AGENTS.md") == (
        "# rune\n<!--heimdall:deps-->depends on: (none) + shared_kernel<!--/heimdall:deps-->\n"
        "<!--heimdall:routes-->routes: (none)<!--/heimdall:routes-->\n"
    )
    # rerun: markers replaced in place, no duplication
    repo.run("", "map", "--root", "src")
    assert repo.read_file("src/slices/kvad/AGENTS.md") == (
        "# kvad\nhand-written notes stay.\n<!--heimdall:deps-->depends on: rune + shared_kernel<!--/heimdall:deps-->\n"
        "<!--heimdall:routes-->routes: (none)<!--/heimdall:routes-->\n"
    )


def test_map_creates_and_refreshes_the_root_agents_md_block(repo: TempRepo) -> None:
    _write_sample_tree(repo)
    # no root AGENTS.md -> created with the generated block
    repo.run("", "map", "--root", "src")
    txt = repo.read_file("AGENTS.md")
    assert txt.startswith("# Agents\n\n## Slice map\n\n<!--heimdall:map-->\n")
    assert "| kvad | rune | 0 | `src/slices/kvad/contract/` | (none) |" in txt
    assert "| rune | (none) | 1 | `src/slices/rune/contract/` | (none) |" in txt
    assert txt.count("<!--heimdall:map-->") == 1 and txt.endswith("<!--/heimdall:map-->\n")
    # hand-written prose around the markers survives, the block is replaced in place
    repo.write_file("AGENTS.md", "# Mine\nintro stays.\n\n<!--heimdall:map-->\nstale\n<!--/heimdall:map-->\n\noutro stays.\n")
    repo.write_file("src/slices/galdr/slice.json", '{"depends_on": ["rune"]}\n')
    repo.run("", "map", "--root", "src")
    txt = repo.read_file("AGENTS.md")
    assert txt.startswith("# Mine\nintro stays.\n\n<!--heimdall:map-->\n") and txt.endswith("<!--/heimdall:map-->\n\noutro stays.\n")
    assert "stale" not in txt and "| galdr | rune | 0 |" in txt and "| rune | (none) | 2 |" in txt
    # an AGENTS.md without markers gets the block appended under its own heading
    repo.write_file("AGENTS.md", "# Mine\nno markers yet.\n")
    repo.run("", "map", "--root", "src")
    assert repo.read_file("AGENTS.md").startswith("# Mine\nno markers yet.\n\n## Slice map\n\n<!--heimdall:map-->\n")


def test_map_honors_budget_and_kernel_flags(repo: TempRepo) -> None:
    _write_sample_tree(repo)
    repo.run("", "map", "--root", "src", "--budget", "9000", "--kernel", "rune")
    m = json.loads(repo.read_file(".heimdall/map.json"))
    assert m["kernel"] == "rune"
    assert m["slices"]["kvad"]["budget"] == 9000
    # rune is now the kernel -> dropped from kvad's deps; the explicit shared_kernel entry stays
    assert m["slices"]["kvad"]["depends_on"] == ["shared_kernel"]
    assert m["slices"]["rune"]["fan_in"] == 0


def test_map_ignores_directories_without_manifest(repo: TempRepo) -> None:
    _write_sample_tree(repo)
    repo.write_file("src/slices/__init__.py", "")
    repo.write_file("src/slices/scratch/notes.md", "no manifest here")
    _, stdout, _ = repo.run("", "map", "--root", "src")
    assert "2 slices" in stdout


def test_map_no_slices_dir_exit1(repo: TempRepo) -> None:
    code, _, stderr = repo.run("", "map", "--root", "nowhere")
    assert code == 1
    assert "no slices/ under nowhere" in stderr


def test_map_usage_errors_exit2(repo: TempRepo) -> None:
    assert repo.run("", "map")[0] == 2
    assert repo.run("", "map", "--root", "src", "--budget", "lots")[0] == 2
    assert repo.run("", "map", "--bogus")[0] == 2


def test_map_inventories_routes_from_routes_py_without_importing_it(repo: TempRepo) -> None:
    _write_sample_tree(repo)
    repo.write_file(
        "src/slices/kvad/internal/routes.py",
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
        in repo.read_file("src/slices/kvad/AGENTS.md")
    )
    assert "<!--heimdall:routes-->routes: (none)<!--/heimdall:routes-->" in repo.read_file("src/slices/rune/AGENTS.md")
    assert (
        "| kvad | rune | 0 | `src/slices/kvad/contract/` | `POST /kvad/compose`<br>`GET /kvad/{stave_id}`<br>`DELETE /other/x` |"
        in repo.read_file("AGENTS.md")
    )
    # a slice AGENTS.md that predates the routes marker gets the line inserted right after deps, once
    repo.write_file("src/slices/rune/AGENTS.md", "# rune\n<!--heimdall:deps-->stale<!--/heimdall:deps-->\nnotes stay.\n")
    repo.run("", "map", "--root", "src")
    assert repo.read_file("src/slices/rune/AGENTS.md") == (
        "# rune\n<!--heimdall:deps-->depends on: (none) + shared_kernel<!--/heimdall:deps-->\n"
        "<!--heimdall:routes-->routes: (none)<!--/heimdall:routes-->\nnotes stay.\n"
    )


def test_map_records_shared_packages_from_pyproject(repo: TempRepo) -> None:
    import json

    repo.write_file("src/slices/rune/slice.json", '{"depends_on": []}\n')
    repo.write_file("pyproject.toml", '[tool.other]\nx = 1\n\n[tool.heimdall]\nshared = ["http_common", "auth_common"]\n')
    assert repo.run("", "map", "--root", ".")[0] == 0
    assert json.loads(repo.read_file(".heimdall/map.json"))["shared"] == ["auth_common", "http_common"]
