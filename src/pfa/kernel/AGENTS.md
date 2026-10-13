# kernel

The primitives every feature shares: `Result` (`primitives.py`) and the messaging types `Message`, `Query`, `Command` and `Bus` (`messaging.py`). Nothing else.

| ✅ Do | ❌ Don't |
|---|---|
| Add to the kernel only what every feature needs to talk to the others. | Put a helper here because two features happen to want it. |
| Keep it standard library only: no framework, no model, nothing from `pfa.features` or `pfa.api`. | Import a feature, the entry point, or a third-party package. |
| Change it additively; every feature and every route depends on it. | Rename or remove a public name without migrating every caller in the same plan. |

- **What it costs.** Every feature's working set includes the kernel's public surface (`eitri surface pfa.kernel` prints it), so each public name added here is paid by every feature's token budget ([EIT100](../../../harness/rules/EIT100.md)). The Heimdall hook says so on a session's first kernel edit.
- **Where else it could go.** Something one feature needs belongs in that feature's `internal/`. Something one feature offers others belongs in its `contract/`. Something about HTTP belongs in `src/pfa/api/`.
- **Working set:** this folder, and the callers the change touches.
