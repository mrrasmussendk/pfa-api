# Architecture

[Back to README](../README.md)

- [New to this architecture? Start here](#new-to-this-architecture-start-here)
- [The dependency direction](#the-dependency-direction)
- [Inside a feature](#inside-a-feature)
- [The entry point](#the-entry-point)
- [CQRS — how a feature exposes capability](#cqrs--how-a-feature-exposes-capability)
- [Chunking — the model's 512-token window](#chunking--the-models-512-token-window)

## New to this architecture? Start here

If you have built APIs before, you have probably built them in **layers**: a `routes/` folder, a `services/` folder, a `models/` folder. Every feature is smeared across all of them, and every folder contains every *other* feature too.

This repo is organised the other way round. `src/pfa/` is **the application**, and it reads top-down: `application.py` builds it, `features/` holds what it does, `kernel/` holds what every feature shares, and `api/` is the door you come in through.

```
src/pfa/
  application.py     every feature registered on one bus; no HTTP
  features/
    chunking/        contract/, internal/, module.py, feature.json
    embeddings/
  kernel/            Result, Bus, Query, Command
  api/               the main entry point
    app.py           create_app()
    routes/          chunking.py, embeddings.py, health.py
    problems.py      RFC 9457 problem details
    validation.py    request hygiene
```

Each feature is built as a **vertical slice**: one folder containing everything the feature is, consumed by everyone else only through its `contract/`. To understand "embeddings" you open `src/pfa/features/embeddings/`. The HTTP edge is deliberately *not* in the feature: it is a thin adapter in `src/pfa/api/routes/embeddings.py` that turns requests into the feature's messages. The feature knows nothing of HTTP, so the same message serves a route today and another feature tomorrow.

### The vocabulary, in the order you will meet it

| Word | What it means here | Where it lives |
|---|---|---|
| **Application** | The whole thing: every feature registered on one bus. Built by one function, no HTTP anywhere. | `src/pfa/application.py` |
| **Feature** | One capability, as one Python package (a vertical slice): its contract, its logic, its plug into the application. Messages in, `Result` out. No framework. | `src/pfa/features/<name>/` |
| **Contract** | The *only* part of a feature a consumer may import: the messages it answers, the results it returns, and any infrastructure error it may raise. Plain dataclasses, no framework. | `features/<name>/contract/` |
| **Internal** | The implementation: engine and handlers. Private by rule, even though Python cannot enforce it. | `features/<name>/internal/` |
| **Module** | The feature's plug: `register(bus)`, `ready()`, `warmup()`. Imported by `application.py` and by nothing else. | `features/<name>/module.py` |
| **Manifest** | A one-line JSON file declaring which other features this feature may use. | `features/<name>/feature.json` |
| **Kernel** | The tiny set of primitives every feature shares: `Result`, `Bus`, `Query`, `Command`. Nothing else goes here. | `src/pfa/kernel/` |
| **Entry point** | A door into the application. Today there is one, HTTP: the only package that knows FastAPI and pydantic. | `src/pfa/api/` |
| **Route** | A feature's HTTP edge: pydantic models, one `APIRouter`, dispatch on the bus, `Problem` on error. One module per feature. | `api/routes/<name>.py` |
| **Message** | A frozen dataclass naming one thing you can ask a feature to do. A `Query` reads or computes; a `Command` changes state. | `contract/queries.py` (and `commands.py` once a feature changes state — none does yet) |
| **Handler** | The one function that serves one message. Takes the message, returns a `Result`. | `internal/handlers.py` |
| **Bus** | The dispatcher. Give it a message; it finds the handler and returns the `Result`. Callers never see handlers. | `kernel/messaging.py`, built in `application.py` |
| **Result** | A domain outcome: `ok`, `value`, `error`. Handlers return it instead of raising; the route turns it into a status code. | `kernel/primitives.py` |

### One request, end to end

Follow `POST /embeddings/similarity` through the code and you have seen the whole pattern:

1. **The route** (`pfa/api/routes/embeddings.py`) validates the JSON body with a pydantic model, builds a message, `RankCandidates(question=..., candidates=...)`, and calls `request.app.state.bus.dispatch(message)`. It imported exactly one thing from the feature: its contract.
2. **The bus** (`pfa/kernel/messaging.py`) looks up the one handler registered for `RankCandidates` and calls it.
3. **The handler** (`pfa/features/embeddings/internal/handlers.py`) does the work. It needs text split to the model's window, so it dispatches *another* message, `SplitText`, which belongs to the `chunking` feature's contract. It never imports chunking's internals; it only knows the message.
4. **Chunking's handler** splits the text and returns a `Result[Chunks]`. Embeddings' handler encodes the chunks with the model — behind the inference gate — ranks them, and returns a `Result[Ranking]`.
5. **Back in the route**, `result.ok` decides the status: a domain rejection such as an empty question becomes a `422` problem document via `Problem.domain_rejection(result.error)`; success becomes the response model. Had the model been saturated, the engine would have raised `EngineBusy` — declared in embeddings' contract, so the route can catch it — and the route would answer `503` with `Retry-After`.

Notice what did *not* happen: no route imported a service class, no feature imported another feature's implementation, nothing under `features/` touched FastAPI, pydantic or the application, no handler raised for a domain reason, and `application.py` never ran any logic.

### Why it is built this way

- **The tree says what the program is.** The root package is the application; the entry point is one folder inside it. Nothing has to be explained with a sentence like "the API is the main program and the slices are its features". It is visible.
- **A feature fits in one folder, so it fits in one head.** Whoever works on embeddings — human or AI — loads the embeddings folder, chunking's contract, the kernel, and (for an HTTP change) one routes module. That is the whole working set, and the EIT100 budget check keeps the feature part of it bounded as the project grows.
- **Contracts are data, so coupling is visible.** Because features and routes talk through message types, `grep SplitText` finds every place chunking is used. There is no hidden coupling through base classes, dependency-injection magic, or dynamic imports (EIT002 bans those on purpose).
- **The edge is an adapter, not a home.** Routes consume a feature exactly as another feature would. That is what keeps "internal" honest: nothing outside a feature depends on anything inside it except its contract and its plug.
- **The manifest is the seam.** `embeddings/feature.json` says `["chunking"]`. Import a third feature without declaring it and `eitri check` fails (EIT004). The dependency graph in the `AGENTS.md` files is generated from these manifests, so it cannot lie.
- **Rules fail the check, they do not warn.** Python will let you import anything. `eitri check` is what says no, and `tools/brokkr_canary.py` proves it still says no after every change.

### How do I…

- **…add an endpoint to an existing feature?** [guides/adding-a-route.md](../harness/guides/adding-a-route.md). Short version: pydantic models and a route in `src/pfa/api/routes/<feature>.py`, a message in the feature's `contract/` if the capability is new, a handler, one `bus.register` line in the feature's `module.py`, a test.
- **…return an error?** [guides/returning-errors.md](../harness/guides/returning-errors.md). `raise Problem.domain_rejection(result.error)` for a handler's `Result.ok=False`; `Problem(status, detail, type=…)` for anything else. Never `HTTPException`; never any of this inside a feature.
- **…add a feature?** Copy `src/pfa/features/embeddings/` as described in [AGENTS.md](../AGENTS.md), rename, write `feature.json`, add the module to `FEATURES` in `application.py`, write its routes module and add it to `ROUTERS`, run `heimdall map --root .`. The root `AGENTS.md` has the numbered steps.
- **…use another feature's capability?** Declare it in `feature.json`, import its `contract`, dispatch its message on the bus. Never import its `internal/` or `module.py`. If the capability you need is not in its contract, add it *there*, as a new message.
- **…share code between features?** First ask whether you should. A framework-free primitive every feature needs goes in the kernel, and every feature pays for it in its budget. FastAPI or pydantic glue goes in `src/pfa/api/` (`problems.py`, `validation.py`), which no feature imports. Logic is probably a feature.
- **…add a second entry point?** A CLI or a worker is a sibling of `api/` that calls `application.build_bus()` and dispatches messages. It never imports `pfa.api`.
- **…test without the 2 GB model?** Register a fake handler on the bus with `replace=True`. `tests/api/test_embeddings.py` does exactly this: real handlers, fake engine, real seam between features.
- **…know whether I broke something?** The [development checks](../README.md#development): linters, tests, walls. CI runs the same, plus Brokkr and the Jev review.

### The two mistakes everyone makes at first

1. **Putting shared things in the kernel.** The kernel is importable from every contract and counted in every feature's budget. It must stay tiny. FastAPI-aware helpers go in `pfa/api`, not the kernel.
2. **Importing `internal/` because it is right there.** Python lets you. The check does not. A feature whose internals are imported elsewhere can no longer be changed in isolation, which is the one property the whole design exists to protect.

## The dependency direction

Arrows point from the importing package to its allowed dependency. The application owns the features and plugs them in; the entry point consumes features through their contracts; each feature consumes other features through their contracts only.

![Allowed imports: arrows point from importer to dependency](diagrams/dependencies.svg)

[Mermaid source](diagrams/dependencies.mmd)

| Package | Knows about | May import | Framework code | Deployed |
|---|---|---|---|---|
| `pfa/api` | the application, every contract | `pfa.application`, `pfa.features.*.contract`, `pfa.kernel`, FastAPI/pydantic | yes | yes |
| `pfa/application.py` | every feature | `pfa.features.*.module`, `pfa.kernel` | **never** | yes |
| `pfa/features/<x>/module.py` | its own feature | own `internal`, own `contract`, `pfa.kernel` | **never** | yes |
| `pfa/features/<x>/internal` | its own feature | own `contract`, declared deps' `contract`, `pfa.kernel`, stdlib | **never** | yes |
| `pfa/features/<x>/contract` | its own feature | own `contract`, `pfa.kernel`, stdlib | **never** | yes |
| `pfa/kernel` | nothing | stdlib | **never** | yes |
| `tools/` | the layout rules | stdlib | no | **never** |

**The entry point is not the application, and the kernel is not the application.** `pfa.api` is one door; `pfa.application` is what is behind it. The **kernel** is the only package every feature may import, so it is priced into every feature's token budget (EIT100) and importable from every contract (EIT003); anything there is paid for by every feature and leaks into every public surface. Put application wiring in the kernel and you have a cycle and FastAPI in every contract.

**What goes where, when you are unsure:**

- Used by several features, framework-free, tiny, stable? → `pfa/kernel`. Think twice; every feature's budget grows.
- Tied to FastAPI/pydantic (an error shape, request hygiene, a response model)? → `pfa/api`. Eitri rejects the framework and the application anywhere under `features/` (EIT006) and `tests/api/test_architecture.py` checks the same.
- Something the edge must know about a feature that is not a `Result` — a timeout, a saturated model? → a plain exception in the feature's `contract/`, mapped to a `Problem` by the route.
- Wires features together? → `pfa/application.py`. Owns the process, mounts routers? → `pfa/api`. Nothing imports either from below.
- A new concept with its own data and rules? → a new feature.

## Inside a feature

The embeddings feature lives at `src/pfa/features/embeddings/`:

| Boundary | File | Responsibility |
|---|---|---|
| Configuration | `feature.json` | Declares the dependency on chunking |
| Instructions | `AGENTS.md` | Generated dependencies and routes, plus working notes |
| Public contract | `contract/queries.py` | `EmbedQuery`, `EmbedPassages`, `RankCandidates` |
| Public contract | `contract/results.py` | `Embedding`, `PassageEmbedding`, `Match`, `Ranking` |
| Public contract | `contract/errors.py` | `EngineBusy`: the one infrastructure failure the edge maps to a 503 |
| Private implementation | `internal/e5_engine.py` | Lazy loading, model prefixes, and inference concurrency |
| Private implementation | `internal/handlers.py` | One handler per query; dispatches chunking messages |
| Plug into the application | `module.py` | Registration, readiness, and warm-up |

Its HTTP edge is **outside** the feature, in `src/pfa/api/routes/embeddings.py`. `src/pfa/application.py` is an explicit, greppable list of feature modules. It builds the `Bus`, lets every feature register its handlers, and fans out each module's `ready()` and `warmup()` for `GET /ready` and the startup warm-up thread. Nothing outside a feature imports its `internal/`; Eitri (EIT001) and `tests/api/test_architecture.py` both check that.

## The entry point

`src/pfa/api/` is the only package that knows FastAPI and pydantic:

| File | Responsibility |
|---|---|
| `__main__.py` | `python -m pfa.api` / `pfa-api`: run the service with uvicorn |
| `app.py` | `create_app()`: builds the application, installs problem details, mounts `ROUTERS`, starts warm-up when asked |
| `routes/<feature>.py` | One module per feature: pydantic models, one `APIRouter(prefix="/<feature>")`, dispatch on the bus, `Problem` on error |
| `routes/health.py` | `GET /health` (liveness) and `GET /ready` (readiness), on the event loop |
| `routes/__init__.py` | `ROUTERS`: the explicit list `create_app()` mounts |
| `problems.py` | RFC 9457 problem documents: `Problem`, the problem types, `install_problem_details` |
| `validation.py` | Request hygiene: `StrictRequest`, `text(max)`, `check_total_chars` |

A routes module is a consumer of its feature like any other: it imports `pfa.features.<x>.contract` and nothing else from the feature. Heimdall classifies `pfa/api/routes/<feature>.py` as part of that feature, so an "add a route" task stays a one-feature task, and `heimdall map` reads that file to inventory the feature's routes.

The package inits of `pfa` and `pfa.api` import nothing. Features import `pfa.kernel` and routes import `pfa.api.problems`, so an eager import of the app from either init would loop. Import `pfa.api.app` explicitly.

## CQRS — how a feature exposes capability

Every feature follows Command Query Responsibility Segregation, in plain Python, no library:

- The **Contract is messages.** `contract/queries.py` (and `commands.py` when a feature changes state) holds frozen dataclasses, one per use case, subclassing `Query` or `Command` from the kernel. `contract/results.py` holds what they return. There is no service class in a contract.
- **One handler per message**, in `internal/handlers.py`: a callable that takes the message and returns a kernel `Result`. Handlers never raise for domain reasons and never know about HTTP. (Infrastructure failures such as a saturated model are exceptions declared in `contract/`, which the route maps to a `503` problem.)
- **The bus routes.** The feature's `module.py` registers each handler for its message type on the one `Bus` the application builds. Two handlers for one message is an error.
- **Callers dispatch.** A route builds the message from its pydantic body and calls `request.app.state.bus.dispatch(message)`. Another feature does the same with the dependency's contract messages — which is why `grep RankCandidates` finds every caller of that use case.

Adding a use case is therefore: a message, a result type, a handler, one `bus.register` line, and (if it has an HTTP edge) a route in `pfa/api/routes/`. Tests swap a handler on the bus with `replace=True`.

## Chunking — the model's 512-token window

`multilingual-e5-large` truncates silently past 512 tokens. The engine caps every encoded text at `PFA_EMBED_MAX_TOKENS` (default 500, prefix and special tokens included) and the embeddings handlers ask the **`chunking` feature** — through its contract, on the bus — to split longer input first: on paragraph and sentence boundaries, falling back to words and finally characters, counting with the model's own tokenizer. `chunking` is a feature of its own so the seam is visible: `embeddings` declares it in `feature.json`, the feature map shows the fan-in, and `POST /chunking/split` lets you watch it work. `POST /embeddings/passages` returns one vector per chunk with `source_index`, `chunk_index`, `chunk_count`; `POST /embeddings/similarity` scores a candidate by its best chunk and returns that chunk's text; a long question is chunked and mean-pooled into one unit vector.
