# Adding a setting

✅ **Do** marks the expected approach. ❌ **Don't** marks a prohibited shortcut. ⚠️ **Check** marks a condition to resolve before continuing.

| ✅ Do | ❌ Don't |
|---|---|
| Read the variable where it is used: a feature's engine reads its own, the entry point reads the server's. | Add a settings object to the kernel or to a contract; every feature would pay for it. |
| Give the constructor a parameter that overrides the variable, with the default in a `DEFAULT_*` constant. | Make tests set environment variables to reach a value. |
| Read it when the component is built, lazily. | Read it at import time, or in `create_app()` for something only a feature uses. |
| Name it `PFA_<AREA>_<WHAT>` and document it in [the service reference](../../docs/service.md#settings). | Add an undocumented variable, or reuse one for a second meaning. |

A setting is a variable an operator turns, not something a client sends. It belongs to the component it changes. The embeddings engine is the model in this repo:

```python
# src/pfa/features/embeddings/internal/e5_engine.py
DEFAULT_CONCURRENCY = 1

def __init__(self, ..., concurrency: int | None = None) -> None:
    self._concurrency = max(1, concurrency or int(os.environ.get("PFA_EMBED_CONCURRENCY", DEFAULT_CONCURRENCY)))
```

The constructor parameter is what tests and other callers use; the variable is what the operator uses. Both meet in one line.

## Where it goes

| The setting changes | Read it in | Document it in |
|---|---|---|
| How a feature does its work (model, limits, concurrency) | that feature's `internal/` engine | `docs/service.md`, the model table or *Settings* |
| How the server runs (bind address, workers, proxies) | `src/pfa/api/__main__.py` | `docs/service.md`, *Settings* |
| How the service logs | `src/pfa/api/logs.py` | `docs/service.md`, *Settings*, and see [Logging](logging.md) |

⚠️ **Check:** when the setting has a different production value, set it in the `Dockerfile` `ENV` block and say so in the table's default column ("off (on in the image)").

⚠️ **Check:** a value that cannot be parsed should fail when the component is built, with the variable's name in the message, not on the first request. Test the default and one override through the constructor parameter.
