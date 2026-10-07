"""The 16 behavioral checks the harness was validated with: feedback fires precisely on
scope creep and frozen contracts, stays silent on clean work, and the drift report shows
wandering sessions undiluted."""

from __future__ import annotations

from conftest import TempRepo

ev = TempRepo.ev


# 1
def test_clean_session_edits_and_reads_own_slice_all_hooks_silent(repo: TempRepo) -> None:
    repo.write_sample_map()
    assert repo.hook(ev("s1", "Edit", "src/slices/kvad/internal/kvad_engine.py")) == (0, "")
    assert repo.hook(ev("s1", "Read", "src/slices/kvad/internal/kvad_service.py")) == (0, "")
    assert repo.hook(ev("s1", "Read", "src/shared_kernel/primitives.py")) == (0, "")
    assert repo.hook(ev("s1", "Read", "src/slices/rune/contract/rune_service.py")) == (0, "")


# 2
def test_clean_session_telemetry_still_logged(repo: TempRepo) -> None:
    repo.write_sample_map()
    repo.hook(ev("s1", "Edit", "src/slices/kvad/internal/kvad_engine.py"))
    repo.hook(ev("s1", "Read", "src/slices/kvad/internal/kvad_service.py"))
    lines = repo.telemetry.rstrip("\n").split("\n")
    assert len(lines) == 2
    assert '"event": "edit"' in lines[0]
    assert '"event": "read"' in lines[1]


# 3
def test_wanderer_foreign_internal_read_logged_not_warned(repo: TempRepo) -> None:
    repo.write_sample_map()
    code, stderr = repo.hook(ev("s1", "Read", "src/slices/rune/internal/rune_engine.py"))
    assert code == 0  # reads NEVER interrupt — pure observation
    assert stderr == ""
    assert '"kind": "slice:rune"' in repo.telemetry


# 4
def test_scope_creep_second_slice_edit_warns_with_both_slice_names(repo: TempRepo) -> None:
    repo.write_sample_map()
    repo.hook(ev("s1", "Edit", "src/slices/kvad/internal/kvad_engine.py"))
    code, stderr = repo.hook(ev("s1", "Edit", "src/slices/rune/internal/rune_engine.py"))
    assert code == 2
    assert "you are now editing slice 'rune' after editing ['kvad']" in stderr
    assert "cross-slice changes should go through contracts" in stderr


# 5
def test_scope_creep_warns_exactly_once_third_edit_same_slice_silent(repo: TempRepo) -> None:
    repo.write_sample_map()
    repo.hook(ev("s1", "Edit", "src/slices/kvad/internal/kvad_engine.py"))
    assert repo.hook(ev("s1", "Edit", "src/slices/rune/internal/rune_engine.py"))[0] == 2
    # the slice is now part of the session's working set — repeating the edit is not a new crossing
    assert repo.hook(ev("s1", "Edit", "src/slices/rune/internal/rune_service.py")) == (0, "")


# 6
def test_scope_creep_separate_sessions_no_warning(repo: TempRepo) -> None:
    repo.write_sample_map()
    repo.hook(ev("s1", "Edit", "src/slices/kvad/internal/kvad_engine.py"))
    assert repo.hook(ev("s2", "Edit", "src/slices/rune/internal/rune_engine.py")) == (0, "")


# 7
def test_frozen_contract_high_fan_in_warns(repo: TempRepo) -> None:
    repo.write_sample_map(with_frozen_core=True)  # core has fan_in 12 >= 10
    code, stderr = repo.hook(ev("s1", "Edit", "src/slices/core/contract/core_service.py"))
    assert code == 2
    assert "'core' Contract has fan-in 12 — treat as frozen" in stderr
    assert "expand-contract" in stderr


# 8
def test_low_fan_in_contract_edit_silent(repo: TempRepo) -> None:
    repo.write_sample_map()  # rune fan_in 1 < 10
    assert repo.hook(ev("s1", "Edit", "src/slices/rune/contract/rune_service.py")) == (0, "")


# 9
def test_read_kernel_file_classified_kernel(repo: TempRepo) -> None:
    repo.write_sample_map()
    repo.hook(ev("s1", "Read", "src/shared_kernel/primitives.py"))
    assert '"kind": "kernel"' in repo.telemetry


# 10
def test_read_contract_file_classified_contract_with_slice(repo: TempRepo) -> None:
    repo.write_sample_map()
    repo.hook(ev("s1", "Read", "src/slices/rune/contract/rune_service.py"))
    assert '"kind": "contract:rune"' in repo.telemetry


# 11
def test_read_outside_slices_dir_classified_outside(repo: TempRepo) -> None:
    repo.write_sample_map()
    repo.hook(ev("s1", "Read", "docs/rules/EIT001.md"))
    assert '"kind": "outside"' in repo.telemetry


# 12
def test_read_non_code_file_produces_no_finding(repo: TempRepo) -> None:
    repo.write_sample_map()
    repo.hook(ev("s1", "Read", "src/slices/kvad/appsettings.json"))
    assert repo.telemetry == ""


# 13
def test_hook_irrelevant_tool_no_finding_no_telemetry(repo: TempRepo) -> None:
    repo.write_sample_map()
    assert repo.hook('{"session_id":"s1","tool_name":"Bash","tool_input":{"command":"ls"}}') == (0, "")
    assert repo.telemetry == ""


# 14
def test_hook_garbage_stdin_exit0(repo: TempRepo) -> None:
    repo.write_sample_map()
    code, _, stderr = repo.run("{not json", "hook")
    assert code == 0
    assert stderr == ""


# 15
def test_hook_no_map_stays_silent_even_on_cross_slice_edits(repo: TempRepo) -> None:
    repo.hook(ev("s1", "Edit", "src/slices/kvad/internal/kvad_engine.py"))
    code, stderr = repo.hook(ev("s1", "Edit", "src/slices/rune/internal/rune_engine.py"))
    assert code == 0
    assert stderr == ""
    assert repo.telemetry == ""


# 16
def test_drift_per_session_wandering_session_shows_100_percent_undiluted(repo: TempRepo) -> None:
    repo.write_sample_map()
    # clean session: 3 reads, all in bounds
    repo.hook(ev("clean", "Edit", "src/slices/kvad/internal/kvad_engine.py"))
    repo.hook(ev("clean", "Read", "src/slices/kvad/internal/kvad_service.py"))
    repo.hook(ev("clean", "Read", "src/shared_kernel/primitives.py"))
    repo.hook(ev("clean", "Read", "src/slices/rune/contract/rune_service.py"))
    # wandering session: edits kvad, then reads ONLY foreign internals
    repo.hook(ev("wander", "Edit", "src/slices/kvad/internal/kvad_engine.py"))
    repo.hook(ev("wander", "Read", "src/slices/rune/internal/rune_engine.py"))
    repo.hook(ev("wander", "Read", "src/slices/rune/internal/rune_service.py"))

    _, stdout, _ = repo.run("", "drift")
    lines = stdout.split("\n")
    # per-session table: the wanderer shows 100%, NOT averaged with the clean session
    assert lines[1] == "clean     kvad                            3     0      0%"
    assert lines[2] == "wander    kvad                            2     2    100%"
    # the aggregate DOES dilute (5 reads, 2 oob -> 40%) — which is exactly why
    # the per-session table exists and prints first
    assert "kvad                        5              2     40%" in stdout
