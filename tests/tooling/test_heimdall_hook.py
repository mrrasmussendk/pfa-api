from __future__ import annotations

import re

from conftest import TempRepo


def test_hook_read_in_slice_logs_telemetry_line(repo: TempRepo) -> None:
    repo.write_sample_map()
    code, stderr = repo.hook(TempRepo.ev("s1", "Read", "src/pfa/features/kvad/internal/kvad_engine.py"))
    assert code == 0
    assert stderr == ""
    assert re.fullmatch(
        r'\{"event": "read", "path": "src/pfa/features/kvad/internal/kvad_engine\.py", '
        r'"kind": "slice:kvad", "sensor": "boundary_reads", "ts": \d+\.\d+, "session": "s1"\}\n',
        repo.telemetry,
    )


def test_hook_second_slice_edit_exit2_with_feedback_and_session_state_file(repo: TempRepo) -> None:
    repo.write_sample_map()
    first = repo.hook(TempRepo.ev("s1", "Edit", "src/pfa/features/kvad/internal/kvad_engine.py"))
    assert first[0] == 0
    assert repo.has_file(".heimdall/session-s1.json")
    code, stderr = repo.hook(TempRepo.ev("s1", "Edit", "src/pfa/features/rune/internal/rune_engine.py"))
    assert code == 2
    assert "Heimdall: you are now editing slice 'rune' after editing ['kvad']" in stderr
    assert "cross-slice" in stderr


def test_hook_invalid_json_exit0_silent(repo: TempRepo) -> None:
    repo.write_sample_map()
    code, _, stderr = repo.run("this is not json", "hook")
    assert code == 0
    assert stderr == ""
    assert repo.telemetry == ""


def test_hook_no_map_exit0_no_telemetry(repo: TempRepo) -> None:
    code, stderr = repo.hook(TempRepo.ev("s1", "Edit", "src/pfa/features/kvad/internal/kvad_engine.py"))
    assert code == 0
    assert stderr == ""
    assert not repo.has_file(".heimdall/telemetry.jsonl")


def test_hook_unreadable_map_exit1(repo: TempRepo) -> None:
    repo.write_file(".heimdall/map.json", "{broken")
    code, stderr = repo.hook(TempRepo.ev("s1", "Edit", "src/pfa/features/kvad/internal/kvad_engine.py"))
    assert code == 1
    assert "unreadable .heimdall/map.json" in stderr


def test_session_store_isolates_sessions_and_sanitizes_ids(repo: TempRepo) -> None:
    repo.write_sample_map()
    repo.hook(TempRepo.ev("a/b:c", "Edit", "src/pfa/features/kvad/internal/kvad_engine.py"))
    assert repo.has_file(".heimdall/session-a_b_c.json")
    assert '"edited_slices":["kvad"]' in repo.read_file(".heimdall/session-a_b_c.json").replace(" ", "")
    # a different session editing another slice gets no cross-slice warning
    code, _ = repo.hook(TempRepo.ev("other", "Edit", "src/pfa/features/rune/internal/rune_engine.py"))
    assert code == 0


def test_backslash_paths_classify_like_forward_slashes(repo: TempRepo) -> None:
    repo.write_sample_map()
    repo.hook(TempRepo.ev("s1", "Read", "src\\pfa\\features\\rune\\contract\\rune_service.py"))
    assert '"kind": "contract:rune"' in repo.telemetry


def test_usage_on_no_args_and_unknown_command(repo: TempRepo) -> None:
    code, _, stderr = repo.run("")
    assert code == 2 and "usage: heimdall" in stderr
    code, _, stderr = repo.run("", "bogus")
    assert code == 2 and "usage: heimdall" in stderr


def test_hook_read_of_a_shared_package_is_classified_shared(repo: TempRepo) -> None:
    repo.write_sample_map()
    import json

    m = json.loads(repo.read_file(".heimdall/map.json"))
    m["shared"] = ["http_common"]
    repo.write_file(".heimdall/map.json", json.dumps(m))
    code, stderr = repo.hook(TempRepo.ev("s1", "Read", "src/http_common/problems.py"))
    assert code == 0 and stderr == ""
    assert '"kind": "shared:http_common"' in repo.telemetry
    # without the declaration the same read is outside
    repo.write_sample_map()
    repo.hook(TempRepo.ev("s2", "Read", "src/http_common/problems.py"))
    assert '"kind": "outside", "sensor": "boundary_reads", "ts"' in repo.telemetry.splitlines()[-1]
