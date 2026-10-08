from __future__ import annotations

import json
import re

import pytest
from conftest import SHAPED_SOURCE, WIDE_FUNCTION, TempRepo


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


# ---------------------------------------------------------------- function shape: the limits reach the agent while it edits


def test_hook_names_the_long_function_the_edit_touched_and_exits_2(repo: TempRepo) -> None:
    repo.write_sample_map()
    path = "src/pfa/features/kvad/internal/kvad_engine.py"
    repo.write_file(path, SHAPED_SOURCE)
    code, stderr = repo.hook(TempRepo.ev("s1", "Edit", path, old_string="x3 = 3", new_string="    x3 = 3"))
    assert code == 2
    assert (
        f"Heimdall: the function you just edited is over the shape limit (40 lines, 5 parameters): {path}:1 long_one (46 lines, 0 params)"
        in stderr
    )
    assert "Split it into functions whose names say what they do" in stderr
    assert '"kind": "function_shape"' in repo.telemetry and '"sensor": "function_shape"' in repo.telemetry
    # the same file, an edit inside the short function: the long one is history, not what the agent wrote
    code, stderr = repo.hook(TempRepo.ev("s1", "Edit", path, old_string="a + b", new_string="    return a + b"))
    assert code == 0 and stderr == ""


def test_hook_write_judges_the_whole_file_and_counts_parameters(repo: TempRepo) -> None:
    repo.write_sample_map()
    path = "src/pfa/kernel/wide.py"
    repo.write_file(path, WIDE_FUNCTION)
    code, stderr = repo.hook(TempRepo.ev("s1", "Write", path, content=WIDE_FUNCTION))
    assert code == 2 and f"{path}:1 wide (2 lines, 6 params)" in stderr
    # MultiEdit: every edit's text locates a function; a method's self is not a parameter
    repo.write_file(path, "class K:\n    def m(self, a, b, c, d, e):\n        return a\n" + WIDE_FUNCTION)
    code, stderr = repo.hook(TempRepo.ev("s1", "MultiEdit", path, edits=[{"old_string": "x", "new_string": "        return a\n"}]))
    assert code == 2 and "K.m" not in stderr and "wide (2 lines, 6 params)" in stderr


def test_hook_shape_skips_overrides_and_files_outside_the_repo(repo: TempRepo, tmp_path_factory: pytest.TempPathFactory) -> None:
    repo.write_sample_map()
    path = "src/pfa/kernel/handler.py"
    override = "class H(Base):\n    def redirect_request(self, req, fp, code, msg, headers, newurl):\n        return None\n"
    repo.write_file(path, override)
    assert repo.hook(TempRepo.ev("s1", "Write", path, content=override)) == (0, "")  # the base class chose that signature
    plain = override.replace("class H(Base):", "class H:")
    repo.write_file(path, plain)
    code, stderr = repo.hook(TempRepo.ev("s1", "Write", path, content=plain))
    assert code == 2 and "H.redirect_request (2 lines, 6 params)" in stderr  # no base: the author chose it
    outside = tmp_path_factory.mktemp("scratch") / "scratch.py"
    outside.write_text(WIDE_FUNCTION, encoding="utf-8")
    assert repo.hook(TempRepo.ev("s1", "Write", str(outside), content=WIDE_FUNCTION)) == (0, "")  # not the project's code


def test_hook_shape_leaves_tests_other_languages_and_missing_files_alone(repo: TempRepo) -> None:
    repo.write_sample_map()
    repo.write_file("tests/api/test_long.py", SHAPED_SOURCE)
    assert repo.hook(TempRepo.ev("s1", "Write", "tests/api/test_long.py", content=SHAPED_SOURCE)) == (0, "")
    repo.write_file("src/pfa/kernel/notes.md", SHAPED_SOURCE)
    assert repo.hook(TempRepo.ev("s1", "Write", "src/pfa/kernel/notes.md", content=SHAPED_SOURCE)) == (0, "")
    assert repo.hook(TempRepo.ev("s1", "Edit", "src/pfa/kernel/gone.py", new_string="x")) == (0, "")
    repo.write_file("src/pfa/kernel/broken.py", "def (:\n")
    assert repo.hook(TempRepo.ev("s1", "Write", "src/pfa/kernel/broken.py", content="def (:\n")) == (0, "")
