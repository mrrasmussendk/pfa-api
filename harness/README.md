# Agent harness instructions

[Project overview](../README.md) · [Harness explanation](../docs/tooling.md#heimdall--the-harness)

This directory contains the instructions for making and checking a change. It is the practical companion to the [reference documentation](../docs/README.md): guides describe what to do, while reference pages explain the design and trade-offs.

## Start a task

1. Read the root [AGENTS.md](../AGENTS.md) to identify the task's owning feature and required guide.
2. Read that feature's `AGENTS.md`, then load its allowed working set: the feature, its declared dependencies' contracts, the kernel, and its routes module when changing HTTP behaviour.
3. Follow the guide for the task. Read a rule page when that rule applies or a check reports it.
4. Run the required checks and follow [the completion guide](guides/hook-feedback.md) before committing and finishing.

## Choose a guide

| Task | Guide |
|---|---|
| Fix a bug or change existing behaviour | [Changing behaviour](guides/changing-behaviour.md) |
| Add an endpoint to an existing feature | [Adding a route](guides/adding-a-route.md) |
| Add a capability with its own data and rules | [Adding a feature](guides/adding-a-feature.md) |
| Change a feature's public contract | [Changing a contract](guides/changing-a-contract.md) |
| Return an API error | [Returning errors](guides/returning-errors.md) |
| Add an environment setting | [Adding a setting](guides/adding-a-setting.md) |
| Add or change logging | [Logging](guides/logging.md) |
| Write or change tests | [Writing tests](guides/writing-tests.md) |
| Write or fix an end-to-end scenario | [End-to-end scenarios](guides/e2e-scenarios.md) |
| Respond to hook feedback or finish a change | [What the hooks tell you](guides/hook-feedback.md) |
| Prepare a PR or act on review findings | [PR review](guides/pr-review.md) |
| Resolve an `EIT…` architecture finding | [Rule guidance](rules/) |

## Find the supporting files

| Location | Purpose |
|---|---|
| [Root AGENTS.md](../AGENTS.md) and each feature's `AGENTS.md` | Entry points, working sets, and generated feature summaries |
| [guides/](guides/) | Task-specific steps and completion requirements |
| [rules/](rules/) | Explanations and fixes for Eitri diagnostics |
| [e2e/system.json](e2e/system.json) | How Heimdall starts the service, waits for readiness, and selects a scenario runner |
| [tests/e2e/](../tests/e2e/) | Behaviour scenarios, one Gherkin file per feature |
| [postman/collections/e2e/](../postman/collections/e2e/) | Requests and assertions that implement the API scenarios |
| [tools/](../tools/) | Executable checks, observation, and review tooling |
| `.heimdall/` | Generated map, local telemetry, and run state |

The configured Claude Code hooks use this material during a task. The command-line checks also run explicitly in other environments; see [development commands](../README.md#development) and [running the harness](../README.md#run-the-harness).

## Maintain these instructions

Keep guides concrete: identify the working set, required actions, prohibited shortcuts, and completion checks. Link to [docs/](../docs/README.md) for longer explanations, and load only the guides relevant to the current task.

**Preserve each rule's `**Fix:**` marker.** Eitri diagnostics link to these rule pages, and Heimdall reads that line when preparing review feedback. Root and feature `AGENTS.md` files must also remain at their discovery locations.
