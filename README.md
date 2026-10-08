# PFA API ⚒️

A FastAPI embedding service built as **vertical slices**, with the architecture enforced by **Eitri**, guarded by **Brokkr**, watched by **Heimdall**, and judged on every pull request by **Jev**. A Python port of [brokkr-eitri](https://github.com/mrrasmussendk/brokkr-eitri); the tooling is development-only and lives beside the service, never inside it.

```
PFA-API/
├─ src/                      # the deployable service
│  ├─ pfa_api/               #   app factory, composition root, /health + /ready
│  ├─ http_common/           #   HTTP-edge helpers shared by slices: RFC 9457 problem details
│  ├─ shared_kernel/         #   the one package every slice may import: Result, Bus, Query, Command
│  └─ slices/                #   one package per feature: contract/ (public) + internal/ (private)
├─ tools/                    # development-only tooling — never deployed (its own package: pfa-devtools)
│  ├─ eitri/                 #   the walls + the context-budget gate         -> eitri check
│  ├─ heimdall/              #   the agent harness + the Jev review          -> heimdall map|hook|drift|review|estimate
│  ├─ brokkr/                #   the shared token estimator
│  ├─ brokkr_canary.py       #   mutation tests: every rule must bite
│  ├─ smoke_test.py          #   the harness loop end to end
│  └─ fixtures.py            #   the synthetic slice tree the tooling is tested against
├─ docs/guides/              # one guide per kind of change; docs/rules/ has one page per Eitri rule
├─ .github/workflows/        # ci.yml (the gate) · pr-review.yml (the judgment)
└─ tests/                    # tests/api (the service) + tests/tooling (the walls and the harness)
```

**Contents**

- [Quick start](#quick-start)
- **Part I — Using the service:** [Endpoints](#endpoints) · [The model](#the-model) · [Errors are problem documents](#errors-are-problem-documents-rfc-9457) · [Running in production](#running-in-production)
- **Part II — How the code is organised:** [New to this architecture?](#new-to-this-architecture-start-here) · [The dependency direction](#the-dependency-direction) · [Inside a slice](#inside-a-slice) · [CQRS](#cqrs--how-a-slice-exposes-capability) · [Chunking](#chunking--the-models-512-token-window)
- **Part III — What keeps it that way:** [Code quality](#code-quality--ruff-mypy-and-warnings-as-errors) · [Eitri](#eitri--the-walls) · [The token budget](#how-the-token-budget-works) · [Brokkr](#brokkr--the-canary) · [Heimdall](#heimdall--the-harness-) · [CI/CD](#cicd--the-gate-and-the-judgment)
- [Appendix](#appendix): what changed from the .NET original · the philosophy · license

## Quick start

```bash
pip install -e ".[dev]" -e ./tools              # the API with dev extras, plus the tooling
python -m pfa_api                               # http://127.0.0.1:8000 — docs at /docs
ruff check . && ruff format --check . && mypy   # lint, format, types (CI runs these first)
pytest                                          # tests/api + tests/tooling; warnings are errors
eitri check --root .                            # the walls + the budget — must be green
heimdall map --root .                           # regenerate the slice maps after changing a slice
```

A production install is `pip install .` and carries no tooling (CI verifies the wheel ships none). The container image is `docker compose up --build`; see [Running in production](#running-in-production).

---

# Part I — Using the service

## Endpoints

| Endpoint | What it does |
|---|---|
| `GET /health` | **Liveness.** The process answers. Says nothing about the model. |
| `GET /ready` | **Readiness.** `200` once the tokenizer and the model are in memory, `503` problem document while loading. Route traffic on this, not on `/health`. |
| `POST /chunking/split` | Split text into chunks that fit a token budget, counted with the model's own tokenizer. |
| `POST /embeddings/query` | Embed a question as a *query* vector (unit-normalised, 1024 dims). |
| `POST /embeddings/passages` | Embed documents as *passage* vectors — one vector per chunk, tagged with its source and chunk position. |
| `POST /embeddings/similarity` | Rank candidate texts against a question, best first; a long candidate scores as its best chunk. |

Interactive documentation at `/docs`, the OpenAPI document at `/openapi.json`. Every error response is documented there as an `application/problem+json` `ProblemDetails`.

```bash
curl -X POST localhost:8000/embeddings/similarity -H "content-type: application/json" \
     -d '{"question": "Hvordan nulstiller jeg min adgangskode?", "candidates": ["To reset your password, open Settings", "Opening hours"]}'
```

## The model

The service serves [`intfloat/multilingual-e5-large`](https://huggingface.co/intfloat/multilingual-e5-large), an **embedding** model: text in, a unit-normalised 1024-dimensional vector out, in 100+ languages. It does not answer questions. `POST /embeddings/query` turns a question into a *query* vector; rank your *passage* vectors against it (dot product = cosine, since both are normalised) to find what answers it. A Danish question ranks an English answer on meaning, not on shared words.

e5 needs an instruction prefix per side — `query: ` for questions, `passage: ` for documents. Only `slices/embeddings/internal/e5_engine.py` knows that; callers choose the side by choosing the endpoint.

```bash
# 1. torch: install the CUDA build FIRST if you have an NVIDIA GPU, otherwise skip this line
pip install torch --index-url https://download.pytorch.org/whl/cu124
# 2. the service (pulls sentence-transformers; a CPU torch if step 1 was skipped)
pip install -e ".[dev]" -e ./tools
# 3. the weights, ~2.2 GB, into the Hugging Face cache (HF_HOME, default ~/.cache/huggingface)
python tools/download_model.py
```

| Setting | Default | Meaning |
|---|---|---|
| `PFA_EMBED_MODEL` | `intfloat/multilingual-e5-large` | model id or local path; names the tokenizer too, so chunking and embedding agree |
| `PFA_EMBED_DEVICE` | CUDA if available, else CPU | `cuda`, `cuda:1`, `cpu` |
| `PFA_EMBED_MAX_TOKENS` | `500` | hard ceiling per encoded text, prefix and special tokens included (the model's window is 512) |
| `HF_HOME` | `~/.cache/huggingface` | where the weights live |

The model loads **lazily** — on the first request, or at startup when `PFA_WARMUP` is on — never at import time or in `create_app()`, so tests and local startup stay fast. Tests never load it: `tests/api/test_embeddings.py` registers the real handlers over a fake tokenizer and a fake engine. `PFA_RUN_MODEL_TESTS=1 pytest tests/api/test_embeddings.py` exercises the real model.

## Errors are problem documents (RFC 9457)

Every error the API sends — a domain rejection, a validation failure, an unknown path, a saturated model, a bug — has **one shape**: status code in the header, `Content-Type: application/problem+json`, and a body with the five members RFC 9457 defines plus optional extensions.

```json
{
  "type": "urn:pfa-api:problem:domain-rejection",
  "title": "Request rejected",
  "status": 422,
  "detail": "empty question",
  "instance": "/embeddings/query"
}
```

| Member | Meaning |
|---|---|
| `type` | URI naming the *kind* of problem. `about:blank` means "just the HTTP status". Stable: branch on this, not on `detail`. |
| `title` | Short summary of the kind — identical for every occurrence of that `type`. |
| `status` | The HTTP status, repeated in the body. |
| `detail` | What went wrong *this time*, for a human. Never contains stack traces, paths or secrets. |
| `instance` | The request path (or a more specific URI the route chose). |
| extensions | `errors` on validation problems, `components` on readiness, `retry_after` when busy. |

| `type` | Status | When |
|---|---|---|
| `urn:pfa-api:problem:domain-rejection` | 422 | A handler said the request does not make sense (blank question, empty passage). |
| `urn:pfa-api:problem:validation-error` | 422 | The body, query or path does not fit the models. `errors: [{loc, msg, type}]` lists each failure — never the offending input. |
| `urn:pfa-api:problem:not-ready` | 503 | `GET /ready` while components load. `components` says which; `Retry-After: 5`. |
| `urn:pfa-api:problem:model-busy` | 503 | The inference gate did not open within `PFA_EMBED_QUEUE_TIMEOUT`. `Retry-After` header and `retry_after` member. |
| `about:blank` | 404, 405, 500, … | Plain HTTP errors. A 500 carries **no `detail`**: the traceback goes to the server log, not the wire. |

The machinery lives in `src/http_common/problems.py` and is installed once in `create_app()`. Inside a slice, a route raises `Problem.domain_rejection(result.error)` or `Problem(status, detail, type=…, **extensions)`; raising FastAPI's `HTTPException` in a slice fails `eitri check` (rule EIT005). The developer side of this is `docs/guides/returning-errors.md`.

## Running in production

### The model is synchronous; the server is not

Torch inference is CPU- or GPU-bound and blocking. The slice routes are therefore plain `def` functions: FastAPI runs them on its threadpool, and the event loop never waits on the model. Two things follow from that, and both are handled:

- **The probes must not share that threadpool.** A cold load (seconds on GPU, minutes on a first download) or a burst of first requests fills the pool. `/health` and `/ready` are `async def` and run on the event loop, so they keep answering through it. Otherwise a slow start looks like a dead process, gets restarted, and never finishes loading.
- **N concurrent requests must not mean N concurrent inferences.** The engine serialises `encode` behind a bounded gate, `PFA_EMBED_CONCURRENCY` (default 1). A burst becomes an ordered queue; torch already parallelises one encode across the cores, and on a GPU concurrent calls on one model race for memory. A request that waits longer than `PFA_EMBED_QUEUE_TIMEOUT` gets a `503` `model-busy` problem with `Retry-After` instead of hanging. The one-time load happens *outside* the gate, so a cold start never holds the inference slot.

### Start warm, route on readiness

`PFA_WARMUP=1` loads the tokenizer, then the model, on a background thread when the app starts. Startup returns at once; `/health` is green immediately; `/ready` turns `200` when both loads finish. A failed warm-up is logged and leaves `/ready` at `503`, which the orchestrator sees. The image and the compose file turn warm-up on; tests and local development leave it off.

```
startup ──▶ /health 200 ───────────────────────────────────▶
            /ready  503 not-ready {tokenizer: false, embedding_model: false}
                    503 not-ready {tokenizer: true,  embedding_model: false}
                    200 ready     {tokenizer: true,  embedding_model: true}   ◀── traffic from here
```

### Settings

| Setting | Default | Meaning |
|---|---|---|
| `PFA_HOST` / `PFA_PORT` | `127.0.0.1` / `8000` | bind address (the image binds `0.0.0.0`) |
| `PFA_WARMUP` | off (on in the image) | preload tokenizer + model at startup on a background thread |
| `PFA_EMBED_CONCURRENCY` | `1` | encodes allowed at once; raise only with GPU headroom |
| `PFA_EMBED_QUEUE_TIMEOUT` | `30` | seconds a request waits for the gate before `503 model-busy` |
| `PFA_WORKERS` | `1` | uvicorn workers. Each holds its own 2 GB model — **scale with replicas, not workers** |
| `PFA_FORWARDED_ALLOW_IPS` | `127.0.0.1` | the reverse proxy whose `X-Forwarded-*` headers to trust |
| `PFA_GRACEFUL_SHUTDOWN` | `30` | seconds to let in-flight encodes finish on SIGTERM |
| `PFA_LOG_LEVEL` / `PFA_ACCESS_LOG` | `info` / `1` | uvicorn logging |
| `PFA_RELOAD` | off | `1` for auto-reload in development |

### Request validation

Every request body is validated at the edge before a handler sees it, and every failure is a `422` `validation-error` problem naming the field:

| Check | Rule |
|---|---|
| Unknown fields | rejected (`extra_forbidden`), so a typo such as `questions` fails on the first call instead of being ignored |
| Question | 1–20 000 characters |
| Passage / candidate / text to split | 1–200 000 characters each, at most 256 per request |
| Whole request | all texts together at most 2 000 000 characters — the work one call may ask for |
| Control characters | NUL and other C0/C1 controls are rejected with their position; tab, newline and carriage return are allowed |
| Chunk budget | 1–512 tokens |

Whether the *content* makes sense — a whitespace-only question, an empty passage — is the handler's decision and comes back as a `domain-rejection` with the reason. There is no body-size limit at the server itself; put that in the reverse proxy in front of it.

### The image

`Dockerfile` is multi-stage: stage one builds the wheel from `src/`, stage two installs it on `python:3.12-slim` as a non-root user with `PFA_WARMUP=1`, `PFA_EMBED_CONCURRENCY=1` and `TOKENIZERS_PARALLELISM=false` set. `.dockerignore` is an allow-list, so a new top-level folder stays out until someone adds it on purpose. The Docker `HEALTHCHECK` is liveness (`/health`); point the orchestrator's readiness probe at `/ready`.

| Build arg | Default | Use |
|---|---|---|
| `TORCH_INDEX` | `https://download.pytorch.org/whl/cpu` | CPU torch (~200 MB). Set to `.../whl/cu124` for a CUDA image. |
| `PRELOAD_MODEL` | `0` | `1` bakes the 2.2 GB weights into the image, so the first start does not download. |
| `PYTHON_VERSION` | `3.12` | |

The weights live in `/models` (`HF_HOME`), declared as a volume: mount it to download once and keep it across restarts.

```bash
docker build -t pfa-api .
docker run -p 8000:8000 -v pfa-models:/models pfa-api
docker compose up --build        # the same, with a named volume and the GPU lines ready to uncomment
```

---

# Part II — How the code is organised

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

- **…add an endpoint to an existing feature?** `docs/guides/adding-a-route.md`. Short version: pydantic models and a route in the slice's `internal/routes.py`, a message in its `contract/` if the capability is new, a handler, one `bus.register` line in `module.py`, a test.
- **…return an error?** `docs/guides/returning-errors.md`. `raise Problem.domain_rejection(result.error)` for a handler's `Result.ok=False`; `Problem(status, detail, type=…)` for anything else. Never `HTTPException` inside a slice.
- **…add a feature?** Copy `src/slices/chunking/` (the smallest slice), rename, write `slice.json`, add the module to `MODULES` in `composition.py`, run `heimdall map --root .`. The root `AGENTS.md` has the numbered steps.
- **…use another slice's capability?** Declare it in `slice.json`, import its `contract`, dispatch its message on the bus. Never import its `internal/`. If the capability you need is not in its contract, add it *there*, as a new message.
- **…share code between slices?** First ask whether you should. A framework-free primitive every slice needs goes in the kernel, and every slice pays for it in its budget. FastAPI or pydantic glue goes in `src/http_common/`, imported only from `internal/`. Logic is probably a slice.
- **…test without the 2 GB model?** Register a fake handler on the bus with `replace=True`. `tests/api/test_embeddings.py` does exactly this: real handlers, fake engine, real seam between slices.
- **…know whether I broke something?** The Quick start block, top to bottom: linters, tests, walls. CI runs the same, plus Brokkr and the Jev review.

### The two mistakes everyone makes at first

1. **Putting shared things in the kernel.** The kernel is importable from every contract and counted in every slice's budget. It must stay tiny. FastAPI-aware helpers go in `http_common`, not the kernel.
2. **Importing `internal/` because it is right there.** Python lets you. The check does not. A slice whose internals are imported elsewhere can no longer be changed in isolation, which is the one property the whole design exists to protect.

## The dependency direction

Everything in this repo follows one arrow. Code may only import **downwards**:

```
          pfa_api            the composition root: knows EVERY slice, implements NOTHING
             │               (app factory, build_bus, routers, /health, /ready, warm-up)
             ▼
          slices/*           the features: each knows its OWN code, its declared
             │               dependencies' CONTRACTS, http_common, and the kernel
             ▼
        http_common          HTTP-edge helpers: knows FastAPI, knows NO slice
             │               (Problem, install_problem_details)
             ▼
        shared_kernel        the primitives: knows NOTHING about slices or HTTP
                             (Result, Bus, Query, Command)
```

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

```
slices/embeddings/
├─ slice.json              # {"depends_on": ["chunking"]} — the declared seam, and nothing else
├─ AGENTS.md               # generated deps + routes lines, then hand-written notes
├─ contract/               # PUBLIC surface — framework-free messages + result types (CQRS)
│  ├─ queries.py           #   EmbedQuery, EmbedPassages, RankCandidates
│  └─ results.py           #   Embedding, PassageEmbedding, Match, Ranking
└─ internal/               # everything else — private by rule
   ├─ e5_engine.py         #   the model wrapper: lazy load, query:/passage: prefixes, the inference gate
   ├─ handlers.py          #   one handler per query; dispatches chunking's SplitText through the bus
   ├─ routes.py            #   the slice's FastAPI router; pydantic models live here, errors are Problems
   └─ module.py            #   wiring: register(bus), router, ready(), warmup() — all the composition root sees
```

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

---

# Part III — What keeps it that way

Four layers, each catching what the one before cannot:

| Layer | Tool | Answers | Runs |
|---|---|---|---|
| Code quality | Ruff, mypy, pytest with warnings as errors | Is the code well-formed, typed, tested? | locally and first in CI |
| The walls | Eitri | Does the code respect the architecture? | `eitri check`, CI |
| The canary | Brokkr | Does Eitri still bite? | CI |
| The harness | Heimdall (+ Jev on PRs) | What did the agent actually do, and is the change sound? | every tool call; every PR |

## Code quality — Ruff, mypy, and warnings as errors

```bash
ruff check . && ruff format --check . && mypy    # what CI runs; `ruff check --fix . && ruff format .` repairs most findings
```

- **Ruff** lints and formats, configured in `[tool.ruff]`: line length 140, Python 3.10 target, isort that knows the repo's first-party packages. Rule families: pycodestyle, pyflakes, isort, bugbear, pyupgrade, simplify, comprehensions, performance, pylint errors and warnings, implicit string concatenation, and a ban on stray `print` in the service (the tooling CLIs and tests are exempt — printing is their job). Each ignored rule has its reason beside it in the config.
- **mypy** checks `src/` and the tooling, configured in `[tool.mypy]`. The service packages require typed definitions; the tooling is checked with `check_untyped_defs`. Torch and sentence-transformers ship no stubs and are ignored as missing.
- **Warnings are errors** in pytest (`filterwarnings = ["error"]`): a deprecation anywhere fails the suite instead of scrolling past. The dev extra depends on `httpx2`, which Starlette's `TestClient` now expects.

## Eitri — the walls

`eitri check --root .` runs every check (CI runs it, and `tests/api/test_architecture.py` runs it in pytest). Config lives in `[tool.eitri]` in `pyproject.toml`.

| Rule | What it guards | Severity |
|---|---|---|
| **EIT001** | A slice's public surface is its `contract/` package. Importing anything else of another slice binds you to its internals. `internal/module.py` is the one exemption. | error |
| **EIT002** | No visibility escape hatches: `importlib.import_module`, `__import__`, `sys.path.*`, `sys.modules[...] =`, `from x import *`. | error |
| **EIT003** | Contract purity: `contract/` modules may import only the stdlib, the kernel, and their own contract. (FastAPI and pydantic therefore stay in `internal/`.) | error |
| **EIT004** | Undeclared dependency: importing `slices.x.contract` requires `x` in your `slice.json`. | error |
| **EIT005** | Error shape: no `HTTPException` from `fastapi`/`starlette` inside a slice, however imported or aliased — errors are RFC 9457 problem documents raised as `http_common.Problem`. | error |
| **EIT100** | **Context budget**: own source + dependency contract surfaces + kernel > budget → check fails. | error |
| **EIT101** | Context budget report (the same number, always visible). | info |

| `[tool.eitri]` key | Default | Meaning |
|---|---|---|
| `slice_prefix` | `slices.` | the package Eitri polices |
| `kernel` | `shared_kernel` | the one package every slice may import freely |
| `token_budget` | `15000` | EIT100's ceiling per slice |
| `contract_allowed_modules` | `[]` | third-party packages a contract may import (relaxes EIT003) |
| `problem_helper` | `http_common.Problem` | the remedy EIT005 names — the rule itself knows nothing about this project |
| `enabled` | `true` | **`false` disarms every wall.** Brokkr exists to make that visible. |

EIT100 is the novel one: every check, every slice, it computes the worst-case working set an AI coding agent would need to load to work on that slice, and fails if it exceeds the budget. Dependencies are priced by their **contract surface reconstructed from the AST as body-less stubs** (`eitri surface embeddings` prints it), so a dependency's internals cost zero — the architecture's promise, as a number.

```
src/slices/search: error EIT100: Slice 'search' worst-case agent working set is ≈16,204 tokens
      (own source 12,981 + dependency contracts 2,672 + kernel 551), over the budget
      of 15,000 — split the slice or slim its contracts
```

### How the token budget works

**Why it exists.** An agent has no memory of the codebase between sessions. Everything it knows about the code is what it loaded into context this session, and the quality of what it writes falls off with the size of what it had to read to get there. Past a certain working set the agent stops reading and starts guessing: it re-implements a helper it never saw, calls a function with the signature it assumed, edits the file that looked right. A human compensates with months of familiarity, an IDE, and a colleague to ask. The agent starts cold every time and pays the full price of loading the working set on every task. The size of that working set is therefore the single biggest lever on whether agent output is correct, and the one no test suite measures.

EIT100 puts a ceiling on it. It is a [fitness function](https://www.thoughtworks.com/insights/articles/fitness-function-driven-development): an architectural property turned into a number the build checks, so the property cannot erode quietly. The number is in tokens rather than lines or files because tokens are the unit the agent's context is actually measured in — the gate and the thing it protects use one ruler.

**Why it produces clean code without a rule saying so.** "Separate your concerns" and "keep interfaces thin" are not enforceable: no check can tell a good seam from a bad one. A token ceiling *is* enforceable, and the only ways to stay under it happen to be the things those rules ask for:

- To keep a slice under budget you must split it when it grows past one capability — **cohesion**, because the split that keeps both halves small is the one along the real seam.
- To keep a dependency cheap you must keep its `contract/` to messages and result types and move everything else to `internal/` — **thin interfaces**, because a consumer pays for every public name you expose.
- To keep every slice under budget you must keep the kernel tiny — **no god-module**, because the kernel is priced into all of them.
- And you cannot cheat by reaching into a neighbour's `internal/` for a shortcut, because EIT001–EIT004 fail the build. The walls are what make the pricing honest; the budget is what makes the walls worth having.

Agents optimise for the feedback they get. If the gate is "tests pass", code lands wherever it is easiest to make a test pass. If the gate also says "this slice no longer fits", the agent is told, in the loop, to find the seam — and it does, because that is the only move that turns the check green. That is what makes this part of the harness rather than a linter: `AGENTS.md` says where things go (feedforward), EIT100 says when the map has stopped being true (feedback), and `heimdall drift` closes the loop by showing whether agents actually stayed inside the working set the budget promised. Sustained reads outside it mean the seam is wrong, not the agent.

What the budget does *not* guarantee is that a split is a good one: two slices that always change together are worse than one, and the check will not object. It bounds what must be read. Judgement about where to cut is still yours — or Jev's, on the PR.

`tools/eitri/context_budget.py` computes the number; `tools/eitri/surface.py` builds the stubs; `tools/brokkr/tokenization.py` counts the tokens.

**The formula.** For every slice, on every `eitri check`:

```
working set  =  own source                      every .py under src/slices/<slice>/ (contract/ + internal/), counted in full
             +  Σ dependency contract surfaces   for each slice in slice.json: its contract/ as body-less stubs
             +  kernel surface                   src/shared_kernel/ as body-less stubs — always, every slice may import it
```

`http_common` is not in the sum: it is declared shared in `[tool.heimdall]`, is only imported from `internal/`, and is not something a slice *consumes* as a dependency. Tests live under `tests/`, outside the slice, and are not counted either.

**What a "surface" is.** A dependency is priced by what you need to *call* it, not by what it does. Eitri parses each contract module into an AST and re-renders it as a `.pyi`-shaped stub: public classes, functions, annotated attributes and constants, with every function body replaced by `...`, private names (`_x`) dropped, dunders kept (an agent needs `__init__` to construct a type), and `__all__` honoured when a module declares one. `eitri surface <slice>` prints exactly what gets priced; `eitri surface shared_kernel` prints the kernel's. This is chunking's whole contract surface today:

```
# queries.py
@dataclass(frozen=True)
class CountTokens(Query):
    text: str
@dataclass(frozen=True)
class SplitText(Query):
    text: str
    budget: int

# results.py
@dataclass(frozen=True)
class Chunks:
    tokenizer: str
    budget: int
    chunks: tuple[str, ...]
    token_counts: tuple[int, ...]
```

That is 82 tokens. Chunking's *implementation* — the tokenizer wrapper, the paragraph/sentence/word fallbacks, the router — costs `embeddings` nothing, which is exactly what the architecture promises: you consume a slice through its contract and never open its `internal/`. The same stub treatment is why the kernel's 686 tokens of source price at 177. A file Eitri cannot parse is charged at its raw text, so the number never under-counts.

**How tokens are counted.** Brokkr's estimator is dependency-free so the gate stays fast and importable from anywhere: identifiers cost one token per ~6 characters, digit runs one per ~3, up to three punctuation characters cost one, whitespace is free. It lands within roughly ±10% of `o200k_base` on typical Python and errs on the high side, so a stricter budget is the failure mode. `heimdall estimate <path>` prints the same estimator's number for any file or directory, so a human and the gate use one ruler. `docs/calibration.md` shows how to re-check it against tiktoken.

**Today's numbers** (`eitri check --root .` always prints them as EIT101 info lines; `--no-info` hides them):

| slice | own source | dependency contracts | kernel | total | of budget |
|---|---:|---:|---:|---:|---:|
| chunking | 2,242 | 0 | 177 | 2,419 | 16% |
| embeddings | 4,796 | 82 (chunking) | 177 | 5,055 | 34% |

**Where the budget comes from.** `[tool.eitri] token_budget` in `pyproject.toml`, 15,000 by default; `eitri check --budget N` overrides it for one run (Brokkr's canary 7 uses `--budget 100` to prove the rule still bites). The number is deliberately far below any model's context window: a slice that fits in 15k tokens leaves most of the window for the task, the guides, the conversation and the diff, and a human can hold it in their head too.

**What it costs to add code.** The formula tells you who pays before you write a line:

- A line in your own `internal/` costs **only your slice**.
- A public name in your `contract/` costs **you plus every slice that depends on you** — the fan-in column in `AGENTS.md`'s slice map is the multiplier.
- A public name in `shared_kernel` costs **every slice in the repo**, forever. That is why the kernel is two files — `Result`, and the `Message`/`Query`/`Command`/`Bus` messaging primitives — and why "should this go in the kernel?" is usually answered no.

**When EIT100 fails** the fix is structural, never the number. Split the slice by sub-capability (two slices with a contract between them each fit; one god-slice does not), slim the contract (a consumer should see messages and result types, not helpers — move the rest to `internal/`), or slim the kernel. Raising `token_budget` is a change to the promise, not to the code, and Brokkr will ask you to run the canaries afterwards. `docs/rules/EIT100.md` is the one-page version of this section.

Rules that merely warn get negotiated with. Rules that fail the build get obeyed. In .NET that gate was the compiler; here it is `eitri check` in CI — which is why Brokkr exists. One page per rule under `docs/rules/`.

## Brokkr — the canary

Eitri says whether the code passes. Brokkr says whether Eitri **still bites**. It is a mutation test for the rules themselves: inject one deliberate violation per rule and assert that the check fails. A rule that no longer fails on its own violation is a wall that looks armed and is not — and nothing else in the repo would tell you.

The ways a wall gets disarmed are all quiet. Someone sets `enabled = false` "for now". A refactor of the tooling changes how imports are resolved and a rule silently stops matching. A config key is renamed and the budget falls back to its default. `eitri check` prints `0 error(s)` in every one of those cases, and the green looks exactly like a real green.

`python tools/brokkr_canary.py` runs eight checks, in order, one line each:

| # | Check | What it proves |
|---|---|---|
| 1 | The real `src/` passes `eitri check` clean | The walls do not bite on clean work. |
| 2–6 | EIT001, EIT002, EIT003, EIT004, EIT005 bite | One minimal violating file per rule, written into a synthetic tree, fails the check. |
| 7 | EIT100 bites | With `--budget 100`, the budget rule fails on a tree that passes at 15000. |
| 8 | `enabled = false` disarms everything | The config switch really does switch the walls off — so you know what that line means if you ever see it in a diff. |

Checks 2 to 8 run against a **synthetic two-slice tree** written to a temp directory by `tools/fixtures.py`, not against the real service, so the canaries keep meaning the same thing as the real slices change. The exit code is the number of canaries that failed to bite.

```
canary ok:   real repo passes (walls do not bite on clean work)
canary ok:   EIT001 bit
canary ok:   EIT002 bit
canary ok:   EIT003 bit
canary ok:   EIT004 bit
canary ok:   EIT005 bit
canary ok:   EIT100 bit
canary note: 'enabled = false' silently disarms every wall — keep Brokkr in CI
---
canary: 8 passed, 0 failed
```

**Reading a failure.** `CANARY FAIL: EIT003 did not bite` means the rule is broken, not your code: look at `tools/eitri/walls.py`, `imports.py`, `core.py`. The one exception is check 1 — `the real repo does not pass` means the service has a violation, and Brokkr stops there.

**Adding a canary.** Every rule needs one; `tools/new_rule.py EIT006 "title"` scaffolds the rule page and reminds you. A canary is one tuple in `CANARIES`: the rule id, a path in the synthetic tree, and the smallest file that violates *only* that rule.

If you tune the rules — relax EIT003 with `contract_allowed_modules`, point EIT005 at another helper with `problem_helper`, raise the budget — run Brokkr afterwards. Relaxing is fine; disarming is not, and only Brokkr can tell you which one you just did.

## Heimdall — the harness 👁️

Eitri is a gate: it runs when you ask and says yes or no. Heimdall is a **harness**: it runs *while a coding agent works* — tell the agent what exists before it starts, watch what it touches, talk back when it crosses a line — and, on a pull request, hands the finished diff to a judge.

```
  feedforward ──▶ the agent works ──▶ sensors observe ──▶ feedback ──▶ review
  (AGENTS.md,      (Read / Grep /      (hook on every     (in-loop warning,   (Jev judges the
   map.json)        Edit / Write)       tool call)         drift report)       PR diff)
        ▲                                                        │
        └──────── re-cut a seam when the drift says so ──────────┘
```

Everything Heimdall knows comes from `.heimdall/map.json`, and everything it writes goes under `.heimdall/` (gitignored, per-checkout state).

### 1 · Feedforward — what the agent reads before it acts

| Artifact | Who writes it | What it tells the agent |
|---|---|---|
| `AGENTS.md` (root) | you, except the slice table | The dependency direction, the walls, the exact working set for a task, how to add a slice, how to change a contract, which guide to read. `CLAUDE.md` is one line, `@AGENTS.md`, so Claude Code loads the same document. |
| the **slice table** inside it | `heimdall map` | Every slice with its declared dependencies, fan-in, contract path, and routes — generated from the manifests and `routes.py`, so it cannot be stale. |
| `src/slices/<name>/AGENTS.md` | `heimdall map` writes the first two lines, you write the rest | `depends on:` and `routes:` (generated), then what the slice is for, its working set, its gotchas. |
| `docs/guides/*.md` | you | One guide per kind of change: adding a route, returning an error, what the PR review judges. |
| `.heimdall/map.json` | `heimdall map` | The machine-readable slice table plus the **shared packages** from `[tool.heimdall] shared` (today `http_common`). This is what the sensors and the review read. |

`heimdall map --root .` regenerates all of it. Run it after adding a slice, changing a `slice.json`, adding a route, or changing `[tool.heimdall]`.

### 2 · Sensors — what the agent actually touches

`.claude/settings.json` registers `python -m heimdall hook` as a Claude Code `PostToolUse` hook for `Read`, `Grep`, `Glob`, `Edit`, `Write` and `MultiEdit`. Claude Code runs it as a fresh process after every one of those calls. The hook loads the map, runs every sensor over the event, appends findings as JSON lines to `.heimdall/telemetry.jsonl`, and exits.

| Sensor | Fires on | What it records |
|---|---|---|
| `boundary_reads` | Read, Grep, Glob of a `.py`, `.pyi`, `.md` or `slice.json` | The path's class: `kernel`, `shared:<pkg>`, `slice:<name>`, `contract:<name>`, or `outside`. Pure observation. |
| `boundary_edits` | Edit, Write, MultiEdit inside a slice | Which slice, remembered per session in `.heimdall/session-<id>.json`, and whether the edit deserves feedback. |

Constraints, because this runs on *every* tool call: stdlib only, no network, no model or framework imports, one small state file per session, and any failure exits 0 so a broken hook can never block the agent. Latency is about 100 ms, mostly interpreter start-up. Adding a sensor is one class and one line in `tools/heimdall/sensors/__init__.py` — an explicit list, never a package scan.

### 3 · Feedback — talking back

**In the loop, immediately.** When a sensor attaches feedback, the hook prints `Heimdall: <message>` to stderr and exits 2, which Claude Code shows the agent before its next action. Exactly two situations trigger it: editing a **second slice** in one session ("cross-slice changes should go through contracts; confirm this is intentional"), and editing a **frozen contract** (fan-in 10 or more: "additive changes only"). Reads never trigger feedback; interrupting context-gathering trains an agent to read less, not better.

**After the session, in aggregate.** `heimdall drift` replays the telemetry against the map:

```
session   slices edited               reads   oob   oob %
clean     embeddings                      7     0      0%
wander    embeddings                      2     2    100%

slice                   reads  out-of-bounds   oob %
embeddings                  9              2     22%

rule of thumb: sustained oob% > 20 on a slice = re-cut that seam
```

A read is *in bounds* if it hit the kernel, a shared package, a slice the session edited, or the contract of a slice the edited slice declares. The per-session table comes first because wandering is a session property that the aggregate dilutes. The number to act on is the aggregate over time: if work on a slice keeps needing reads outside its working set, the boundary is drawn wrong.

### 4 · Review — Jev as the judge

`heimdall review` is the judgment on top of the gate. [Jev](https://docs.typesafe.ai/) (TypeSafe AI) does not write review comments; it answers **typed** questions — yes/no with a probability, a choice, a position on a rubric — over a state you give it. Heimdall is the deliberation around that call:

1. **Bounded state.** The diff, cut per file and capped (12 000 characters per file, 60 000 total; lock files and binaries dropped, truncations named), plus what Heimdall already knows: the slice map as the architecture's promise, the walls in one sentence each, and computed facts — slices touched, cross-slice or not, contracts touched with fan-in and frozen flag, whether routes, tests or docs changed — and the PR title and body as the stated task. Facts stay facts, so the model spends judgment on intent and placement.
2. **One call, fourteen questions.** Gates (`safe_to_merge`, `needs_human_review`, `has_security_concern`), rubrics (`correctness`, `clean_code`, `blast_radius`, `docs_in_sync`), fit (`respects_slice_walls`, `errors_use_problem_details`, `tests_cover_change`, `stays_in_scope`, `leaks_internals`), and two labels for the report.
3. **Policy in code.** Thresholds in `T` in `tools/heimdall/commands/review.py` map the answers to one outcome: `approve` · `comment` · `request_changes` · `escalate` (exit 0 · 0 · 1 · 2; 3 when the review could not run). Missing answers fall back to neutral and land on `escalate`, never `approve`.
4. **Outputs.** A table on stdout, `--json` and `--markdown` reports, and a `review` line in the telemetry beside the session's reads and edits.

```bash
export TYPESAFE_API_KEY=…                       # https://console.typesafe.ai/
heimdall review --base origin/main --task "…"   # what the PR check will see
heimdall review --diff change.diff --dry-run    # print the state and the questions, call nothing
```

Stdlib only, retries on 429/529, never on 401. Facts stay with Eitri: Jev is *told* the walls, never asked to re-derive them. The thresholds are a starting point — measure them against your own merges (`heimdall review --json` on past PRs) before letting `approve` carry weight. Full detail in `docs/guides/pr-review.md`.

### 5 · Estimate — the shared ruler

`heimdall estimate <file-or-dir>` prints the token estimate using the same `brokkr` estimator Eitri's EIT100 uses, so a human can ask "how big is this slice to an agent?" with the ruler the gate applies. Calibration against a real tokenizer: `docs/calibration.md`.

### What Heimdall does not do

- It does not enforce. The walls are Eitri's; Heimdall observes, advises, and on PRs judges. An agent can ignore the warning; the check and the review catch it later.
- It does not see Bash. Reads done with `cat` or `grep` in a shell are invisible to the hook.
- It does not know what the agent *intended*. Bounds are computed over the whole session.
- It does not call the network from the hook. Only `heimdall review` does, and only when asked.
- It does not run itself. If `.heimdall/map.json` is missing the hook exits silently — the first thing to check when the harness seems quiet.

```bash
pip install -e ./tools            # the hook needs `python -m heimdall` to resolve
heimdall map --root .             # feedforward: regenerate the maps (do this first)
heimdall drift                    # feedback: the report, once there is telemetry
python tools/smoke_test.py        # the whole loop end to end through the real hook, prints latency
pytest tests/tooling              # behavioral scenarios, format parity, the review's policy — against a synthetic tree
```

## CI/CD — the gate and the judgment

Two workflows. `ci.yml` is the **gate**: facts, and a fact failing fails the build. `pr-review.yml` is the **judgment**: Jev's typed answers, with policy deciding what fails.

```
ci.yml (push to main, tags, every PR)
  enforce (ubuntu + windows, py3.10 + py3.12)  ──▶  wheel ships no tooling  ──▶  docker image
    ruff check · ruff format --check · mypy           python -m build               build + boot + verify
    eitri check --root .                              grep the wheel listing        push to GHCR (main, tags only)
    python tools/brokkr_canary.py
    pytest  (API with fake model + tooling; warnings are errors)
    python tools/smoke_test.py
    heimdall map && git diff --exit-code AGENTS.md    <- a stale slice map fails the build

pr-review.yml (every non-draft PR)
    heimdall map (must be current)  ──▶  heimdall review --base origin/<base>  ──▶  one sticky PR comment
                                          approve / comment  -> check passes        request_changes -> check fails
                                          escalate -> warning + needs-human-review label
                                          no TYPESAFE_API_KEY (forks) -> "skipped", check passes
```

**What the image job verifies before it pushes.** The built image is started and must answer `/health` and list `/embeddings/similarity` in its OpenAPI; `import eitri` and `import heimdall` must *fail* inside it, proving the dev tooling never ships. Only then, and only on `main` or a tag, is it pushed to `ghcr.io/<owner>/pfa-api` tagged with the branch, the semver (`v1.2.3` → `1.2.3` and `1.2`), and `sha-<commit>`.

**What the review needs from you.** A `TYPESAFE_API_KEY` repository secret. Without it the review posts a "skipped" comment and passes, so forks and key-less checkouts are never blocked by it.

---

# Appendix

## What changed from the .NET original

| .NET original | Here |
|---|---|
| Roslyn analyzers inside `csc` | `eitri check`, an `ast`-based checker |
| `internal` types invisible across assemblies | EIT001 forbids importing another slice's non-`contract` modules |
| `InternalsVisibleTo` banned | EIT002 bans dynamic imports, `sys.path` surgery, wildcard imports |
| Contract *signatures* restricted | EIT003 restricts contract *imports* (broader) |
| csproj + `DisableTransitiveProjectReferences` | `slice.json` + EIT004 |
| — | EIT005: one error shape on the wire (RFC 9457), enforced as a wall |
| Surfaces from assembly metadata | Surfaces as AST stubs |
| NativeAOT hook, ~26 ms | Python hook; latency is interpreter start-up (measured, not gated) |
| — | `heimdall review`: Jev's typed judgments on the PR diff, policy in code |
| Samples project (Kvad/Rune) | Removed; the tooling is tested against a synthetic tree in `tools/fixtures.py` |

The original's measured results (3.3× cheaper single-feature tasks, O(1) vs O(n) growth) came from its .NET twin-repo experiment and were not re-run here. Its 16 behavioral harness checks are ported verbatim in `tests/tooling/test_behavioral_scenarios.py`.

## The philosophy in one line

> Agents don't feel architectural pain. Convert it into a number that fails a check.

## License

MIT
