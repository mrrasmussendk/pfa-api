# Agents: how to work in this repo

Read this before touching code. It is the feedforward half of the harness: the map of what exists and the rules for moving inside it. The feedback half (Heimdall's hooks and `eitri check`) will tell you when you drift — but it is cheaper to not drift.

## What this repo is

- `src/` is the **PFA API**, a FastAPI service built as vertical slices. This is what gets deployed.
  - `src/http_common/` is the HTTP-edge helper package (RFC 9457 problem details). FastAPI-aware, so it is **not** the kernel; only `internal/routes.py` and the composition root import it.
- `tools/` is **development-only tooling** (Eitri, Heimdall, Brokkr). It is never deployed and never imported by `src/`.
- `tests/api` tests the service; `tests/tooling` tests the tooling.

## The dependency direction

Imports only go **down** this arrow. Never up, never sideways into another slice's `internal/`.

```
pfa_api  ──▶  slices/*  ──▶  shared_kernel
(composition   (features:     (primitives: knows nothing
 root: knows    know their     about slices or HTTP)
 every slice)   own code +
                deps' contracts
                + kernel)
```

- **`pfa_api` is NOT part of the kernel and never goes there.** It imports every slice's `internal/module.py`; the kernel must import nothing. Putting the app in the kernel creates a cycle and lets FastAPI into every contract.
- **`shared_kernel` is paid for by every slice** (EIT100) and importable from every contract (EIT003). Only tiny, framework-free, stable primitives belong there. When in doubt, it does not go in the kernel.
- **Shared FastAPI/pydantic helpers** go in `src/http_common/`, which only `internal/` and `pfa_api` import — never the kernel, never a contract. Today it holds the RFC 9457 error machinery (`Problem`, `install_problem_details`).
- **Nothing imports `pfa_api`.** It is the top of the arrow.

## Slice map

<!--heimdall:map-->
kernel: `shared_kernel` · slices: `src/slices` · budget: 15000 tokens per slice · regenerate with `heimdall map --root .`

| slice | depends on | fan-in | contract | routes |
|---|---|---|---|---|
| chunking | (none) | 1 | `src/slices/chunking/contract/` | `POST /chunking/split` |
| embeddings | chunking | 0 | `src/slices/embeddings/contract/` | `POST /embeddings/query`<br>`POST /embeddings/passages`<br>`POST /embeddings/similarity` |
<!--/heimdall:map-->

Each slice's own `AGENTS.md` (in `src/slices/<name>/`) carries its generated `depends on:` and `routes:` lines plus a few hand-written notes on what the slice is for. The table above and those lines are regenerated from `slice.json` and `internal/routes.py` by `heimdall map --root .` — never edit them by hand.

## Guides

Read the guide for the kind of change before loading any code; each one names the exact working set.

- **Adding a route** (new endpoint on an existing slice, with or without a contract change): `docs/guides/adding-a-route.md`.
- **Returning an error** (what a route raises, what the client receives, how to add a problem type): `docs/guides/returning-errors.md`.
- **What the PR check judges** (Jev's typed questions, the policy, how to run it locally): `docs/guides/pr-review.md`.
- **Adding a slice** (a new feature with its own data and rules): the section below.
- **Changing a contract**: the section below, and the fan-in column in the slice map.

## The working set for a task

A task lives in **one slice**. Load, in this order, and nothing more:

1. `src/slices/<slice>/AGENTS.md` — the slice's declared dependencies.
2. `src/slices/<slice>/contract/` — its public surface.
3. `src/slices/<slice>/internal/` — its implementation and router.
4. `src/slices/<dep>/contract/` for each declared dependency — **only the contract**, never `internal/`.
5. `src/shared_kernel/` — the primitives every slice shares.
6. `src/http_common/` — only when the task touches a route's error path (`Problem`); declared shared in `[tool.heimdall]`, so reading it is never drift.

That is the whole context. If you feel you need another slice's `internal/`, the seam is cut wrong: say so instead of reading it.

## The walls (Eitri enforces these; `eitri check --root .` must pass before you finish)

- **EIT001** — never import `slices.<other>.internal...` from a slice. Another slice's public surface is its `contract/` package. The one exemption is `internal/module.py`, which only the composition root imports.
- **EIT002** — no `importlib.import_module`, `__import__`, `sys.path` edits, `sys.modules` writes, or `from x import *` inside `src/slices/`. Coupling must be greppable.
- **EIT003** — `contract/` modules import only the standard library, `shared_kernel`, and their own contract. FastAPI and pydantic belong in `internal/routes.py`, not in a contract.
- **EIT004** — importing `slices.<x>.contract` requires `"x"` in your slice's `slice.json`. Declare first, then import.
- **EIT005** — no `HTTPException` (from `fastapi` or `starlette`) inside `src/slices/`. Every error is an RFC 9457 problem document: `raise Problem.domain_rejection(result.error)` or `raise Problem(status, detail, type=..., ...)` from `http_common`.
- **EIT100** — each slice's agent working set (own source + dependency contract stubs + kernel) must stay under the token budget in `[tool.eitri]`. If you blow it, split the slice or slim the contract; do not raise the budget.

## Adding a slice

1. Copy `src/slices/embeddings/` to `src/slices/<name>/`. Rename the files; one concept = one name everywhere.
2. Put dependencies (slice names only; the kernel is implicit) in `slice.json`.
3. Messages (`Query`/`Command` dataclasses) and result types go in `contract/`; engine, one handler per message, FastAPI router and `module.py` (`register(bus)` + `router`) go in `internal/`.
4. Add the module to `MODULES` in `src/pfa_api/composition.py`.
5. Run `heimdall map --root .` so this file, the slice's `AGENTS.md` and `.heimdall/map.json` learn the new seam. Then write a few hand-written lines under the generated ones in the slice's `AGENTS.md`: what it is for, and its working set.
6. Run `eitri check --root .` and `pytest`.

## CQRS in one paragraph

A slice's contract is its **messages** (frozen `Query`/`Command` dataclasses in `contract/queries.py` / `commands.py`) and **result types** (`contract/results.py`). Each message has exactly one handler in `internal/handlers.py`, registered on the kernel `Bus` by `module.py`. Routes and other slices never call handlers; they `bus.dispatch(message)` and get a `Result`. A route turns `Result.ok=False` into `Problem.domain_rejection(result.error)` — a 422 problem document (RFC 9457), never a bare `HTTPException`. New use case = message + result + handler + one `bus.register` line (+ a route if it has an HTTP edge). Tests replace a handler with `bus.register(..., replace=True)`.

## Changing a contract

A contract with high fan-in is **frozen**: additive changes only. Breaking changes need an expand-contract fan-out (add the new member, migrate every consumer, remove the old one). Heimdall warns in-loop when you edit a contract whose fan-in is 10 or more.

## Commands

```bash
pip install -e ".[dev]" -e ./tools   # once, in a dev environment
ruff check . && ruff format --check . && mypy   # lint, format, types — CI runs these first; `ruff check --fix . && ruff format .` repairs most of it
pytest                               # tests/api + tests/tooling (warnings are errors)
eitri check --root .                 # the walls + the budget — must be green
eitri surface <slice>                # what a consumer of <slice> actually loads
heimdall map --root .                # regenerate the maps after changing slices, slice.json or [tool.heimdall] shared
heimdall drift                       # how far recent agent sessions wandered from the map
heimdall review --base origin/main   # Jev judges the diff (needs TYPESAFE_API_KEY); what the PR check runs
python -m pfa_api                    # run the service
```

## What Heimdall will do while you work

Every Read/Grep/Glob/Edit/Write you make is classified against the slice map and logged to `.heimdall/telemetry.jsonl`. You will be interrupted (hook exit 2, message on stderr) in exactly two cases: editing a second slice in one session, and editing a frozen high fan-in contract. Reads are never interrupted, only recorded; `heimdall drift` shows afterwards whether a session's reads stayed inside the architecture's promise. Sustained out-of-bounds reads above 20% on a slice mean the seam, not the agent, is wrong.

Before you open a PR, `heimdall review --base origin/main --task "<what you set out to do>"` runs the same Jev judgment the PR check will: correctness, clean code, tests covering the change, scope, the walls, Problem Details on error paths. `request_changes` fails the PR check; `escalate` asks for a human. Fix what it names rather than arguing with the probability.
