# What the hooks tell you

✅ **Do** marks the expected approach. ❌ **Don't** marks a prohibited shortcut. ⚠️ **Check** marks a condition to resolve before continuing.

| ✅ Do | ❌ Don't |
|---|---|
| Answer a hook's feedback: split the function, check the boundary, plan the migration. | Raise a limit or work around the check to make the feedback go away. |
| Commit before you stop; the review judges committed work only. | Expect the review to see uncommitted changes. |
| Run `eitri check --root .` yourself. | Treat a quiet hook as proof the walls hold. |

Heimdall watches while you work, and checks again before you finish. Its feedback arrives as a hook result: read it as an instruction from the user.

## While you work

With `.heimdall/map.json` present, each Read, Grep, Glob, Edit and Write is classified against the map and logged to `.heimdall/telemetry.jsonl`. A feature's route module in `src/pfa/api/routes/` counts as the feature; the rest of `src/pfa/` outside the features is always in bounds.

A hook exit 2 flags an edit that:

- touches a second feature in the same session: check the dependency boundary;
- touches a frozen contract (fan-in 10 or more): follow [Changing a contract](changing-a-contract.md);
- leaves a function over the shape limit (40 lines or 5 parameters): split it.

⚠️ **Check:** shell reads are invisible to the hook, and without a map nothing is observed. Eitri is the enforcement; the hook is a warning.

## Before you finish

The `Stop` hook (`.claude/hooks/finish.py`) runs, whenever the tree differs from `origin/main`:

1. `pytest`, under coverage.
2. The change, against `origin/main`:
   - `tools/diff_coverage.py`: at least 90% of the application lines you added or edited run under a test. It names the line ranges that don't.
   - `eitri check --base origin/main`: a contract member you removed or changed is no longer used outside its feature ([EIT008](../rules/EIT008.md)).
   - `tools/openapi_compat.py`: the HTTP API breaks no client written against `main`. When the spec changes, regenerate `docs/openapi.json` with `python tools/openapi_compat.py --write`.
3. `heimdall e2e --base origin/main`: the scenarios the change reaches, and whether one is missing. See [End-to-end scenarios](e2e-scenarios.md).
4. `heimdall review --base origin/main`: the agent pipeline. Jev checks each changed function for faults, and a failing check blocks, naming the function and the fix. Claude verifies only on the PR. See [PR review](pr-review.md).

A failing suite, a broken promise in step 2, a failing or missing scenario, or a `request_changes` verdict blocks the stop and hands you the output. `escalate`, or a review that could not run because `TYPESAFE_API_KEY` is unset, is reported to the user instead. That report also carries the session's drift: how many of your reads fell outside the working set, and which.

## After work

`heimdall drift` shows how far the recorded reads strayed from the map across sessions; `heimdall drift --session <id>` shows one. Out-of-bounds reads above 20% suggest the feature boundary needs a look.
