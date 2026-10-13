# embeddings
<!--heimdall:deps-->depends on: chunking + kernel<!--/heimdall:deps-->
<!--heimdall:routes-->routes: POST /embeddings/query, POST /embeddings/passages, POST /embeddings/similarity<!--/heimdall:routes-->
<!--heimdall:scenarios-->scenarios: `tests/e2e/embeddings.feature` — similarity-ranks-the-answer-first, no-candidates-is-rejected, question-becomes-a-query-vector, documents-become-passage-vectors-in-order<!--/heimdall:scenarios-->
<!--heimdall:budget-->budget: ≈40% of 15,000 tokens used — `eitri check` has the exact count<!--/heimdall:budget-->

Serves `intfloat/multilingual-e5-large`: text in, a unit-normalised 1024-dim vector out. It embeds; it does not answer.

| ✅ Do | ❌ Don't |
|---|---|
| Fit text to the model's window by dispatching `SplitText` / `CountTokens` from `pfa.features.chunking.contract`. | Import or read `features/chunking/internal/`, or rely on the model's silent truncation. |
| Keep the `query: ` / `passage: ` prefixes inside the engine; callers pick a side by picking the query. | Make callers add model prefixes. |
| Load the model lazily, outside the inference gate; let `EngineBusy` propagate to the route. | Load it at import or in `create_app()`, or turn `EngineBusy` into a domain result. |
| Use the fake engine and tokenizer in ordinary tests. | Load the real model unless `PFA_RUN_MODEL_TESTS=1`. |
| Report model loading through `ready()`. | Make `/health` depend on the model. |

- Working set: this folder, `src/pfa/features/chunking/contract/`, `src/pfa/kernel/`, and `src/pfa/api/routes/embeddings.py` for an HTTP change.
- Contract: `EmbedQuery`, `EmbedPassages`, `RankCandidates` → their results; `EngineBusy` in `contract/errors.py`.
- How it chunks, queues and warms up: [docs/architecture.md](../../../../docs/architecture.md) and [docs/service.md](../../../../docs/service.md).
