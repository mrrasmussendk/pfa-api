# Changing a contract

✅ **Do** marks the expected approach. ❌ **Don't** marks a prohibited shortcut. ⚠️ **Check** marks a condition to resolve before continuing.

| ✅ Do | ❌ Don't |
|---|---|
| Add to a contract: a new message, a new result field with a default. | Change or remove a member while a consumer still relies on it. |
| Make a breaking change in steps: add the replacement, migrate every consumer, then remove the old member. | Break the contract and fix the consumers in the same breath. |
| Add the capability you need to the owning feature's contract, as a new message. | Reach into its `internal/` because the contract lacks it. |

A contract is everything other features may rely on, so a change to it is a change to every consumer. The fan-in column of the feature map in the root [AGENTS.md](../../AGENTS.md) says how many features that is.

⚠️ **Check:** `eitri check --root . --base origin/main` fails ([EIT008](../rules/EIT008.md)) when a member is removed or changed while anything outside the feature still used it on `main`. Additions are free: a new message, a new name, a field or trailing parameter with a default.

⚠️ **Check:** a contract with fan-in **10 or more** is frozen. Heimdall warns when you edit it; plan the expand-contract migration before you continue.

⚠️ **Check:** contracts are paid for by every consumer's token budget (EIT100). Keep them to messages, result types and the exceptions the edge must map; the rule is [EIT003](../rules/EIT003.md).
