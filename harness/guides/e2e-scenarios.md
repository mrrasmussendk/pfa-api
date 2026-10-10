# End-to-end scenarios

✅ **Do** marks the expected approach. ❌ **Don't** marks a prohibited shortcut. ⚠️ **Check** marks a condition to resolve before continuing.

| ✅ Do | ❌ Don't |
|---|---|
| Start a use case with `heimdall e2e new <feature>/<name> --method … --given … --when … --then …`. | Hand-write the files, invent fields, or put a scenario where the folder does not name its feature. |
| Make the stub it writes assert exactly the `then`, one assertion per clause. | Delete the failing assertion without replacing it, or assert more than the scenario says. |
| Reach the system through the base URL the tool is given. | Hard-code a host or port, or depend on a service you started by hand. |
| Write the scenario the Stop hook asks for, for the feature it names. | Silence the request by deleting a scenario, or weaken a `then` to make a red run pass. |

Unit tests prove a piece against fakes. A scenario proves the promise from outside: the real system, started the way a user or client meets it. That is the agent checking itself. The Stop hook runs the scenarios a change reaches and hands back what failed or what is missing, so the agent is prompted again until the behaviour holds. A run costs a system start, so not every scenario runs after every change; Jev decides which ones the change can reach.

## Any kind of project

The harness does not assume an API. Two things in a scenario say what it is:

- **`method`** names the kind of behaviour it describes: `API`, `UI`, `service`, `CLI`, or whatever the project needs.
- **`tools`** names what runs it.

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
- `tools` maps each method to the tool `heimdall e2e new` scaffolds with. It is also the set of tools the project's scenarios may use. An API and frontend project might say `{"API": "postman", "UI": "playwright"}`; a background service might say `{"service": "pytest"}`.

| Tool | Methods | Its test | Run with | Base URL |
|---|---|---|---|---|
| `postman` | `API` | `postman/collections/e2e/<feature>/<name>.request.yaml` | `postman collection run … -i <feature>/<name>` | `{{base_url}}` |
| `playwright` | `UI`, `API` | `tests/e2e/<feature>/<name>.spec.ts` | `npx playwright test <files>` | `BASE_URL` |
| `pytest` | any | `tests/e2e/<feature>/<name>_e2e.py` | `python -m pytest <files>` | `E2E_BASE_URL` |

The shared `<feature>/<name>` links a scenario to its tests, so the scenario format needs no field for it. A pytest scenario's test is named `_e2e.py` so the unit run, which collects `test_*.py`, leaves it alone. A new tool is one class in `tools/heimdall/commands/e2e/tools.py`: where its test lives, its failing stub, the text a real test contains, and its run command.

## Writing one

```bash
heimdall e2e new chunking/empty-text-is-a-problem --method API \
  --given "a text that is only whitespace" \
  --when "I send it to POST /chunking/split" \
  --then "I receive 422 as an application/problem+json document whose detail says the text is empty"
```

1. The command writes the scenario to `tests/e2e/<feature>/<name>.json` in the one shape every scenario has: `{"method": …, "tools": […], "scenario": {"given": …, "when": …, "then": …}}`.
2. It also writes one stub per tool that **fails until it is written**: it asserts nothing but `not written yet: assert the 'then'`.
3. Fill the stub in. For Postman, set the method, URL and body to send the `when`, then write one `pm.test` per clause of the `then`, each named `then: …`, so a failure names the clause that broke. Follow `postman/skills/collection-schema-v3/SKILL.md` and check the result with `postman collection lint postman/collections/e2e`.

The command never overwrites, and it refuses a method `system.json` has no tool for, unless you pass `--tool`.

## What the Stop hook does with it

`.claude/hooks/finish.py` runs `heimdall e2e --base origin/main` after `pytest` is green and before the review:

1. **The change.** It takes everything since the merge base with `origin/main`: committed, uncommitted and untracked files. The slice map classifies the paths, so the hook knows which features changed, which features depend on them, and whether the kernel or the application changed.
2. **The questions.** Jev is asked one yes/no question per scenario: *could this change alter what the scenario observes?* It is also asked whether the change adds observable behaviour that no scenario describes. Jev reads the hunks of application files only; docs, tests and tooling are listed by path. When no application code changed, Jev is not asked at all, and only the scenarios whose own files changed run.
3. **The policy.** The thresholds live in `tools/heimdall/commands/e2e/judge.py` (`T`). As in the review, facts come first:
   - A scenario whose own files changed (the `.json` or any of its tests) always runs.
   - A feature that changed and has no scenario always asks for one.
   - Otherwise, a scenario runs at `rerun ≥ 0.30`, and a new one is requested at `needs_new_scenario ≥ 0.60`.
4. **The run.** The hook starts the system from `system.json` and runs each tool's batch against it, then stops the system. The system's log is kept in `.heimdall/e2e-service.log`.

| Exit | Meaning | The hook |
|---|---|---|
| 0 | Green, or nothing the change reaches | Reports one line; remembers the change's fingerprint in `.heimdall/e2e.json`, so the next stop with the same change runs nothing |
| 1 | A scenario failed, including a stub not written yet | Blocks with the tools' output |
| 2 | A scenario is missing, does not follow the scenario shape, or its test is missing or asserts nothing | Blocks, handing back the `heimdall e2e new` command or the file to fix |
| 3 | Could not run (no `system.json`, a tool not on PATH, the system did not start) | Reports to the user; does not block |

⚠️ **Check:** without `TYPESAFE_API_KEY`, or when Jev gives no verdict, the slice map decides instead. It runs the scenarios of every changed feature and of every feature that depends on one, or every scenario when the kernel or the application changed.

```bash
heimdall e2e --base origin/main            # what the hook runs
heimdall e2e --base origin/main --force    # run again even when the last green run saw this change
```

## In CI

The Stop hook runs only the scenarios a change reaches, and only when an agent stops. CI runs all of them on every push and pull request, so a human's change is held to the same promise. The `e2e scenarios` job in `.github/workflows/ci.yml` runs:

```bash
heimdall e2e --all    # every scenario: no change, no Jev, no remembered green run
```

The runner is the environment. Heimdall starts the service from `harness/e2e/system.json` on the runner itself, and Postman runs the requests from the checkout, so nothing is deployed and no Postman login is needed. The exit codes are the ones above. The job caches the model weights and loads them before the run, so no request waits on a 2.2 GB download.
