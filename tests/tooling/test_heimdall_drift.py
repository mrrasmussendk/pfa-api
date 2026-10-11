from __future__ import annotations

from conftest import TempRepo


def _smoke_scenario(repo: TempRepo) -> TempRepo:
    repo.write_sample_map()
    # session s1: edits kvad, reads own internal + declared contract; then creeps into rune
    repo.hook(TempRepo.ev("s1", "Read", "src/pfa/features/kvad/internal/kvad_engine.py"))
    repo.hook(TempRepo.ev("s1", "Edit", "src/pfa/features/kvad/internal/kvad_engine.py"))
    repo.hook(TempRepo.ev("s1", "Read", "src/pfa/features/rune/contract/rune_service.py"))
    repo.hook(TempRepo.ev("s1", "Read", "src/pfa/features/rune/internal/rune_engine.py"))  # OOB at read time...
    repo.hook(TempRepo.ev("s1", "Edit", "src/pfa/features/rune/internal/rune_engine.py"))  # ...but session later edits rune
    # session s2: edits kvad only, one clean read + one OOB read
    repo.hook(TempRepo.ev("s2", "Edit", "src/pfa/features/kvad/internal/kvad_service.py"))
    repo.hook(TempRepo.ev("s2", "Read", "src/pfa/features/kvad/internal/kvad_engine.py"))
    repo.hook(TempRepo.ev("s2", "Read", "src/pfa/features/rune/internal/rune_engine.py"))  # OOB
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
    repo.hook(TempRepo.ev("s1", "Edit", "src/pfa/features/kvad/internal/kvad_engine.py"))
    repo.hook(TempRepo.ev("s1", "Read", "src/pfa/features/kvad/internal/kvad_engine.py"))
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
    repo.hook(TempRepo.ev("s1", "Edit", "src/pfa/features/kvad/internal/kvad_engine.py"))
    repo.hook(TempRepo.ev("s1", "Read", "src/http_common/problems.py"))
    repo.hook(TempRepo.ev("s1", "Read", "src/pfa/features/kvad/internal/kvad_engine.py"))
    _, stdout, _ = repo.run("", "drift")
    assert "s1        kvad                            2     0      0%" in stdout


def test_drift_treats_a_slices_route_module_as_the_slice_and_the_app_package_as_in_bounds(repo: TempRepo) -> None:
    repo.write_sample_map()
    # an "add a route" session: edits kvad's HTTP edge, reads kvad's contract, the problem helper
    # and the composition root — all in-bounds; a read of rune's internals is not
    repo.hook(TempRepo.ev("s1", "Edit", "src/pfa/api/routes/kvad.py"))
    repo.hook(TempRepo.ev("s1", "Read", "src/pfa/features/kvad/contract/kvad_service.py"))
    repo.hook(TempRepo.ev("s1", "Read", "src/pfa/api/problems.py"))
    repo.hook(TempRepo.ev("s1", "Read", "src/pfa/api/composition.py"))
    repo.hook(TempRepo.ev("s1", "Read", "src/pfa/features/rune/internal/rune_engine.py"))
    _, stdout, _ = repo.run("", "drift")
    assert "s1        kvad                            4     1     25%" in stdout
    assert '"kind": "app"' in repo.telemetry and '"kind": "slice:kvad"' not in repo.telemetry
    assert '"event": "edit", "path": "src/pfa/api/routes/kvad.py", "slice": "kvad"' in repo.telemetry


def test_one_session_s_line_names_the_reads_outside_its_working_set(repo: TempRepo) -> None:
    _smoke_scenario(repo)

    code, stdout, _ = repo.run("", "drift", "--session", "s2")

    assert code == 0
    assert stdout == (
        "heimdall drift: 1 of 2 reads this session (50%) fell outside the working set: src/pfa/features/rune/internal/rune_engine.py"
        " — if this feature keeps needing them, its seam or a contract is missing something\n"
    )


def test_a_session_inside_its_working_set_says_so_without_a_hint(repo: TempRepo) -> None:
    _smoke_scenario(repo)

    _, stdout, _ = repo.run("", "drift", "--session", "s1")

    assert stdout == "heimdall drift: 0 of 3 reads this session (0%) fell outside the working set\n"


def test_a_session_with_no_telemetry_has_no_reads(repo: TempRepo) -> None:
    _smoke_scenario(repo)

    _, stdout, _ = repo.run("", "drift", "--session", "nobody")

    assert stdout == "heimdall drift: no reads recorded this session\n"


def test_drift_refuses_arguments_it_does_not_know(repo: TempRepo) -> None:
    _smoke_scenario(repo)

    code, _, stderr = repo.run("", "drift", "--everything")

    assert code == 2 and "usage: heimdall drift" in stderr
