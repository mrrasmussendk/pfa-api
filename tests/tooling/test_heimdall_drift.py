from __future__ import annotations

from conftest import TempRepo


def _smoke_scenario(repo: TempRepo) -> TempRepo:
    repo.write_sample_map()
    # session s1: edits kvad, reads own internal + declared contract; then creeps into rune
    repo.hook(TempRepo.ev("s1", "Read", "src/slices/kvad/internal/kvad_engine.py"))
    repo.hook(TempRepo.ev("s1", "Edit", "src/slices/kvad/internal/kvad_engine.py"))
    repo.hook(TempRepo.ev("s1", "Read", "src/slices/rune/contract/rune_service.py"))
    repo.hook(TempRepo.ev("s1", "Read", "src/slices/rune/internal/rune_engine.py"))  # OOB at read time...
    repo.hook(TempRepo.ev("s1", "Edit", "src/slices/rune/internal/rune_engine.py"))  # ...but session later edits rune
    # session s2: edits kvad only, one clean read + one OOB read
    repo.hook(TempRepo.ev("s2", "Edit", "src/slices/kvad/internal/kvad_service.py"))
    repo.hook(TempRepo.ev("s2", "Read", "src/slices/kvad/internal/kvad_engine.py"))
    repo.hook(TempRepo.ev("s2", "Read", "src/slices/rune/internal/rune_engine.py"))  # OOB
    return repo


def test_drift_prints_per_session_table_first_exact_columns(repo: TempRepo) -> None:
    _smoke_scenario(repo)
    code, stdout, _ = repo.run("", "drift")
    assert code == 0
    lines = stdout.split("\n")
    assert lines[0] == "session   slices edited               reads   oob   oob %"
    # s1 edited both slices; 3 reads; the rune internal read happened BEFORE the
    # s1 rune edit, but session sets are computed over the whole session -> in-bounds
    assert lines[1] == "s1        kvad,rune                       3     0      0%"
    # s2: 2 reads, 1 OOB -> 50%
    assert lines[2] == "s2        kvad                            2     1     50%"


def test_drift_per_slice_aggregate_second_attributes_to_first_edited_slice(repo: TempRepo) -> None:
    _smoke_scenario(repo)
    _, stdout, _ = repo.run("", "drift")
    lines = stdout.split("\n")
    assert lines[3] == ""
    assert lines[4] == "slice                   reads  out-of-bounds   oob %"
    # both sessions' target is "kvad" (first sorted edited slice): 3+2 reads, 0+1 oob -> 20%
    assert lines[5] == "kvad                        5              1     20%"
    assert "rule of thumb: sustained oob% > 20 on a slice = re-cut that seam" in stdout


def test_drift_missing_inputs_exit1(repo: TempRepo) -> None:
    code, _, stderr = repo.run("", "drift")
    assert code == 1
    assert "need telemetry + map" in stderr


def test_drift_skips_malformed_telemetry_lines(repo: TempRepo) -> None:
    repo.write_sample_map()
    repo.hook(TempRepo.ev("s1", "Edit", "src/slices/kvad/internal/kvad_engine.py"))
    repo.hook(TempRepo.ev("s1", "Read", "src/slices/kvad/internal/kvad_engine.py"))
    with open(repo.root / ".heimdall" / "telemetry.jsonl", "a", encoding="utf-8") as f:
        f.write("{garbage\n")
    code, stdout, _ = repo.run("", "drift")
    assert code == 0
    assert "s1        kvad                            1     0      0%" in stdout


def test_drift_counts_shared_package_reads_as_in_bounds(repo: TempRepo) -> None:
    import json

    repo.write_sample_map()
    m = json.loads(repo.read_file(".heimdall/map.json"))
    m["shared"] = ["http_common"]
    repo.write_file(".heimdall/map.json", json.dumps(m))
    repo.hook(TempRepo.ev("s1", "Edit", "src/slices/kvad/internal/routes.py"))
    repo.hook(TempRepo.ev("s1", "Read", "src/http_common/problems.py"))
    repo.hook(TempRepo.ev("s1", "Read", "src/slices/kvad/internal/routes.py"))
    _, stdout, _ = repo.run("", "drift")
    assert "s1        kvad                            2     0      0%" in stdout
