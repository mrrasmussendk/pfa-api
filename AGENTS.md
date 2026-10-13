# Agents: how to work in this repo

A task lives in one feature. This file says which guide to read for it; the guide says what to load and how to do it. Explanations live in [docs/](docs/), not here.

| ✅ Do | ❌ Don't |
|---|---|
| Read the guide for the task before loading code, and work within the owning feature. | Open another feature's `internal/` to get around its contract. |
| Use another feature through its `contract/`, declared in `feature.json`, dispatched on the bus. | Call another feature's handlers or engines directly. |
| Keep features and the kernel framework-free; HTTP lives in `src/pfa/api/routes/<feature>.py`. | Import FastAPI, pydantic, `pfa.api` or `pfa.application` inside a feature. |
| Raise `Problem` from `pfa.api.problems` on an error path. | Raise `HTTPException` anywhere. |
| Run the checks and fix what they find. | Disable a rule or raise the token budget to make a change pass. |

## Which guide

| Task | Guide |
|---|---|
| Fix a bug, or change what existing code does | [Changing behaviour](harness/guides/changing-behaviour.md) |
| Add an endpoint to an existing feature | [Adding a route](harness/guides/adding-a-route.md) |
| Add a new capability with its own data and rules | [Adding a feature](harness/guides/adding-a-feature.md) |
| Change what a feature's contract offers | [Changing a contract](harness/guides/changing-a-contract.md) |
| Return an error from a route | [Returning errors](harness/guides/returning-errors.md) |
| Add a setting (an environment variable) | [Adding a setting](harness/guides/adding-a-setting.md) |
| Log something | [Logging](harness/guides/logging.md) |
| Write or change tests | [Writing tests](harness/guides/writing-tests.md) |
| Write or fix an end-to-end scenario | [End-to-end scenarios](harness/guides/e2e-scenarios.md) |
| Act on a hook's feedback, or finish a change | [What the hooks tell you](harness/guides/hook-feedback.md) |
| Prepare a PR, act on review findings | [PR review](harness/guides/pr-review.md) |
| Fix an `EIT…` finding | its page in [harness/rules/](harness/rules/) |

## The working set

Load the feature's `AGENTS.md`, its `contract/`, `internal/` and `module.py`, its routes module when the task has an HTTP edge, the `contract/` of each declared dependency, and `src/pfa/kernel/`. Nothing else.

⚠️ If the task seems to need another feature's `internal/`, say which capability its contract is missing. ❌ Don't read that implementation.

## Before you finish

```bash
ruff check . && ruff format --check . && mypy && pytest && eitri check --root . --base origin/main
```

Commit, then stop: the `Stop` hook runs the tests under coverage, holds the change to its promises (changed lines tested, contracts additive, the HTTP API compatible), runs the end-to-end scenarios the change reaches, and Jev's review of the committed diff. When a route's shape changes, regenerate `docs/openapi.json` with `python tools/openapi_compat.py --write`.

<API_TASKS>
Before any non-trivial API engineering task (designing, implementing, mocking, testing, monitoring, documenting or deploying an API or Postman Flow), read `postman/skills/api-engineer/SKILL.md` and follow it.
</API_TASKS>

## Feature map

<!--heimdall:map-->
features: `src/pfa/features` · kernel: `src/pfa/kernel` · routes: `src/pfa/api/routes/<feature>.py` · budget: 15000 tokens per feature · regenerate with `heimdall map --root .`

| feature | depends on | fan-in | budget used | contract | routes |
|---|---|---|---|---|---|
| chunking | (none) | 1 | ≈20% | `src/pfa/features/chunking/contract/` | `POST /chunking/split` |
| embeddings | chunking | 0 | ≈30% | `src/pfa/features/embeddings/contract/` | `POST /embeddings/query`<br>`POST /embeddings/passages`<br>`POST /embeddings/similarity` |
<!--/heimdall:map-->
