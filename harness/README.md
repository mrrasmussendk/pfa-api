# Agent harness instructions

This directory contains the instructions an agent uses to perform and correct a change. Explanations of the service, architecture, and harness belong in [docs/](../docs/).

Start with the root [AGENTS.md](../AGENTS.md), then the owning slice's `AGENTS.md`. Load only the guide for the current task; read a rule page when that rule is relevant or a check reports it.

| Task | Feedforward instructions |
|---|---|
| Add an endpoint | [Adding a route](guides/adding-a-route.md) |
| Return an API error | [Returning errors](guides/returning-errors.md) |
| Write or change tests | [Writing tests](guides/writing-tests.md) |
| Prepare a PR and act on review findings | [PR review with Jev](guides/pr-review.md) |
| Resolve an architecture violation | [Eitri rule guidance](rules/) |

✅ Keep instructions concrete: working set, required actions, prohibited shortcuts, and completion checks.

❌ Do not copy reference chapters here or load every guide for every task. Link to explanations when needed.

⚠️ Eitri diagnostics link to `rules/`, and Heimdall reads each rule's `**Fix:**` line for review feedback. Preserve that marker when editing rule guidance.

The executable enforcement and observation tools remain in [tools/](../tools/). Generated maps and per-checkout telemetry remain in `.heimdall/`. Root and slice `AGENTS.md` files stay at their discovery locations.
