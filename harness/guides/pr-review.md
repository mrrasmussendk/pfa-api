# PR review: fault checks per function, verified on the PR

| ✅ Do | ❌ Don't |
|---|---|
| Describe the intended change in the PR title/body or `--task`. | Make the reviewer infer the task from the diff alone. |
| Fix what a block names: it gives the function, the line and the fix. | Treat a favourable answer as permission to bypass a rule, or as approval to merge. |
| Read the unsure checks listed under a comment. | Treat a skipped review as a pass. |
| Ask for a human when the outcome is `escalate`. | Treat a probability as proof. |

A person always decides the merge. The review never says a change is safe; it looks for faults someone made and says where they are.

## Why it asks this way

Jev (TypeSafe AI) answers **typed** questions with probabilities and never says where. Its own guidance is to ask "the most explicit, narrow, specific, atomic questions you can", one per candidate: a broad question hides several judgments behind one answer. The earlier review asked broad ones (`safe_to_merge`, `correctness` of the whole diff) and got blocks that named no place: the agent was told to "split each function" without being told which, three rounds of real fixes moved `p(safe)` by nothing, and every PR's `safe_to_merge` came back No. So the review now asks narrow fault checks **about each changed function**, built in code, and a failing answer names its function.

## Two pipelines

| | Agent pipeline | PR pipeline |
|---|---|---|
| Runs | locally and in the `Stop` hook: `heimdall review` | in CI on every PR: `heimdall review --claude` |
| Jev | answers every check | answers every check |
| A failing check | **blocks**, naming the function | goes to Claude; blocks only if Claude **confirms** it with lines quoted from the file |
| An unsure check | a note | goes to Claude; blocks only if confirmed |
| Claude | never called, key or not | Sonnet 5.5 verifies failing and unsure checks |

| Outcome | Meaning | Check |
|---|---|---|
| 🛑 `request_changes` | an Eitri wall violation, a failing check (confirmed by Claude on the PR), or a file below the clean-code target | **fails** |
| 🙋 `escalate` | nothing failed but the blast radius is wide, or Claude could not decide a failing check | passes with a warning and the `needs-human-review` label |
| 💬 `comment` | no check failed; the unsure ones are listed | passes |

There is no `approve`. ⚠️ **Skipped is not passed:** forks do not receive secrets; without `TYPESAFE_API_KEY` the review is skipped and says so.

## What Jev is asked

`tools/heimdall/commands/review/items.py` finds every function the diff touches, reads it whole from the working tree, and marks the ones in a feature's HTTP edge as routes; the application comes first, then the tooling, within `MAX_ITEM_TOKENS` (14 000; a function over 1 500 is cut). They go into the state as `functions.f1`, `functions.f2`, …, and `tools/heimdall/commands/review/questions.py` builds the checks. Every one is phrased so that **yes** (or a named file) is the fault.

| Check | Asked about | The fault |
|---|---|---|
| `mixes_concerns` | each function | two things that would each stand as a function (style) |
| `mode_parameter` | each function | a boolean or mode parameter that makes it do a different job (style) |
| `mishandles_edge` | each function | an empty, None, zero or boundary input handled wrongly |
| `swallows_error` | each function | an exception caught and carried on from |
| `untested` | each function | changed with no test in the diff exercising it |
| `unsafe_input` | each function | outside input into a shell, path, query or log unvalidated |
| `error_not_problem` | each route | an error answered other than by raising `Problem` |
| `leaks_internals` | each route | a stack trace, path, secret or raw exception text in a response |
| `out_of_scope` | the change (choice of file) | a file that does not serve the task |
| `misplaced` | the change (choice of file) | code placed where `architecture.rules` say it does not belong |
| `docs_stale` | the change | behaviour changed with no documentation updated |
| `clean_code`, `clean_code_limit` | each edited application file, whole, in its own call | below 2.7 on 0–3, and what limits it |

Facts are not asked. Eitri's findings on the changed files (`facts.wall_violations`) block on their own. **Blast radius is computed** (`policy.blast_radius`): wide when the kernel changed or a frozen contract did; moderate for a contract, the application outside the features, a shared package or several features; contained otherwise.

## The policy

Each answer becomes a row: the probability of the fault, where (`path:line function`, a file, or the change), and a state. Thresholds live in `T` in `tools/heimdall/commands/review/policy.py`:

- **fault checks** fail at `p ≥ 0.60`, are unsure from `0.40`;
- **style checks** (`mixes_concerns`, `mode_parameter`) fail at `p ≥ 0.90`, are unsure from `0.75` — see calibration below;
- **which-file checks** fail when a file is named at `p ≥ 0.50`, unsure from `0.30`;
- **clean code** below `2.7` blocks naming the file, with the judge's most probable *actionable* limit as the fix (`none` is not a fix; its runner-up is). 2.7 is the floor the author set and is not to be lowered;
- **blast radius** wide escalates when nothing else blocks.

An unanswered check reads as a pass: a fault is what the judge found, never what it left out.

## Claude verifies on the PR

In the PR pipeline every failing and unsure check is put to Claude (`claude.py`, `claude-sonnet-5-5`, `fallbacks: "default"`) with the task, the diff and the changed files whole, up to 120 000 tokens. Claude is asked to refute first and to confirm only with a place: the file, the function, and one to five lines quoted from the file. `verify.grounded` finds those lines in the file before it believes them.

| Claude rules | A failing check | An unsure check |
|---|---|---|
| confirmed, quote found in the file | blocks, naming `file:line function` and the fix | blocks |
| refuted | dropped | dropped |
| undecided, or a quote not in the file | escalates (`unverified: …`) | dropped |
| not asked: no `ANTHROPIC_API_KEY`, or the call failed | escalates | dropped |

⚠️ **Check:** a key not scoped to a workspace answers 400 "must include the anthropic-workspace-id header": use a workspace-scoped key, or set `ANTHROPIC_WORKSPACE_ID`.

## Size

Jev reads one request of 64k tokens, the state and the questions in it ([docs.typesafe.ai/models](https://docs.typesafe.ai/models)); input costs $0.042 per million tokens, so the window is the constraint, not the price. The state is held to 28 000 tokens (`MAX_STATE_TOKENS`, measured with Brokkr): the facts, the functions, then the diff in priority order (source, tests, tooling, CI, docs), each file at most 6 000 tokens; a cut file is listed under `files_truncated`, one the budget no longer reaches under `files_not_shown`. Files `.gitattributes` marks `linguist-vendored` or `linguist-generated` are never sent. Checks that do not fit beside the state in one request (`MAX_REQUEST_TOKENS`, 56 000) go in batches with the same state; Jev answers each question in isolation, so a batch's answers mean what they would in one call. On this branch: about 650 checks on some 110 functions, in two requests, about two seconds.

## Calibration so far

The numbers in `T` are a starting point; the only proof is your own merges (`heimdall review --json` on past PRs, compared with what reviewers did).

- **2026-10-07, the walls.** The same `EIT001` import scored `p(respects_slice_walls)=0.25` written bluntly and `0.62` dressed in a persuasive docstring. That is why Eitri's facts block on their own and Jev is never asked to re-derive them.
- **2026-10-08, readability.** Eight runs withheld the top level for the subject, not the craft, naming no limit; six edits moved it by under 0.05. A score that names nothing to fix was dropped.
- **2026-10-09, clean code per file.** The whole-diff score named no file and its limit label could be an average found in no file; judged per file whole, two route files fell below target and were named.
- **2026-10-10, the broad questions.** `safe_to_merge` came back No on every PR and `single_purpose` of the whole diff named no function; both broad questions were replaced by the per-function checks above.
- **2026-10-10, the first per-function run** (this branch, 110 functions): every fault check was quiet (no fail), while `mixes_concerns` failed 26 small helpers at 0.62–0.85, partly because the question named "computing and reporting" as two things. The wording was narrowed and the style checks now fail only at 0.90: the next run had no failure, four style checks unsure, and 31 `untested` unsure at 0.40–0.52 (Jev sees the tests in the diff, not the suite).

## Function shape: the same limits while you edit

The AST states two facts about a function: its length and its parameter count. The hook's `function_shape` sensor reads the edited file after every `Edit` or `Write`, and a function the edit touched over 40 lines or 5 parameters (`tools/heimdall/shape.py`) gets `Heimdall: the function you just edited is over the shape limit … path:line name (52 lines, 7 params)` and exit 2. The review lists the same functions under "over the limits" as a fact for the reader; it does not decide on them.

✅ Split the function, or group the values that travel together, before moving on. ❌ Don't raise the limits to make an edit pass.

## Running it locally

```bash
export TYPESAFE_API_KEY=…                      # from https://console.typesafe.ai/
heimdall review                                 # working tree vs HEAD: the agent pipeline
heimdall review --base origin/main --task "…"   # the committed branch, as the Stop hook runs it
heimdall review --diff change.diff --dry-run    # print every call, call nothing
```

Exit codes: `0` comment · `1` request_changes · `2` escalate · `3` could not review (no diff, no key, API error). Each review appends a `{"event": "review", "kind": "<outcome>", ...}` line to `.heimdall/telemetry.jsonl`.

## Checklist before opening a PR

- [ ] The PR title/body describes the intended behaviour and scope.
- [ ] Lint, formatting, types, tests and `eitri check --root .` pass, and no `Heimdall:` shape feedback was left unanswered.
- [ ] The slice map is current after route, dependency, slice or shared-package changes.
- [ ] Every block the agent pipeline named is fixed and the review rerun; `escalate` is referred to a human.
