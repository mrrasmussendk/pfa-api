"""The tooling against the synthetic fixture tree: check, surface, map, canaries, entry points."""

from __future__ import annotations

import io
import os
import subprocess
import sys
from pathlib import Path

import pytest

from eitri import EitriConfig, analyze
from eitri.cli import run as eitri_cli
from fixtures import write_fixture_tree
from heimdall.app import run as heimdall_run

REPO = Path(__file__).resolve().parents[2]
TOOLS = REPO / "tools"


@pytest.fixture
def fixture_root(tmp_path: Path) -> Path:
    return write_fixture_tree(tmp_path / "proj")


def test_fixture_passes_the_walls_and_the_budget(fixture_root: Path) -> None:
    diags = analyze(fixture_root)
    assert [d.id for d in diags] == ["EIT101", "EIT101"]
    kvad = next(d for d in diags if "'kvad'" in d.message)
    assert "contracts 0" not in kvad.message  # kvad pays for rune's contract
    rune = next(d for d in diags if "'rune'" in d.message)
    assert "contracts 0" in rune.message  # a leaf slice pays for no contracts


def test_fixture_pyproject_config_is_read(fixture_root: Path) -> None:
    cfg = EitriConfig.read(fixture_root)
    assert cfg.token_budget == 15_000 and cfg.kernel == "pfa.kernel" and cfg.slices_package == "pfa.features"
    assert cfg.app_package == "pfa" and cfg.composition_root == "pfa.application"


def test_cli_check_prints_compiler_style_lines_and_exit_code(fixture_root: Path) -> None:
    out, err = io.StringIO(), io.StringIO()
    assert eitri_cli(["check", "--root", str(fixture_root)], out, err) == 0
    assert ": info EIT101: Slice 'kvad'" in out.getvalue()
    assert out.getvalue().rstrip().endswith("eitri: 0 error(s), 2 info")
    out, err = io.StringIO(), io.StringIO()
    assert eitri_cli(["check", "--root", str(fixture_root), "--budget", "100"], out, err) == 1
    assert "error EIT100" in out.getvalue()
    out, err = io.StringIO(), io.StringIO()
    assert eitri_cli(["check", "--root", str(fixture_root), "--budget", "100", "--no-info"], out, err) == 1
    assert "EIT101" not in out.getvalue()


def test_cli_surface_prints_a_contract_stub(fixture_root: Path) -> None:
    out, err = io.StringIO(), io.StringIO()
    assert eitri_cli(["surface", "--root", str(fixture_root), "rune"], out, err) == 0
    text = out.getvalue()
    assert "class RuneService(Protocol):" in text
    assert "def read(self, stave_id: StaveId, runes: Sequence[RuneRef], utterance: str) -> Result[RuneReading]:" in text
    assert "RuneEngine" not in text
    out, err = io.StringIO(), io.StringIO()
    assert eitri_cli(["surface", "--root", str(fixture_root), "pfa.kernel"], out, err) == 0
    assert "class Registry:" in out.getvalue()
    assert eitri_cli(["surface", "--root", str(fixture_root), "nope"], io.StringIO(), io.StringIO()) == 1


def test_heimdall_map_regenerates_the_committed_agents_md(fixture_root: Path) -> None:
    before = {n: (fixture_root / "pfa" / "features" / n / "AGENTS.md").read_text(encoding="utf-8") for n in ("kvad", "rune")}
    out, err = io.StringIO(), io.StringIO()
    assert heimdall_run(["map", "--root", "."], io.StringIO(), out, err, str(fixture_root)) == 0
    assert "2 features" in out.getvalue()
    for name, text in before.items():
        assert (fixture_root / "pfa" / "features" / name / "AGENTS.md").read_text(encoding="utf-8") == text


def test_brokkr_canaries_all_bite() -> None:
    r = subprocess.run([sys.executable, str(TOOLS / "brokkr_canary.py")], capture_output=True, text=True, encoding="utf-8", cwd=str(REPO))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "canary: 10 passed, 0 failed" in r.stdout


def test_module_entry_points_run_as_subprocesses(fixture_root: Path) -> None:
    env = {**os.environ, "PYTHONPATH": str(TOOLS)}
    r = subprocess.run(
        [sys.executable, "-m", "eitri", "check", "--root", str(fixture_root)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(REPO),
        env=env,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "eitri: 0 error(s), 2 info" in r.stdout
    r = subprocess.run([sys.executable, "-m", "heimdall"], capture_output=True, text=True, encoding="utf-8", cwd=str(REPO), env=env)
    assert r.returncode == 2 and "usage: heimdall" in r.stderr
