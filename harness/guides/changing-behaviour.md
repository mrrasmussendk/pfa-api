# Changing behaviour or fixing a bug

✅ **Do** marks the expected approach. ❌ **Don't** marks a prohibited shortcut. ⚠️ **Check** marks a condition to resolve before continuing.

| ✅ Do | ❌ Don't |
|---|---|
| Reproduce first: a unit test that fails for the reason in the report, and the scenario that should have caught it. | Change the code first and write a test that agrees with whatever it now does. |
| Change the scenario's text and its test together when the promise itself changes. | Edit a `pm.test` name or a `Then` clause on one side only. |
| Keep the change inside the feature that owns the behaviour, behind its contract. | Fix a symptom in a consumer, or reach into another feature's `internal/`. |
| Keep the HTTP shape and the contract compatible, or change them in steps. | Remove a field, a member or a route in the same change that stops using it. |

Most work is not a new route or a new feature: something an existing route does is wrong, or should now do something else. The difference between a fix and a change of behaviour is whether the promise moves. A fix makes the code keep a promise it already states. A change states a new promise first.

## Steps

1. **Find the owner.** The route in `src/pfa/api/routes/<feature>.py` names the contract message it dispatches; the handler for it is in that feature's `internal/`. Load the working set the root [AGENTS.md](../../AGENTS.md) names for that feature, nothing more.
2. **Read the promise.** The feature's `AGENTS.md` lists its scenarios. Find the one that covers the behaviour in `tests/e2e/<feature>.feature`.
   - A **bug**: the scenario says the right thing and the code breaks it, or no scenario covers the case. Add the missing scenario with `heimdall e2e new` if so.
   - A **change**: edit the scenario's `Then` (and its test's `then:` names) to the new promise.
3. **Write the failing unit test** next to the handler's other tests, named as the sentence it proves. Watch it fail for the reason you expect. See [Writing tests](writing-tests.md).
4. **Change the code** inside the owning feature until the test passes, and no further.
5. **Check what you touched against `main`:**
   - Changed the contract? Additions are free; a removal or a changed signature fails [EIT008](../rules/EIT008.md) while anything still uses it. Follow [Changing a contract](changing-a-contract.md).
   - Changed a request or response model? Regenerate the spec with `python tools/openapi_compat.py --write`. A breaking change fails the Stop hook unless the major version says so.
6. **Commit and stop.** The Stop hook runs the suite under coverage, the checks above, the scenarios the change reaches, and the review.

⚠️ **Check:** at least 90% of the application lines you changed must run under a test (`tools/diff_coverage.py`). A fix without a test that fails before it is not demonstrated.

⚠️ **Check:** when the cause turns out to live in another feature, stop and say which capability its contract is missing. Don't patch around it from your side.
