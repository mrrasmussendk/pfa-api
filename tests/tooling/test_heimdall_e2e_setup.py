"""``heimdall e2e``'s setup: the project's settings (``harness/e2e/system.json``), the feature files and the
tests of their scenarios, the tools that run them, and ``heimdall e2e new``, which scaffolds a scenario and its failing tests."""

from __future__ import annotations

import json
import re

import pytest
from conftest import E2E_REQUEST, TempRepo, e2e_scenario, write_e2e_project, write_e2e_scenario

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
    parse_feature,
    scaffold,
    system_runner,
)

API_ONLY = System(tools={"API": "postman"})
API_AND_UI = System(tools={"API": "postman", "UI": "playwright"})
STEPS = Steps(("a text",), ("I send it to POST /split",), ("I receive the chunks", "they are in order"))


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


def test_each_scenario_in_a_feature_file_loads_with_its_feature_from_the_file_and_its_method_from_the_tag(repo: TempRepo) -> None:
    write_e2e_project(repo)
    write_e2e_scenario(repo, "kvad/rounds")

    scenarios, problems = load_scenarios(str(repo.root), API_ONLY)

    assert problems == []
    assert [(s.id, s.feature, s.method, s.tools) for s in scenarios] == [
        ("kvad/squares", "kvad", "API", ("postman",)),
        ("kvad/rounds", "kvad", "API", ("postman",)),
        ("rune/carves", "rune", "API", ("postman",)),
    ]


def test_and_continues_the_step_before_it_so_each_then_clause_is_its_own() -> None:
    text = "@API\nFeature: kvad\n  Scenario: q\n    Given a\n    And b\n    When c\n    Then d\n    But e\n"

    (s,), problems = parse_feature(text, "kvad", API_ONLY)

    assert problems == [] and (s.given, s.when, s.then) == (("a", "b"), ("c",), ("d", "e"))


def test_a_scenario_knows_the_lines_it_spans_tags_included() -> None:
    text = "@API\nFeature: kvad\n\n  @API\n  Scenario: q\n    Given a\n    When c\n    Then d\n"

    (s,), _ = parse_feature(text, "kvad", API_ONLY)

    assert s.lines == (4, 8)


def test_a_scenario_tag_overrides_the_feature_s_method_and_tool(repo: TempRepo) -> None:
    text = "@API\nFeature: kvad\n  @UI @tool:playwright\n  Scenario: q\n    Given a\n    When c\n    Then d\n"

    (s,), _ = parse_feature(text, "kvad", API_AND_UI)

    assert (s.method, s.tools) == ("UI", ("playwright",))


def test_a_scenario_missing_a_step_is_a_problem_naming_the_file_and_the_step(repo: TempRepo) -> None:
    write_e2e_project(repo)
    repo.write_file("tests/e2e/kvad.feature", "@API\nFeature: kvad\n  Scenario: broken\n    Given g\n    When w\n")

    _, problems = load_scenarios(str(repo.root), API_ONLY)

    assert problems == ["tests/e2e/kvad.feature: scenario broken (line 3): no Then step"]


def test_steps_out_of_order_are_a_problem_naming_the_line(repo: TempRepo) -> None:
    write_e2e_project(repo)
    repo.write_file("tests/e2e/kvad.feature", "@API\nFeature: kvad\n  Scenario: q\n    When w\n    Given g\n    Then t\n")

    _, problems = load_scenarios(str(repo.root), API_ONLY)

    assert problems == ["tests/e2e/kvad.feature: line 5: `Given` after `When` — a scenario's steps go Given, When, Then"]


@pytest.mark.parametrize("keyword", ["Background:", "Scenario Outline: q", "Examples:"])
def test_gherkin_beyond_the_scenario_shape_is_a_problem(repo: TempRepo, keyword: str) -> None:
    write_e2e_project(repo)
    repo.write_file("tests/e2e/kvad.feature", f"@API\nFeature: kvad\n  {keyword}\n")

    _, problems = load_scenarios(str(repo.root), API_ONLY)

    assert len(problems) == 1 and "is not supported" in problems[0]


def test_a_feature_line_that_does_not_name_the_file_s_feature_is_a_problem(repo: TempRepo) -> None:
    write_e2e_project(repo)
    repo.write_file("tests/e2e/kvad.feature", "@API\nFeature: Squares\n")

    _, problems = load_scenarios(str(repo.root), API_ONLY)

    assert problems == ["tests/e2e/kvad.feature: line 2: `Feature: Squares` — the file names feature `kvad`"]


def test_a_scenario_name_used_twice_is_a_problem(repo: TempRepo) -> None:
    write_e2e_project(repo)
    write_e2e_scenario(repo, "kvad/squares")

    _, problems = load_scenarios(str(repo.root), API_ONLY)

    assert problems == ["tests/e2e/kvad.feature: scenario squares (line 9): a scenario of that name comes earlier in the file"]


def test_a_scenario_without_a_method_tag_is_a_problem(repo: TempRepo) -> None:
    write_e2e_project(repo)
    repo.write_file("tests/e2e/kvad.feature", "Feature: kvad\n  Scenario: q\n    Given g\n    When w\n    Then t\n")

    _, problems = load_scenarios(str(repo.root), API_ONLY)

    assert len(problems) == 1 and "no method tag" in problems[0]


def test_a_scenario_naming_a_tool_the_project_does_not_use_is_a_problem(repo: TempRepo) -> None:
    write_e2e_project(repo)
    write_e2e_scenario(repo, "kvad/clicks", tags="@tool:cypress")

    _, problems = load_scenarios(str(repo.root), API_ONLY)

    assert problems == [
        (
            "tests/e2e/kvad.feature: scenario clicks (line 9): "
            "tools ['cypress'] — this project runs scenarios with ['postman'] (harness/e2e/system.json)"
        )
    ]


def test_a_scenario_whose_tool_cannot_test_its_method_is_a_problem(repo: TempRepo) -> None:
    write_e2e_project(repo)
    write_e2e_scenario(repo, "kvad/screen", tags="@UI @tool:postman")

    _, problems = load_scenarios(str(repo.root), API_AND_UI)

    assert problems == ["tests/e2e/kvad.feature: scenario screen (line 9): postman cannot test method 'UI'; it tests ['API']"]


def test_a_scenario_without_its_test_is_a_problem_naming_where_it_goes(repo: TempRepo) -> None:
    write_e2e_project(repo)
    write_e2e_scenario(repo, "kvad/rounds", request=None)

    _, problems = load_scenarios(str(repo.root), API_ONLY)

    assert problems == [
        "tests/e2e/kvad.feature: scenario rounds: no postman test runs it — write postman/collections/e2e/kvad/rounds.request.yaml"
    ]


def test_a_test_must_assert_each_then_clause_by_name_and_nothing_else(repo: TempRepo) -> None:
    write_e2e_project(repo)
    write_e2e_scenario(repo, "kvad/rounds", then=("I receive a response", "it is round"))
    repo.write_file(
        "postman/collections/e2e/kvad/rounds.request.yaml", E2E_REQUEST.replace("'then: I receive a response'", "'then: I get one'")
    )

    _, problems = load_scenarios(str(repo.root), API_ONLY)

    prefix = "tests/e2e/kvad.feature: scenario rounds: postman/collections/e2e/kvad/rounds.request.yaml: "
    assert problems == [
        prefix + "no assertion named 'then: I receive a response'",
        prefix + "no assertion named 'then: it is round'",
        prefix + "asserts 'then: I get one', which the scenario's Then does not say",
    ]


def test_a_postman_test_must_send_the_request_its_when_names(repo: TempRepo) -> None:
    write_e2e_project(repo)
    write_e2e_scenario(repo, "kvad/rounds", request=E2E_REQUEST.replace("/run", "/walk"))

    _, problems = load_scenarios(str(repo.root), API_ONLY)

    assert problems == [
        (
            "tests/e2e/kvad.feature: scenario rounds: postman/collections/e2e/kvad/rounds.request.yaml: "
            "sends POST /walk, but the When says POST /run"
        )
    ]


def test_a_feature_file_that_cannot_be_read_as_one_names_the_line(repo: TempRepo) -> None:
    write_e2e_project(repo)
    repo.write_file("tests/e2e/kvad.feature", "@API\nFeature: kvad\n  Scenario: q\n    Given g\n    Whenever w\n")

    _, problems = load_scenarios(str(repo.root), API_ONLY)

    assert problems == ["tests/e2e/kvad.feature: line 5: not a step — start it with Given, When, Then, And or But"]


def test_a_scenario_knows_its_feature_file_and_its_tests_across_its_tools() -> None:
    s = e2e_scenario("kvad/squares", tools=("postman", "pytest"))

    assert s.spec_path == "tests/e2e/kvad.feature"
    assert s.test_paths == {"postman/collections/e2e/kvad/squares.request.yaml", "tests/e2e/kvad/squares_e2e.py"}


# ---------------------------------------------------------------- the tools


@pytest.mark.parametrize("name", sorted(TOOLS))
def test_every_tool_stub_asserts_each_then_clause_and_fails_until_written(name: str) -> None:
    tool = TOOLS[name]
    s = Scenario("kvad/q", "kvad", "API", (name,), ("g",), ("w",), ("it answers", "it is round"))

    stub = tool.stub(s)

    assert tool.asserted(stub) == ["it answers", "it is round"] and stub.count(NOT_WRITTEN) == 2


def test_a_pytest_stub_stays_valid_python_whatever_the_steps_say() -> None:
    s = Scenario("kvad/q", "kvad", "service", ("pytest",), ('a """quoted""" text',), ("I call it",), ('it says "hi" \\o/',))

    stub = TOOLS["pytest"].stub(s)

    compile(stub, "q_e2e.py", "exec")
    assert TOOLS["pytest"].asserted(stub) == ['it says "hi" \\o/']


def test_a_postman_stub_sends_the_request_its_when_names() -> None:
    s = Scenario("kvad/q", "kvad", "API", ("postman",), ("g",), ("I send it to PUT /kvad/rounds",), ("t",))

    assert TOOLS["postman"].check_test(s, TOOLS["postman"].stub(s)) == []


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


def test_new_adds_the_scenario_to_its_feature_file_and_writes_a_failing_test_with_the_method_s_tool(repo: TempRepo) -> None:
    write_e2e_project(repo)

    written = scaffold(str(repo.root), NewScenario("kvad/rounds", "API", STEPS), API_ONLY)

    assert written == ["tests/e2e/kvad.feature", "postman/collections/e2e/kvad/rounds.request.yaml"]
    assert repo.read_file("tests/e2e/kvad.feature").endswith(
        "  Scenario: rounds\n    Given a text\n    When I send it to POST /split\n    Then I receive the chunks\n    And they are in order\n"
    )


def test_new_starts_a_feature_file_tagged_with_the_method(repo: TempRepo) -> None:
    write_e2e_project(repo)

    scaffold(str(repo.root), NewScenario("tavl/first", "API", STEPS), API_ONLY)

    assert repo.read_file("tests/e2e/tavl.feature").startswith("@API\nFeature: tavl\n\n  Scenario: first\n")


def test_a_scaffolded_scenario_loads_without_problems(repo: TempRepo) -> None:
    write_e2e_project(repo)
    scaffold(str(repo.root), NewScenario("kvad/rounds", "API", STEPS), API_ONLY)

    scenarios, problems = load_scenarios(str(repo.root), API_ONLY)

    assert problems == [] and scenarios[1].id == "kvad/rounds" and scenarios[1].then == ("I receive the chunks", "they are in order")


def test_new_takes_another_tool_the_project_uses_and_tags_the_scenario_with_it(repo: TempRepo) -> None:
    write_e2e_project(repo)

    written = scaffold(str(repo.root), NewScenario("kvad/page", "UI", STEPS, tool="playwright"), API_AND_UI)
    scenarios, problems = load_scenarios(str(repo.root), API_AND_UI)

    assert written[1] == "tests/e2e/kvad/page.spec.ts"
    assert problems == [] and (scenarios[1].id, scenarios[1].method, scenarios[1].tools) == ("kvad/page", "UI", ("playwright",))


def test_new_refuses_an_api_scenario_whose_when_names_no_request(repo: TempRepo) -> None:
    write_e2e_project(repo)

    with pytest.raises(E2EError, match="its When must name the one request it sends"):
        scaffold(str(repo.root), NewScenario("kvad/rounds", "API", STEPS._replace(when=("I split it",))), API_ONLY)


def test_new_refuses_a_method_the_project_has_no_tool_for(repo: TempRepo) -> None:
    write_e2e_project(repo)

    with pytest.raises(E2EError, match="method 'UI' has no tool"):
        scaffold(str(repo.root), NewScenario("kvad/page", "UI", STEPS), API_ONLY)


def test_new_never_overwrites_a_scenario(repo: TempRepo) -> None:
    write_e2e_project(repo)
    (repo.root / "postman/collections/e2e/kvad/squares.request.yaml").unlink()

    with pytest.raises(E2EError, match=re.escape("already exists: scenario squares in tests/e2e/kvad.feature")):
        scaffold(str(repo.root), NewScenario("kvad/squares", "API", STEPS), API_ONLY)


def test_new_never_overwrites_a_test(repo: TempRepo) -> None:
    write_e2e_project(repo)

    with pytest.raises(E2EError, match=re.escape("already exists: postman/collections/e2e/kvad/squares.request.yaml")):
        scaffold(str(repo.root), NewScenario("kvad/squares", "API", STEPS), API_ONLY)


@pytest.mark.parametrize("sid", ["squares", "Kvad/Squares", "kvad/sq uares", "../x/y"])
def test_new_refuses_an_id_that_is_not_feature_slash_name(repo: TempRepo, sid: str) -> None:
    write_e2e_project(repo)

    with pytest.raises(E2EError, match="is not <feature>/<name>"):
        scaffold(str(repo.root), NewScenario(sid, "API", STEPS), API_ONLY)
