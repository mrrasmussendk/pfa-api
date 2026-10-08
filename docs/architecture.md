# Architecture

[Back to README](../README.md)

- [New to this architecture? Start here](#new-to-this-architecture-start-here)
- [The dependency direction](#the-dependency-direction)
- [Inside a slice](#inside-a-slice)
- [CQRS — how a slice exposes capability](#cqrs--how-a-slice-exposes-capability)
- [Chunking — the model's 512-token window](#chunking--the-models-512-token-window)

## New to this architecture? Start here

If you have built APIs before, you have probably built them in **layers**: a `routes/` folder, a `services/` folder, a `models/` folder. Every feature is smeared across all of them, and every folder contains every *other* feature too.

This repo is organised the other way round: **vertical slices**, one folder per feature, containing everything that feature needs from the HTTP endpoint down to the logic. To understand "embeddings" you open `src/slices/embeddings/` and you are done.

### The vocabulary, in the order you will meet it

| Word | What it means here | Where it lives |
|---|---|---|
| **Slice** | One feature, as one Python package. It owns its endpoint, its logic, and its tests. | `src/slices/<name>/` |
| **Contract** | The *only* part of a slice other code may import: the messages it answers and the results it returns. Plain dataclasses, no framework. | `src/slices/<name>/contract/` |
| **Internal** | Everything else in the slice: implementation, FastAPI router, wiring. Private by rule, even though Python cannot enforce it. | `src/slices/<name>/internal/` |
| **Manifest** | A one-line JSON file declaring which other slices this slice may use. | `src/slices/<name>/slice.json` |
| **Kernel** | The tiny set of primitives every slice shares: `Result`, `Bus`, `Query`, `Command`. Nothing else goes here. | `src/shared_kernel/` |
| **HTTP-edge helpers** | FastAPI-aware code more than one slice needs — today the RFC 9457 `Problem`. Not the kernel: contracts never import it. | `src/http_common/` |
| **Composition root** | The one file that knows every slice. It builds the bus, lets each slice register its handlers, mounts each router, and fans out readiness and warm-up. | `src/pfa_api/composition.py` |
| **Message** | A frozen dataclass naming one thing you can ask a slice to do. A `Query` reads or computes; a `Command` changes state. | `contract/queries.py` (and `commands.py` once a slice changes state — none does yet) |
| **Handler** | The one function that serves one message. Takes the message, returns a `Result`. | `internal/handlers.py` |
| **Bus** | The dispatcher. Give it a message; it finds the handler and returns the `Result`. Callers never see handlers. | `shared_kernel/messaging.py`, built in `composition.py` |
| **Result** | A domain outcome: `ok`, `value`, `error`. Handlers return it instead of raising; the route turns it into a status code. | `shared_kernel/primitives.py` |

### One request, end to end

Follow `POST /embeddings/similarity` through the code and you have seen the whole pattern:

1. **The route** (`slices/embeddings/internal/routes.py`) validates the JSON body with a pydantic model, builds a message, `RankCandidates(question=..., candidates=...)`, and calls `request.app.state.bus.dispatch(message)`.
2. **The bus** (`shared_kernel/messaging.py`) looks up the one handler registered for `RankCandidates` and calls it.
3. **The handler** (`slices/embeddings/internal/handlers.py`) does the work. It needs text split to the model's window, so it dispatches *another* message, `SplitText`, which belongs to the `chunking` slice's contract. It never imports chunking's internals; it only knows the message.
4. **Chunking's handler** splits the text and returns a `Result[Chunks]`. Embeddings' handler encodes the chunks with the model — behind the inference gate — ranks them, and returns a `Result[Ranking]`.
5. **Back in the route**, `result.ok` decides the status: a domain rejection such as an empty question becomes a `422` problem document via `Problem.domain_rejection(result.error)`; success becomes the response model.

Notice what did *not* happen: no route imported a service class, no slice imported another slice's implementation, nothing in `contract/` touched FastAPI, no handler raised for a domain reason, and the composition root never ran any logic.

### Why it is built this way

- **A feature fits in one folder, so it fits in one head.** Whoever works on embeddings — human or AI — loads the embeddings folder, chunking's contract, and the kernel. That is the whole working set, and the EIT100 budget check keeps it that way as the project grows.
- **Contracts are data, so coupling is visible.** Because slices talk through message types, `grep SplitText` finds every place chunking is used. There is no hidden coupling through base classes, dependency-injection magic, or dynamic imports (EIT002 bans those on purpose).
- **The manifest is the seam.** `embeddings/slice.json` says `["chunking"]`. Import a third slice without declaring it and `eitri check` fails (EIT004). The dependency graph in the `AGENTS.md` files is generated from these manifests, so it cannot lie.
- **Rules fail the check, they do not warn.** Python will let you import anything. `eitri check` is what says no, and `tools/brokkr_canary.py` proves it still says no after every change.

### How do I…

- **…add an endpoint to an existing feature?** [guides/adding-a-route.md](../harness/guides/adding-a-route.md). Short version: pydantic models and a route in the slice's `internal/routes.py`, a message in its `contract/` if the capability is new, a handler, one `bus.register` line in `module.py`, a test.
- **…return an error?** [guides/returning-errors.md](../harness/guides/returning-errors.md). `raise Problem.domain_rejection(result.error)` for a handler's `Result.ok=False`; `Problem(status, detail, type=…)` for anything else. Never `HTTPException` inside a slice.
- **…add a feature?** Copy `src/slices/embeddings/` as described in [AGENTS.md](../AGENTS.md), rename, write `slice.json`, add the module to `MODULES` in `composition.py`, run `heimdall map --root .`. The root `AGENTS.md` has the numbered steps.
- **…use another slice's capability?** Declare it in `slice.json`, import its `contract`, dispatch its message on the bus. Never import its `internal/`. If the capability you need is not in its contract, add it *there*, as a new message.
- **…share code between slices?** First ask whether you should. A framework-free primitive every slice needs goes in the kernel, and every slice pays for it in its budget. FastAPI or pydantic glue goes in `src/http_common/`, imported only from `internal/`. Logic is probably a slice.
- **…test without the 2 GB model?** Register a fake handler on the bus with `replace=True`. `tests/api/test_embeddings.py` does exactly this: real handlers, fake engine, real seam between slices.
- **…know whether I broke something?** The [development checks](../README.md#development): linters, tests, walls. CI runs the same, plus Brokkr and the Jev review.

### The two mistakes everyone makes at first

1. **Putting shared things in the kernel.** The kernel is importable from every contract and counted in every slice's budget. It must stay tiny. FastAPI-aware helpers go in `http_common`, not the kernel.
2. **Importing `internal/` because it is right there.** Python lets you. The check does not. A slice whose internals are imported elsewhere can no longer be changed in isolation, which is the one property the whole design exists to protect.

## The dependency direction

Arrows point from the importing package to its allowed dependency. The composition root wires slices together; each slice consumes other slices through their public contracts.

![Allowed imports: arrows point from importer to dependency](diagrams/dependencies.svg)

[Mermaid source](diagrams/dependencies.mmd)

| Package | Knows about | May import | Framework code | Deployed |
|---|---|---|---|---|
| `src/pfa_api` | every slice | `slices.*.internal.module` (only `composition.py`), `http_common`, `shared_kernel`, FastAPI | yes | yes |
| `src/slices/<x>/internal` | its own slice | own `contract`, declared deps' `contract`, `http_common`, `shared_kernel`, FastAPI/pydantic | yes | yes |
| `src/slices/<x>/contract` | its own slice | own `contract`, `shared_kernel`, stdlib | **never** | yes |
| `src/http_common` | HTTP | `shared_kernel`, FastAPI/pydantic, stdlib — **no slice, not `pfa_api`** | yes | yes |
| `src/shared_kernel` | nothing | stdlib | **never** | yes |
| `tools/` | the layout rules | stdlib | no | **never** |

**The API is not in the kernel, and must never be.** The two packages are opposites. The **kernel** is the only package every slice may import, so it is priced into every slice's token budget (EIT100) and importable from every contract (EIT003); anything there is paid for by every feature and leaks into every public surface. The **composition root** imports every slice's `internal/module.py`; in the kernel it would create a cycle and make FastAPI importable from every contract.

**What goes where, when you are unsure:**

- Used by several slices, framework-free, tiny, stable? → `shared_kernel`. Think twice; every slice's budget grows.
- Used by several slices but tied to FastAPI/pydantic? → `http_common`, imported only from `internal/` and `pfa_api`. Eitri allows that in `internal/` and rejects it in `contract/`; `tests/api/test_architecture.py` checks that `http_common` imports no slice.
- Wires slices together, mounts routers, owns the process? → `pfa_api`. Nothing else may import it.
- A new concept with its own data and rules? → a new slice.

## Inside a slice

The embeddings slice lives at `src/slices/embeddings/`:

| Boundary | File | Responsibility |
|---|---|---|
| Configuration | `slice.json` | Declares the dependency on chunking |
| Instructions | `AGENTS.md` | Generated dependencies and routes, plus working notes |
| Public contract | `contract/queries.py` | `EmbedQuery`, `EmbedPassages`, `RankCandidates` |
| Public contract | `contract/results.py` | `Embedding`, `PassageEmbedding`, `Match`, `Ranking` |
| Private implementation | `internal/e5_engine.py` | Lazy loading, model prefixes, and inference concurrency |
| Private implementation | `internal/handlers.py` | One handler per query; dispatches chunking messages |
| HTTP edge | `internal/routes.py` | FastAPI routes, validation, and Problem responses |
| Composition entry point | `internal/module.py` | Registration, router, readiness, and warm-up |

`src/pfa_api/composition.py` is an explicit, greppable list of slice modules. It builds the `Bus`, lets every slice register its handlers, mounts every router, and fans out each module's `ready()` and `warmup()` into `GET /ready` and the startup warm-up thread. Nothing else outside `slices/` imports a slice's `internal/`; `tests/api/test_architecture.py` checks that.

## CQRS — how a slice exposes capability

Every slice follows Command Query Responsibility Segregation, in plain Python, no library:

- The **Contract is messages.** `contract/queries.py` (and `commands.py` when a slice changes state) holds frozen dataclasses, one per use case, subclassing `Query` or `Command` from the kernel. `contract/results.py` holds what they return. There is no service class in a contract.
- **One handler per message**, in `internal/handlers.py`: a callable that takes the message and returns a kernel `Result`. Handlers never raise for domain reasons and never know about HTTP. (Infrastructure failures such as a saturated model are exceptions, which the route maps to a `503` problem.)
- **The bus routes.** `module.py` registers each handler for its message type on the one `Bus` the composition root builds. Two handlers for one message is an error.
- **Callers dispatch.** A route builds the message from its pydantic body and calls `request.app.state.bus.dispatch(message)`. Another slice does the same with the dependency's contract messages — which is why `grep RankCandidates` finds every caller of that use case.

Adding a use case is therefore: a message, a result type, a handler, one `bus.register` line, and (if it has an HTTP edge) a route. Tests swap a handler on the bus with `replace=True`.

## Chunking — the model's 512-token window

`multilingual-e5-large` truncates silently past 512 tokens. The engine caps every encoded text at `PFA_EMBED_MAX_TOKENS` (default 500, prefix and special tokens included) and the embeddings handlers ask the **`chunking` slice** — through its contract, on the bus — to split longer input first: on paragraph and sentence boundaries, falling back to words and finally characters, counting with the model's own tokenizer. `chunking` is a slice of its own so the seam is visible: `embeddings` declares it in `slice.json`, the slice map shows the fan-in, and `POST /chunking/split` lets you watch it work. `POST /embeddings/passages` returns one vector per chunk with `source_index`, `chunk_index`, `chunk_count`; `POST /embeddings/similarity` scores a candidate by its best chunk and returns that chunk's text; a long question is chunked and mean-pooled into one unit vector.
