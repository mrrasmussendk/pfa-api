"""``heimdall e2e``'s setup: the project's settings (``harness/e2e/system.json``), the scenario files and their
tests, the tools that run them, and ``heimdall e2e new``, which scaffolds a scenario and its failing tests."""

from __future__ import annotations

import json
import re

import pytest
from conftest import TempRepo, e2e_scenario, e2e_scenario_json, write_e2e_project, write_e2e_scenario

from heimdall.commands import e2e
from heimdall.commands.e2e import (
    NOT_WRITTEN,
    TOOLS,
    E2EError,
    NewScenario,
    Scenario,
    Steps,
    System,
    by_tool,
    load_scenarios,
    load_system,
    scaffold,
    system_runner,
)

API_ONLY = System(tools={"API": "postman"})
API_AND_UI = System(tools={"API": "postman", "UI": "playwright"})
STEPS = Steps("a text", "I split it", "I receive the chunks")


# ---------------------------------------------------------------- the project's settings


def test_the_system_settings_load_the_start_command_and_a_tool_per_method(repo: TempRepo) -> None:
    repo.write_file(
        e2e.SYSTEM, json.dumps({"start": ["{python}", "-m", "app"], "port_env": "PORT", "ready": "/up", "tools": {"UI": "playwright"}})
    )

    system = load_system(str(repo.root))

    assert system == System(tools={"UI": "playwright"}, start=("{python}", "-m", "app"), port_env="PORT", ready="/up")


def test_a_project_without_system_settings_cannot_run(repo: TempRepo) -> None:
    with pytest.raises(E2EError, match=re.escape("harness/e2e/system.json is missing")):
        load_system(str(repo.root))


def test_system_settings_without_a_tool_per_method_cannot_run(repo: TempRepo) -> None:
    repo.write_file(e2e.SYSTEM, json.dumps({"tools": ["postman"]}))

    with pytest.raises(E2EError, match="`tools` must map each method to a tool"):
        load_system(str(repo.root))


def test_system_settings_naming_a_tool_heimdall_has_no_runner_for_cannot_run(repo: TempRepo) -> None:
    repo.write_file(e2e.SYSTEM, json.dumps({"tools": {"API": "cypress"}}))

    with pytest.raises(E2EError, match=re.escape("no runner for ['cypress']")):
        load_system(str(repo.root))


def test_a_start_command_that_cannot_learn_its_port_cannot_run(repo: TempRepo) -> None:
    repo.write_file(e2e.SYSTEM, json.dumps({"start": ["node", "server.js"], "tools": {"API": "postman"}}))

    with pytest.raises(E2EError, match="cannot learn the port"):
        load_system(str(repo.root))


def test_a_start_command_may_take_the_port_as_an_argument(repo: TempRepo) -> None:
    repo.write_file(e2e.SYSTEM, json.dumps({"start": ["node", "server.js", "--port", "{port}"], "tools": {"API": "postman"}}))

    system = load_system(str(repo.root))

    assert system.start == ("node", "server.js", "--port", "{port}")


# ---------------------------------------------------------------- scenarios


def test_a_scenario_in_the_scenario_shape_loads_with_its_feature_from_the_folder(repo: TempRepo) -> None:
    write_e2e_project(repo)

    scenarios, problems = load_scenarios(str(repo.root), API_ONLY)

    assert problems == []
    assert [(s.id, s.feature, s.method, s.tools) for s in scenarios] == [
        ("kvad/squares", "kvad", "API", ("postman",)),
        ("rune/carves", "rune", "API", ("postman",)),
    ]


def test_a_scenario_missing_a_step_is_a_problem_naming_the_file_and_the_step(repo: TempRepo) -> None:
    write_e2e_project(repo)
    repo.write_file("tests/e2e/kvad/broken.json", e2e_scenario_json(scenario={"given": "g", "when": "w"}))

    _, problems = load_scenarios(str(repo.root), API_ONLY)

    assert problems == ["tests/e2e/kvad/broken.json: scenario.then is missing or empty"]


def test_a_scenario_naming_a_tool_the_project_does_not_use_is_a_problem(repo: TempRepo) -> None:
    write_e2e_project(repo)
    repo.write_file("tests/e2e/kvad/clicks.json", e2e_scenario_json(tools=["cypress"]))

    _, problems = load_scenarios(str(repo.root), API_ONLY)

    assert problems == [
        "tests/e2e/kvad/clicks.json: tools ['cypress'] — this project runs scenarios with ['postman'] (harness/e2e/system.json)"
    ]


def test_a_scenario_whose_tool_cannot_test_its_method_is_a_problem(repo: TempRepo) -> None:
    write_e2e_project(repo)
    repo.write_file("tests/e2e/kvad/screen.json", e2e_scenario_json(method="UI"))

    _, problems = load_scenarios(str(repo.root), API_AND_UI)

    assert problems == ["tests/e2e/kvad/screen.json: postman cannot test method 'UI'; it tests ['API']"]


def test_a_scenario_without_its_test_is_a_problem_naming_where_it_goes(repo: TempRepo) -> None:
    write_e2e_project(repo)
    write_e2e_scenario(repo, "kvad/rounds", request=None)

    _, problems = load_scenarios(str(repo.root), API_ONLY)

    assert problems == ["tests/e2e/kvad/rounds.json: no postman test runs it — write postman/collections/e2e/kvad/rounds.request.yaml"]


def test_a_test_that_asserts_nothing_is_a_problem(repo: TempRepo) -> None:
    write_e2e_project(repo)
    write_e2e_scenario(repo, "kvad/rounds", request="$kind: http-request\n")

    _, problems = load_scenarios(str(repo.root), API_ONLY)

    assert problems == ["tests/e2e/kvad/rounds.json: postman/collections/e2e/kvad/rounds.request.yaml has no pm.test( asserting the `then`"]


def test_a_scenario_that_is_not_json_is_a_problem(repo: TempRepo) -> None:
    write_e2e_project(repo)
    repo.write_file("tests/e2e/kvad/torn.json", "{")

    _, problems = load_scenarios(str(repo.root), API_ONLY)

    assert len(problems) == 1 and problems[0].startswith("tests/e2e/kvad/torn.json: not JSON")


def test_a_scenario_knows_its_files_across_its_tools() -> None:
    s = e2e_scenario("kvad/squares", tools=("postman", "pytest"))

    assert s.files == {"tests/e2e/kvad/squares.json", "postman/collections/e2e/kvad/squares.request.yaml", "tests/e2e/kvad/squares_e2e.py"}


# ---------------------------------------------------------------- the tools


@pytest.mark.parametrize("name", sorted(TOOLS))
def test_every_tool_stub_asserts_something_and_fails_until_written(name: str) -> None:
    tool = TOOLS[name]

    stub = tool.stub(e2e_scenario("kvad/squares", tools=(name,)))

    assert tool.marker in stub and NOT_WRITTEN in stub and "then: t" in stub


def test_a_pytest_stub_stays_valid_python_whatever_the_steps_say() -> None:
    s = Scenario("kvad/q", "kvad", "service", ("pytest",), 'a """quoted""" text', "I call it", "it answers")

    stub = TOOLS["pytest"].stub(s)

    compile(stub, "q_e2e.py", "exec")


def test_a_pytest_scenario_test_stays_out_of_the_unit_run() -> None:
    path = TOOLS["pytest"].test_path("kvad/squares")

    assert path.endswith("_e2e.py") and not path.rsplit("/", 1)[1].startswith("test_")


def test_the_pytest_tool_hands_the_base_url_to_the_test_through_the_environment() -> None:
    argv, env = TOOLS["pytest"].command([e2e_scenario("kvad/squares", tools=("pytest",))], "http://127.0.0.1:9")

    assert argv[-1] == "tests/e2e/kvad/squares_e2e.py" and env == {"E2E_BASE_URL": "http://127.0.0.1:9"}


def test_the_postman_tool_runs_by_feature_path_without_reporting_to_the_cloud(monkeypatch: pytest.MonkeyPatch) -> None:
    # Logged in, the CLI reports runs by default through a v2 path that cannot find `<feature>/<name>` items.
    monkeypatch.setattr("shutil.which", lambda exe: exe)

    argv, _ = TOOLS["postman"].command([e2e_scenario("kvad/squares")], "http://127.0.0.1:9")

    assert argv[4:6] == ["-i", "kvad/squares"] and "--no-report-events" in argv


def test_scenarios_are_batched_by_each_tool_they_name() -> None:
    a, b = e2e_scenario("kvad/a", tools=("postman", "pytest")), e2e_scenario("kvad/b", tools=("pytest",))

    groups = by_tool([a, b])

    assert {t: [s.id for s in g] for t, g in groups.items()} == {"postman": ["kvad/a"], "pytest": ["kvad/a", "kvad/b"]}


def test_a_scaffolded_pytest_scenario_fails_when_run_until_its_then_is_asserted(repo: TempRepo) -> None:
    pytest_only = System(tools={"service": "pytest"})
    scaffold(str(repo.root), NewScenario("jobs/retry", "service", STEPS), pytest_only)
    s = Scenario("jobs/retry", "jobs", "service", ("pytest",), *STEPS)

    result = system_runner(str(repo.root), pytest_only)([s])

    assert not result.ok and NOT_WRITTEN in result.output


# ---------------------------------------------------------------- heimdall e2e new


def test_new_writes_the_scenario_and_a_failing_test_with_the_methods_tool(repo: TempRepo) -> None:
    write_e2e_project(repo)

    written = scaffold(str(repo.root), NewScenario("kvad/rounds", "API", STEPS), API_ONLY)

    assert written == ["tests/e2e/kvad/rounds.json", "postman/collections/e2e/kvad/rounds.request.yaml"]
    assert json.loads(repo.read_file("tests/e2e/kvad/rounds.json"))["scenario"]["then"] == "I receive the chunks"


def test_a_scaffolded_scenario_loads_without_problems(repo: TempRepo) -> None:
    write_e2e_project(repo)
    scaffold(str(repo.root), NewScenario("kvad/rounds", "API", STEPS), API_ONLY)

    _, problems = load_scenarios(str(repo.root), API_ONLY)

    assert problems == []


def test_new_takes_another_tool_the_project_uses(repo: TempRepo) -> None:
    write_e2e_project(repo)

    written = scaffold(str(repo.root), NewScenario("kvad/page", "API", STEPS, tool="playwright"), API_AND_UI)

    assert written[1] == "tests/e2e/kvad/page.spec.ts"


def test_new_refuses_a_method_the_project_has_no_tool_for(repo: TempRepo) -> None:
    write_e2e_project(repo)

    with pytest.raises(E2EError, match="method 'UI' has no tool"):
        scaffold(str(repo.root), NewScenario("kvad/page", "UI", STEPS), API_ONLY)


def test_new_never_overwrites_a_scenario(repo: TempRepo) -> None:
    write_e2e_project(repo)

    with pytest.raises(E2EError, match=re.escape("already exists: tests/e2e/kvad/squares.json")):
        scaffold(str(repo.root), NewScenario("kvad/squares", "API", STEPS), API_ONLY)


@pytest.mark.parametrize("sid", ["squares", "Kvad/Squares", "kvad/sq uares", "../x/y"])
def test_new_refuses_an_id_that_is_not_feature_slash_name(repo: TempRepo, sid: str) -> None:
    write_e2e_project(repo)

    with pytest.raises(E2EError, match="is not <feature>/<name>"):
        scaffold(str(repo.root), NewScenario(sid, "API", STEPS), API_ONLY)
