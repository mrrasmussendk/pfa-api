# End-to-end scenarios

✅ **Do** marks the expected approach. ❌ **Don't** marks a prohibited shortcut. ⚠️ **Check** marks a condition to resolve before continuing.

| ✅ Do | ❌ Don't |
|---|---|
| Start a use case with `heimdall e2e new <feature>/<name> --method … --given … --when … --then …`, repeating a step for each further clause. | Hand-write the test files, or reach for Gherkin beyond the scenario shape (`Background`, outlines, tables, step bindings). |
| Make the stub it writes assert each `then` clause under its name, `then: <clause>`. | Delete a failing assertion without replacing it, rename one away from its clause, or assert more than the scenario says. |
| Change the scenario's text and its test together. | Leave the text saying one thing while the test does another. |
| Reach the system through the base URL the tool is given. | Hard-code a host or port, or depend on a service you started by hand. |
| Write the scenario the Stop hook asks for, for the feature it names. | Silence the request by deleting a scenario, or weaken a `then` to make a red run pass. |

Unit tests prove a piece against fakes. A scenario proves the promise from outside: the real system, started the way a user or client meets it. That is the agent checking itself. The Stop hook runs the scenarios a change reaches and hands back what failed or what is missing, so the agent is prompted again until the behaviour holds. A run costs a system start, so not every scenario runs after every change; Jev decides which ones the change can reach.

## Any kind of project

The harness does not assume an API. A scenario's tags say what it is:

- **The method tag** names the kind of behaviour it describes: `@API`, `@UI`, `@service`, `@CLI`, or whatever the project needs. A tag on the `Feature` applies to all its scenarios; a scenario's own tag overrides it.
- **`@tool:<tool>`** names what runs it, only when it is not the tool `system.json` gives the method.

`harness/e2e/system.json` is the project's part. It is harness settings, not a scenario, so it sits with the harness:

```json
{
  "start": ["{python}", "-m", "pfa.api"],
  "port_env": "PFA_PORT",
  "ready": "/health",
  "tools": {"API": "postman"}
}
```

- `start`, `port_env` and `ready` are optional.
  - `start` is the command that starts the system under test.
  - `{python}` is the interpreter running Heimdall, and `{port}` is the free port picked for the run; `port_env` passes that port to the system.
  - `ready` is the path polled until the system answers.
- Leave `start` out when the scenarios start what they need, as a CLI or a library's do.
- `tools` maps each method to the tool that runs its scenarios. It is also the set of tools the project's scenarios may use, and its keys the method tags. An API and frontend project might say `{"API": "postman", "UI": "playwright"}`; a background service might say `{"service": "pytest"}`.

| Tool | Methods | Its test | Run with | Base URL |
|---|---|---|---|---|
| `postman` | `API` | `postman/collections/e2e/<feature>/<name>.request.yaml` | `postman collection run … -i <feature>/<name>` | `{{base_url}}` |
| `playwright` | `UI`, `API` | `tests/e2e/<feature>/<name>.spec.ts` | `npx playwright test <files>` | `BASE_URL` |
| `pytest` | any | `tests/e2e/<feature>/<name>_e2e.py` | `python -m pytest <files>` | `E2E_BASE_URL` |

The shared `<feature>/<name>` — the feature file and the `Scenario:` name — links a scenario to its tests, so the scenario needs no field for it. A pytest scenario's test is named `_e2e.py` so the unit run, which collects `test_*.py`, leaves it alone. A new tool is one class in `tools/heimdall/commands/e2e/tools.py`: where its test lives, its failing stub, the text a real test contains, and its run command.

## Writing one

A feature's scenarios are one Gherkin file, `tests/e2e/<feature>.feature`, so its whole promise reads on one page:

```gherkin
@API
Feature: chunking

  Scenario: empty-text-is-a-problem
    Given a text that is only whitespace
    And a budget of 12 tokens
    When I send them to POST /chunking/split
    Then I receive 422
    And the body is an application/problem+json document
    And its detail says the text is empty
```

The steps are prose, not bindings: nothing executes them. Each `Then`, and each `And` or `But` after it, is one clause, and the test asserts each clause under its name. Postman and Playwright name an assertion by the test's title; pytest by the test function's one-line docstring. The scenario's name is lower-case letters, digits, `-` and `_`, and is unique in the file.

```bash
heimdall e2e new chunking/empty-text-is-a-problem --method API \
  --given "a text that is only whitespace" --given "a budget of 12 tokens" \
  --when "I send them to POST /chunking/split" \
  --then "I receive 422" \
  --then "the body is an application/problem+json document" \
  --then "its detail says the text is empty"
```

1. The command adds the scenario to `tests/e2e/<feature>.feature`, starting the file, tagged with the method, when the feature has none yet. Each repeated step becomes an `And`.
2. It writes one test per tool that **fails until it is written**: one assertion per `then` clause, each named `then: <clause>` and failing with ``not written yet: assert the `then` ``. For Postman it also sets the method and URL the `when` names.
3. Fill the stub in. For Postman, add the body that sends what the `given` and `when` say, and make each `pm.test` assert its clause. Follow `postman/skills/collection-schema-v3/SKILL.md` and check the result with `postman collection lint postman/collections/e2e`.

The command never overwrites, and it refuses a method `system.json` has no tool for, unless you pass `--tool`.

### How the text is held to the test

| Check | When | What it catches |
|---|---|---|
| Clause for clause | Every load: the Stop hook and CI | A `then` clause with no `then: <clause>` assertion, an assertion no clause states, one asserted twice |
| The request | Every load, Postman tests | A request whose method or path is not the `<METHOD> /<path>` the `when` names |
| Jev | When a scenario's own lines or its test changed, and `TYPESAFE_API_KEY` is set | A test that sends or asserts what the text does not say, or leaves out what it does |

A failed check blocks the stop (exit 2) with the scenario, the file and what disagrees. Fix whichever side is wrong: the text when the behaviour moved on, the test when it no longer tests what was promised.

## What the Stop hook does with it

`.claude/hooks/finish.py` runs `heimdall e2e --base origin/main` after `pytest` is green and before the review:

1. **The change.** It takes everything since the merge base with `origin/main`: committed, uncommitted and untracked files. The slice map classifies the paths, so the hook knows which features changed, which features depend on them, and whether the kernel or the application changed.
2. **The questions.** Jev is asked one yes/no question per scenario: *could this change alter what the scenario observes?* It is also asked whether the change adds observable behaviour that no scenario describes, and, for each scenario whose text or test changed, whether the two still agree; it reads those tests in full. Otherwise Jev reads the hunks of application files only; docs, tests and tooling are listed by path. When no application code changed, Jev is asked only the agreement questions, and only the scenarios whose own files changed run.
3. **The policy.** The thresholds live in `tools/heimdall/commands/e2e/judge.py` (`T`). As in the review, facts come first:
   - A scenario whose own files changed (its lines of the feature file, or any of its tests) always runs, and Jev is asked whether its text still says what its test does (`drift ≥ 0.50` asks for a fix).
   - A feature that changed and has no scenario always asks for one.
   - Otherwise, a scenario runs at `rerun ≥ 0.30`, and a new one is requested at `needs_new_scenario ≥ 0.60`.
4. **The run.** The hook starts the system from `system.json` and runs each tool's batch against it, then stops the system. The system's log is kept in `.heimdall/e2e-service.log`.

| Exit | Meaning | The hook |
|---|---|---|
| 0 | Green, or nothing the change reaches | Reports one line; remembers the change's fingerprint in `.heimdall/e2e.json`, so the next stop with the same change runs nothing |
| 1 | A scenario failed, including a stub not written yet | Blocks with the tools' output |
| 2 | A scenario is missing, does not follow the scenario shape, or its test is missing or disagrees with its text | Blocks, handing back the `heimdall e2e new` command or the file to fix |
| 3 | Could not run (no `system.json`, a tool not on PATH, the system did not start) | Reports to the user; does not block |

⚠️ **Check:** without `TYPESAFE_API_KEY`, or when Jev gives no verdict, the slice map decides instead. It runs the scenarios of every changed feature and of every feature that depends on one, or every scenario when the kernel or the application changed.

```bash
heimdall e2e --base origin/main            # what the hook runs
heimdall e2e --base origin/main --force    # run again even when the last green run saw this change
```

## In CI

The Stop hook runs only the scenarios a change reaches, and only when an agent stops. CI runs all of them on every pull request and every push to `main` or a tag, so a human's change is held to the same promise. The `e2e scenarios` job in `.github/workflows/ci.yml` runs:

```bash
heimdall e2e --all    # every scenario: no change, no Jev, no remembered green run
```

The runner is the environment. Heimdall starts the service from `harness/e2e/system.json` on the runner itself, and Postman runs the requests from the checkout, so nothing is deployed and no Postman login is needed. The exit codes are the ones above. The job caches the model weights and loads them before the run, so no request waits on a 2.2 GB download.
