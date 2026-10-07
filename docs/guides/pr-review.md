# PR review with Jev

Every pull request gets two kinds of check. `ci.yml` is the **gate**: Eitri's walls, Brokkr's canaries, the tests. Those are facts, and a fact failing fails the build. `pr-review.yml` is the **judgment**: TypeSafe AI's Jev model reads the diff and answers typed questions about correctness, clean code, tests, security, scope and architecture fit. Heimdall turns those answers into one outcome and a sticky PR comment.

Jev does not write review comments. It answers questions whose answers are **typed**: yes/no with a probability (`noul`), one of a fixed set of options (`choice`), a position on a rubric (`score`). That is why it fits a pipeline: the model judges, the code decides, and the decision is reproducible from the probabilities.

## What runs

```
PR diff (base...HEAD) ─┐
.heimdall/map.json   ──┼─▶ heimdall review ─▶ POST api.typesafe.ai/v1/systemone ─▶ answers ─▶ policy ─▶ outcome
PR title + body      ──┘        │                                                                     │
                                └─ review/review.json · review/review.md · telemetry "review" line ◀──┘
```

| Outcome | Meaning | Check | PR |
|---|---|---|---|
| `approve` | safe, routine, clean, tested | passes | comment |
| `comment` | mergeable, with notes (tests doubtful, code merely acceptable) | passes | comment |
| `request_changes` | security, correctness, wall violation, errors bypassing Problem Details, or not safe | **fails** | comment |
| `escalate` | the model wants a human, or is on the fence | passes with a warning | comment + `needs-human-review` label |

Forks do not receive secrets. Without `TYPESAFE_API_KEY` the review is **skipped**, the comment says so, and the check passes. The gate in `ci.yml` is unaffected.

## What the judge is told

`heimdall review` builds **bounded state**: the facts Heimdall can compute are stated as facts, so Jev spends its judgment on what only judgment can decide.

- **`task`** — the PR title and body (or `--task` locally).
- **`architecture`** — from `.heimdall/map.json`: the kernel, shared packages, every slice's declared dependencies and contract fan-in, and the walls in one sentence each (EIT001–EIT005, frozen contracts, handlers never raise).
- **`facts`** — slices touched, whether the change is cross-slice, contracts touched with fan-in and frozen flag, whether `internal/routes.py` changed, which tests changed, whether docs changed, which files were truncated.
- **`files`** — one entry per changed file: path, status, its **area** (`slice:<name>`, `contract:<name>`, `kernel`, `shared:<pkg>`, `service`, `tests`, `tooling`, `ci`, `docs`), line counts and the hunks.

Caps: 12 000 characters per file, 60 000 for the whole state; the rest is summarised as "N more diff lines not shown" and the file is listed under `files_truncated`. Lock files, minified assets, images and `.heimdall/` are never sent. Missing context produces confident wrong answers, so a truncated review is flagged in the comment.

## The questions

Fixed, typed, greppable: `questions()` in `tools/heimdall/commands/review.py`.

| id | type | judges |
|---|---|---|
| `safe_to_merge` | noul | gate: mergeable as-is |
| `needs_human_review` | noul | gate: wants a person |
| `has_security_concern` | noul | gate: injection, leaks, trust boundaries |
| `tests_cover_change` | noul | changed behaviour is demonstrated by changed tests (only if source changed) |
| `stays_in_scope` | noul | every change serves the task |
| `respects_slice_walls` | noul | imports and placement follow the architecture |
| `errors_use_problem_details` | noul | changed routes raise `Problem`, nothing else (only if routes changed) |
| `leaks_internals` | noul | stack traces, paths, secrets reaching clients |
| `correctness` | score 0–3 | likely broken → clearly correct |
| `clean_code` | score 0–3 | hard to follow → exemplary: names, small functions, no duplication, why-comments |
| `blast_radius` | score 0–2 | contained → wide |
| `docs_in_sync` | score 0–2 | docs stale → in sync |
| `change_kind` | choice | feature / fix / refactor / tests / docs / tooling / mixed |
| `biggest_risk` | choice | the one thing a reviewer should check |

## The policy

Thresholds live in `T` in `tools/heimdall/commands/review/policy.py`, not in the model. In order:

0. **Facts first.** Eitri runs on the changed files before the judge is consulted; any error (`EIT001`–`EIT100`) lands in `facts.wall_violations` and is a `request_changes` on its own. The walls are decided by the import graph, never by a probability — a persuasive docstring cannot argue an import away.
1. **Block** (`request_changes`): `has_security_concern ≥ 0.50`, `leaks_internals ≥ 0.5`, `safe_to_merge ≤ 0.35`, `respects_slice_walls ≤ 0.40`, `biggest_risk = architecture` with `p ≥ 0.80` (the judge's own top-risk label overrides a Yes on the walls), `correctness ≤ 1.25`, or routes changed and `errors_use_problem_details ≤ 0.4`.
2. **Escalate**: `needs_human_review ≥ 0.60`, or `safe_to_merge` within 0.15 of 0.5.
3. **Approve**: `safe_to_merge ≥ 0.75`, `needs_human_review ≤ 0.35`, `has_security_concern ≤ 0.20`, `clean_code ≥ 1.5`, and (no source change or `tests_cover_change ≥ 0.50`).
4. Otherwise **comment**.

Every reason the policy records carries one sentence of advice. The PR comment opens with those sentences under **What went wrong** (or **What the judge wants** on escalate), lists Eitri's findings with the file, the line and the `**Fix:**` line from the rule's page under `docs/rules/`, and only then shows the probability table.

A probability is a policy input, not proof. Before letting `approve` bypass human review, measure these thresholds against your own merge history: `heimdall review --json` on past PRs, compare outcomes to what reviewers actually did, and move the numbers. Missing answers fall back to neutral values, which lands on `escalate`, never on `approve`.

Two calibration points so far (2026-10-07), both the same `EIT001` import of another slice's `internal/`: written bluntly it scored `p(safe)=0.06`, `p(respects_slice_walls)=0.25`; dressed in a docstring selling it as a performance fast path it scored `p(safe)=0.30`, `p(respects_slice_walls)=0.62`, `clean_code` "Exemplary" — and still `biggest_risk = architecture` at `0.96`. The walls rule alone would have let the second one through. That is why Eitri's facts and the top-risk label now sit above it.

## Running it locally

```bash
export TYPESAFE_API_KEY=…                      # from https://console.typesafe.ai/
heimdall review                                 # working tree vs HEAD
heimdall review --base origin/main --task "…"   # what the PR check will see
heimdall review --diff change.diff --dry-run    # print the state and questions, call nothing
git diff main...HEAD | heimdall review --diff - --markdown review.md
```

Exit codes: `0` approve/comment · `1` request_changes · `2` escalate · `3` could not review (no diff, no key, API error). Each real review appends a `{"event": "review", "kind": "<outcome>", "slice": "...", "sensor": "jev"}` line to `.heimdall/telemetry.jsonl`, next to the read/edit lines the hook writes, so a session's judgments sit beside its behaviour.

## Where it sits with the other tools

- **Eitri** decides facts about imports; Jev is never asked to re-derive them. Its findings on the changed files go into the state (`facts.wall_violations`) and into the comment, so Jev judges intent and placement, which an import graph cannot see, and the policy blocks on the import graph, which a docstring cannot talk around.
- **Heimdall's hook** stays stdlib-only and local; it never calls the network on a tool call. The review is a separate command, run by CI or by you.
- **Brokkr** proves the walls still bite. Nothing proves Jev's thresholds are right except your own calibration — treat the numbers in `T` the way `docs/calibration.md` treats the token estimator.
