# Adding a feature

✅ **Do** marks the expected approach. ❌ **Don't** marks a prohibited shortcut. ⚠️ **Check** marks a condition to resolve before continuing.

| ✅ Do | ❌ Don't |
|---|---|
| Start from a copy of an existing feature and rename every file to the one concept it holds. | Grow an existing feature until it holds two capabilities. |
| Declare every feature you consume in `feature.json` and import only its `contract/`. | Import another feature's `internal/` or `module.py`. |
| Keep the feature framework-free; its HTTP edge goes in `src/pfa/api/routes/<name>.py`. | Put FastAPI, pydantic or a status code under `src/pfa/features/`. |

A feature is a capability with its own data and rules, in one folder, used by everyone else only through its `contract/`. Add one when the change is a new capability, not a new endpoint on an existing one; for that, follow [Adding a route](adding-a-route.md).

## Steps

1. Copy `src/pfa/features/embeddings/` to `src/pfa/features/<name>/`. Rename the files; one concept is one name everywhere.
2. Put the features it depends on (names only; the kernel is implicit) in `feature.json`.
3. Write the contract and the implementation:
   - `contract/`: the messages (`Query`/`Command` dataclasses), their result types, and any infrastructure exception the edge must map.
   - `internal/`: the engine, and one handler per message.
   - `module.py` at the feature root: `register(bus)`, plus `ready()` and `warmup()`.
4. Add the module to `FEATURES` in `src/pfa/application.py`.
5. Write the HTTP edge in `src/pfa/api/routes/<name>.py`: pydantic models, one `APIRouter(prefix="/<name>")`, dispatch on the bus, `Problem` on error. Add its router to `ROUTERS` in `src/pfa/api/routes/__init__.py`.
6. Run `heimdall map --root .`, so the root `AGENTS.md`, the feature's `AGENTS.md` and `.heimdall/map.json` learn the new feature. Under the generated lines in the feature's `AGENTS.md`, write what it is for and its working set, in a few lines.
7. Write its first scenario with `heimdall e2e new <name>/<scenario> ...` ([End-to-end scenarios](e2e-scenarios.md)).
8. Run `eitri check --root .` and `pytest`.

⚠️ **Check:** `eitri check` counts the feature's own source, its dependencies' contracts and the kernel against the token budget (EIT100). A feature that does not fit is two features.

How a feature exposes capability (messages, one handler each, the bus) is explained in [docs/architecture.md](../../docs/architecture.md#cqrs--how-a-feature-exposes-capability).
