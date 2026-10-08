# Agents: how to work in this repo

Read this before touching code. It is the feedforward half of the harness: the map of what exists and the rules for moving inside it. The feedback half (Heimdall's hooks and `eitri check`) will tell you when you drift — but it is cheaper to not drift.

**Reading key:** ✅ **Do** — the expected action. ❌ **Don't** — a prohibited shortcut. ⚠️ **Check** — a condition that needs attention. These markers describe instructions, not completed checks.

| ✅ Do | ❌ Don't |
|---|---|
| Read the relevant guide and work within the owning feature. | Open another feature's implementation to bypass its contract. |
| Declare dependencies and dispatch contract messages through the bus. | Call another feature's handlers or engines directly. |
| Keep features framework-free and the kernel small. | Put FastAPI, pydantic, or application wiring in a feature or the kernel. |
| Put a feature's HTTP edge in `src/pfa/api/routes/<feature>.py` and raise `Problem` there. | Raise `HTTPException` anywhere, or import the application or FastAPI inside a feature. |
| Run the checks and fix the findings. | Disable rules or raise the token budget to make a change pass. |

## What this repo is

`src/pfa/` is **the application**. The tree reads top-down:

```
src/pfa/
  application.py     builds the application: every feature registered on one bus. No HTTP.
  features/<name>/   one feature each — messages in, Result out, no framework
    contract/        its public surface: messages, result types, infrastructure errors
    internal/        its implementation: engine, handlers
    module.py        its plug: register(bus), ready(), warmup()
    feature.json     which other features it may use
  kernel/            Result, Bus, Query, Command — the primitives every feature shares
  api/               the main entry point: HTTP
    app.py           create_app(): wraps the application in FastAPI
    routes/<name>.py one module per feature: pydantic models, APIRouter, Problem on error
    problems.py      RFC 9457 problem details
    validation.py    request hygiene
```

A feature is built as a **vertical slice**: everything it is, in one folder, consumed by everyone else only through its `contract/`. The tooling still says "slice" in its diagnostics; it means a feature folder.

- `tools/` is **development-only tooling** (Eitri, Heimdall, Brokkr). It is never deployed and never imported by `src/`.
- `tests/api` tests the application; `tests/tooling` tests the tooling.

## The dependency direction

✅ Follow arrows from the importing package to its dependency. ❌ Never import upwards: a feature never imports the application, the API, or another feature's `internal/`.

![Allowed imports: arrows point from importer to dependency](docs/diagrams/dependencies.svg)

[Mermaid source](docs/diagrams/dependencies.mmd)

- **The application owns the features.** `pfa/application.py` is the one module that imports a feature's `module.py`. It knows every feature; no feature knows it.
- **The API is one door into the application.** `pfa/api` imports `pfa.application` and the features' `contract/` packages — its routes dispatch messages exactly as another feature would. It never imports `internal/` or `module.py`. Nothing imports `pfa.api`. A second entry point (a CLI, a worker) would sit beside it and call the same `build_bus()`.
- **A feature has two public doors and one private room.** `contract/` is for every consumer. `module.py` is for `application.py` only. `internal/` is for the feature alone.
- **`pfa.kernel` is paid for by every feature** (EIT100) and importable from every contract (EIT003). Only tiny, framework-free, stable primitives belong there. When in doubt, it does not go in the kernel.
- **FastAPI and pydantic live in `pfa/api` and nowhere else** (EIT006). Shared HTTP-edge helpers go in `pfa/api/problems.py` or `pfa/api/validation.py` — never in a feature, never in the kernel.

## Feature map

<!--heimdall:map-->
features: `src/pfa/features` · kernel: `src/pfa/kernel` · routes: `src/pfa/api/routes/<feature>.py` · budget: 15000 tokens per feature · regenerate with `heimdall map --root .`

| feature | depends on | fan-in | contract | routes |
|---|---|---|---|---|
| chunking | (none) | 1 | `src/pfa/features/chunking/contract/` | `POST /chunking/split` |
| embeddings | chunking | 0 | `src/pfa/features/embeddings/contract/` | `POST /embeddings/query`<br>`POST /embeddings/passages`<br>`POST /embeddings/similarity` |
<!--/heimdall:map-->

Each feature's own `AGENTS.md` (in `src/pfa/features/<name>/`) carries its generated `depends on:` and `routes:` lines plus a few hand-written notes on what the feature is for.

✅ Regenerate the table and marked lines from `feature.json` and `src/pfa/api/routes/<feature>.py` with `heimdall map --root .`. ❌ Do not edit generated content by hand. Hand-written guidance outside the markers remains editable.

## Guides

Task guides and rule instructions live in [harness/](harness/). Explanatory reference material lives in [docs/](docs/). This file and the feature-level `AGENTS.md` files remain the feedforward entry points.

Read the guide for the kind of change before loading any code; each one names the exact working set.

- **Adding a route** (new endpoint for an existing feature, with or without a contract change): [Adding a route](harness/guides/adding-a-route.md).
- **Returning an error** (what a route raises, what the client receives, how to add a problem type): [Returning errors](harness/guides/returning-errors.md).
- **What the PR check judges** (Jev's typed questions, the policy, how to run it locally): [PR review with Jev](harness/guides/pr-review.md).
- **Adding a feature** (a new capability with its own data and rules): [Adding a feature](#adding-a-feature).
- **Changing a contract**: [Changing a contract](#changing-a-contract), and the fan-in column in the feature map.

## The working set for a task

A task lives in **one feature**. Load, in this order, and nothing more:

1. `src/pfa/features/<feature>/AGENTS.md` — the feature's declared dependencies.
2. `src/pfa/features/<feature>/contract/` — its public surface.
3. `src/pfa/features/<feature>/internal/` and `module.py` — its implementation and its plug into the application.
4. `src/pfa/api/routes/<feature>.py` — its HTTP edge, only when the task has one. Heimdall counts edits there as edits to the feature.
5. `src/pfa/features/<dep>/contract/` for each declared dependency — **only the contract**, never `internal/`.
6. `src/pfa/kernel/` — the primitives every feature shares.
7. `src/pfa/api/problems.py` and `src/pfa/api/validation.py` — only when the task touches a route's error path or request validation. Reads anywhere in `src/pfa/` outside the features are never drift.

⚠️ If the task appears to require another feature's `internal/`, explain the missing contract capability before proceeding. ❌ Do not expand the working set by reading that implementation.

## The walls (Eitri enforces these; `eitri check --root .` must pass before you finish)

| Rule | ✅ Do | ❌ Don't |
|---|---|---|
| **EIT001 — boundaries** | Consume another feature through its `contract/`. Only `pfa/application.py` imports a feature's `module.py`. | Import `pfa.features.<other>.internal...` or `.module` from a feature, or `.internal...` from anywhere in the application. |
| **EIT002 — visible coupling** | Use explicit, searchable imports inside `src/pfa/features/`. | Use `importlib.import_module`, `__import__`, `sys.path` edits, `sys.modules` writes, or `from x import *`. |
| **EIT003 — pure contracts** | Import only stdlib, `pfa.kernel`, and the feature's own contract in `contract/`. | Import frameworks or implementation code into a contract. |
| **EIT004 — declared dependencies** | Add `"x"` to `feature.json` before importing `pfa.features.x.contract`. | Introduce an undeclared feature dependency. |
| **EIT005 — error shape** | In `pfa/api`, raise `Problem.domain_rejection(result.error)` or `Problem(status, detail, type=..., ...)` from `pfa.api.problems`. | Raise FastAPI or Starlette `HTTPException`. |
| **EIT006 — features depend downward** | Keep routes, request models and status codes in `src/pfa/api/routes/<feature>.py`. Signal an infrastructure failure with a plain exception declared in `contract/`. | Import `fastapi`, `starlette`, `pfa.application` or `pfa.api` anywhere under `src/pfa/features/`. |
| **EIT100 — context budget** | Keep own source + dependency contract stubs + kernel within `[tool.eitri]`'s budget. Split the feature or slim its contract when needed. | Raise the budget to accommodate an oversized feature. |

## Adding a feature

1. Copy `src/pfa/features/embeddings/` to `src/pfa/features/<name>/`. Rename the files; one concept = one name everywhere.
2. Put dependencies (feature names only; the kernel is implicit) in `feature.json`.
3. Messages (`Query`/`Command` dataclasses), result types and any infrastructure exception the edge must map go in `contract/`; engine and one handler per message go in `internal/`; `module.py` at the feature root does `register(bus)` plus `ready()` / `warmup()`.
4. Add the module to `FEATURES` in `src/pfa/application.py`.
5. Write the HTTP edge in `src/pfa/api/routes/<name>.py` — pydantic models, one `APIRouter(prefix="/<name>")`, dispatch on the bus, `Problem` on error — and add its router to `ROUTERS` in `src/pfa/api/routes/__init__.py`.
6. Run `heimdall map --root .` so this file, the feature's `AGENTS.md` and `.heimdall/map.json` learn the new seam. Then write a few hand-written lines under the generated ones in the feature's `AGENTS.md`: what it is for, and its working set.
7. Run `eitri check --root .` and `pytest`.

## CQRS in one paragraph

A feature's contract is its **messages** (frozen `Query`/`Command` dataclasses in `contract/queries.py` / `commands.py`) and **result types** (`contract/results.py`). Each message has exactly one handler in `internal/handlers.py`, registered on the kernel `Bus` by the feature's `module.py`. Nothing calls a handler: routes in `pfa/api/routes/<feature>.py` and handlers in other features `bus.dispatch(message)` and get a `Result`. A route turns `Result.ok=False` into `Problem.domain_rejection(result.error)` — a 422 problem document (RFC 9457), never a bare `HTTPException`. New use case = message + result + handler + one `bus.register` line (+ a route in `pfa/api` if it has an HTTP edge). Tests replace a handler with `bus.register(..., replace=True)`.

## Changing a contract

⚠️ A contract with fan-in **10 or more** is frozen; Heimdall warns when you edit it.

- ✅ Make additive changes. For a breaking change, add the replacement, migrate every consumer, then remove the old member in a coordinated expand-contract migration.
- ❌ Do not change or remove an existing member while consumers still rely on it.

## Commands

```bash
pip install -e ".[dev]" -e ./tools   # once, in a dev environment
ruff check . && ruff format --check . && mypy   # lint, format, types — CI runs these first; `ruff check --fix . && ruff format .` repairs most of it
pytest                               # tests/api + tests/tooling (warnings are errors)
eitri check --root .                 # the walls + the budget — must be green
eitri surface <feature>              # what a consumer of <feature> actually loads
heimdall map --root .                # regenerate the maps after changing features, feature.json, routes or [tool.heimdall]
heimdall drift                       # how far recent agent sessions wandered from the map
heimdall review --base origin/main   # Jev judges the diff (needs TYPESAFE_API_KEY); what the PR check runs
python -m pfa.api                    # run the service
```

## What Heimdall will do while you work

With the Claude Code hooks configured and `.heimdall/map.json` present, supported Read/Grep/Glob/Edit/Write events are classified against the map and logged to `.heimdall/telemetry.jsonl`. A feature's route module in `pfa/api/routes/` counts as the feature; everything else in `src/pfa/` outside the features is always in-bounds.

- ⚠️ **Immediate feedback:** hook exit 2 flags edits to a second feature in one session, to a frozen high fan-in contract, or that leave a function over the shape limit (40 lines or 5 parameters). Check the dependency boundary or migration plan, or split the function, before continuing; the shape feedback must be answered, not raised.
- ✅ **After work:** use `heimdall drift` to inspect recorded reads. Reads are never interrupted; sustained out-of-bounds reads above 20% suggest the feature boundary needs investigation.
- ❌ **Do not assume complete coverage:** shell-based reads are invisible to the hook, and a missing map disables observation. Eitri remains the enforcement check.

Before you open a PR, `heimdall review --base origin/main --task "<what you set out to do>"` runs the same Jev judgment the PR check will: correctness, clean code (readability, single purpose, lean signatures, asked about the functions the hook already flagged), tests covering the change, scope, the walls, Problem Details on error paths. `request_changes` fails the PR check; `escalate` asks for a human. Readability below 2.5 and clean code below 2.8 (on 0–3) fail it too: the comment names what the judge found limiting and which function to start with. Fix what it names rather than arguing with the probability.
