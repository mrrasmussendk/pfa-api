# chunking
<!--heimdall:deps-->depends on: (none) + kernel<!--/heimdall:deps-->
<!--heimdall:routes-->routes: POST /chunking/split<!--/heimdall:routes-->
<!--heimdall:scenarios-->scenarios: `tests/e2e/chunking.feature` — split-within-budget, empty-text-is-a-problem, control-character-is-rejected, budget-outside-the-window-is-rejected, unknown-field-is-rejected<!--/heimdall:scenarios-->
<!--heimdall:budget-->budget: ≈10% of 15,000 tokens used — `eitri check` has the exact count<!--/heimdall:budget-->

Splits text into pieces that fit a model-token budget, on paragraph and sentence boundaries first. The budget is a hard limit, counted by the model's own tokenizer.

| ✅ Do | ❌ Don't |
|---|---|
| Count with the configured model tokenizer (`PFA_EMBED_MODEL`), loaded lazily. | Substitute character counts, or load the tokenizer at import time. |
| Expose `CountTokens` and `SplitText` through the contract and the bus. | Make consumers import the chunker or the tokenizer. |
| Test the algorithm with a fake token counter. | Load embedding weights in chunking tests. |

- Working set: this folder, `src/pfa/kernel/`, and `src/pfa/api/routes/chunking.py` for an HTTP change.
- Contract: `CountTokens(text) -> int`, `SplitText(text, budget) -> Chunks`. `embeddings` is its one consumer.
