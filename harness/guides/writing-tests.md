# Writing tests

| ✅ Do | ❌ Don't |
|---|---|
| Write the failing test first, then the least production code that passes it. | Write the handler or route and back-fill tests that describe whatever it happens to do. |
| Test one concept per function, named as the sentence it proves. | Pile several behaviours into one test so a failure names nothing. |
| Lay every test out as Arrange, Act, Assert, in that order. | Interleave setup, calls and checks so a reader has to trace the body. |
| Fake the expensive component on the bus with `bus.register(..., replace=True)`. | Load the real model or hit the network in an ordinary test. |
| Keep a test readable as prose: no branching, no loops that compute the expectation. | Share mutable state between tests or depend on their order. |

⚠️ **Check:** the PR judge asks `tests_cover_change` whenever source changed. A change with no changed test is not demonstrated, and `pytest` runs with warnings as errors, so a deprecation anywhere in the suite fails the run.

## Why this matters for an agent

An agent starts every session cold. It cannot remember how the handler behaved yesterday, and it cannot watch a human run the service by hand. The test suite is the only executable statement of what the feature is supposed to do that survives between sessions, so a test is not a chore after the work. It is the work's specification, in the one form a check can run.

Writing the test first has a second effect that no rule says out loud. A test can only be written first for code that is easy to call: a handler that takes a message and returns a `Result`, an engine behind a small protocol a fake can satisfy, a route that dispatches on the bus. Code that reaches into another feature's `internal/`, imports the framework, or does three things in one function is hard to put under a test before it exists. So the discipline pushes the design towards exactly the shape the walls (EIT001, EIT006) and the shape limit (40 lines, 5 parameters) ask for, without those rules being in the loop.

What it does not guarantee: a green suite proves the behaviours someone thought to write down. It does not prove the seam is in the right place or that the contract is thin. Eitri judges the walls and the budget; the review judges single purpose and readability. Tests cover the third thing, behaviour, and only as far as they are written.

## The three laws

1. **No production code until a unit test fails.** Not "a test exists": one that fails, for the reason you expect. A test that passes before the code is written tests nothing.
2. **No more of a test than is enough to fail.** A test that does not compile, or that fails on an import, already counts as failing. Stop there and make it pass.
3. **No more production code than is enough to pass the failing test.** Then go back to the first law.

The loop is seconds long, not hours. In this repo one turn looks like this:

```python
# tests/api/test_chunking.py  — written first; fails because /chunking/count does not exist
def test_count_route_counts_with_the_tokenizer(words_app) -> None:
    r = TestClient(words_app).post("/chunking/count", json={"text": "one two three"})
    assert r.status_code == 200 and r.json() == {"tokens": 3}
```

Run it, watch it fail with a 404, then add the smallest route that returns `{"tokens": 3}` for that input. Run again. Only now write the next test, the one for the domain rejection, and let it pull the error branch out of you. The route in [Adding a route](adding-a-route.md) is what three or four such turns leave behind.

⚠️ **Check:** when the code already exists, say so in the PR body and still write the test before changing anything. The first law then reads: no change to production code until a test fails that documents the change.

## Arrange, Act, Assert

Every test body has three parts, in this order, and nothing else:

1. **Arrange.** Put the world in the state the case needs: the app, the fake on the bus, the input.
2. **Act.** Do the one thing the test is about: dispatch the message, post the request, call the function.
3. **Assert.** State what must now be true.

```python
def test_blank_question_is_a_domain_rejection(fake) -> None:
    client, _ = fake                                                     # arrange

    r = client.post("/embeddings/query", json={"question": "   "})       # act

    assert r.status_code == 422 and r.json()["detail"] == "empty question"   # assert
```

The order is the point. A reader finds the input at the top, the call in the middle and the expectation at the bottom, every time, so no test has to be traced. The rules that follow from it:

- ✅ **One act.** Two calls in the act step is two tests, or one test of a sequence whose name says so (`test_second_split_reuses_the_cached_tokenizer`).
- ✅ **Arrange in fixtures when it repeats.** `fake`, `words_app` and `FakeEngine` in `tests/api/` are shared arrange steps. The test body keeps only what makes this case different: the blank question, the over-long sentence.
- ❌ **No assert before the act.** Checking a precondition on the arranged state is a smell: either the fixture is untrustworthy, which is its own test, or the assert is noise.
- ❌ **No arrange after the act.** Setting up more state after the first call means the test has two scenarios in it. Split it.
- ✅ **Blank lines between the three steps** when the test is longer than a few lines. Comments naming the steps are optional; the layout should make them obvious without.

## Clean tests

Tests are code and they rot like code. A suite nobody can read is a suite nobody dares to change, and a suite nobody dares to change gets deleted the first time the production code moves. If you do not keep the tests clean, you lose them, and with them the only safety net the next session has.

Readability is the whole of test cleanliness. A reader should see, without scrolling, **what** was set up, **what** was done, and **what** is expected. Everything else is noise. Push setup into a fixture or a small builder (`FakeEngine`, `fake_chunking`, the `fake` fixture in `tests/api/test_embeddings.py`), and let the test body be the three lines that state the case.

- ✅ **The name is the sentence the test proves.** `test_blank_question_is_a_domain_rejection`, `test_an_over_long_sentence_never_merges_into_a_neighbours_chunk`. When it fails, the name alone says what broke.
- ✅ **Build a small domain language for the tests** when the raw API is noisy. `words = lambda s: len(s.split())` in the chunking tests reads better than the counter it stands for.
- ❌ **No logic in a test.** No `if`, no loops that compute the expected value, no helper that mirrors the implementation. A test that needs its own test is wrong.
- ❌ **No asserting on internals.** Assert through the contract: the `Result`, the problem document, what the fake engine was asked to encode. A test that reads a private attribute breaks on every refactor and proves nothing a consumer cares about.

## One concept per test

Each test verifies one concept, and its name says which. The number of `assert` lines is not the rule; the number of ideas is. Two asserts that together state one fact are fine:

```python
assert r.status_code == 200 and r.json() == {"tokens": 3}
```

Two asserts about two facts are two tests. If the question is embedded with the query prefix *and* a blank question is rejected, that is two names, two functions, two failures that each point at one thing. The symptom of a test with two concepts is a name with "and" in it, or a name so vague (`test_query`) that it could not say.

Keeping to one concept also keeps the function short, which is the same shape limit the hook enforces on production code. A test that needs forty lines is testing more than one thing or has setup that belongs in a fixture.

## F.I.R.S.T.

- **Fast.** The whole suite runs in seconds or nobody runs it before every change. That is why the model is never loaded in an ordinary test: `FakeEngine` and the word-counting `fake_chunking` stand in on the bus, through the contract, so the handlers and routes under test are real and only the weights are fake.
- **Independent.** A test sets up what it needs and leaves nothing behind. Each test gets its own `create_app()` through the fixtures. Never let one test depend on another having run, and never order them to make them pass.
- **Repeatable.** Same result on any machine, with or without network, with or without a GPU, at any time of day. Anything that reads the clock, the environment or a model cache is faked or injected.
- **Self-validating.** A test passes or fails. It does not print something for a human to inspect, and it does not log a warning instead of asserting. `filterwarnings = ["error"]` in `pyproject.toml` makes the warning case concrete: a warning *is* a failure.
- **Timely.** Written just before the production code that makes them pass. Tests written after the fact find the code hard to test, and the fix at that point is a refactor nobody budgeted for. Written first, the test shapes the code while it is still cheap to shape.

## Where a test lives and what it reaches

| What you changed | Test file | Reaches | Fakes |
|---|---|---|---|
| A pure function in `internal/` (the chunker) | `tests/api/test_<feature>.py` | the function directly | the counter it takes as an argument |
| A handler | same file | `Handler(fake_engine, bus)(message)` and the `Result` | the engine; other features through their contract on the bus |
| A route | same file | `TestClient(app).post(...)` and the response | the real handler over a fake engine, registered with `replace=True` |
| An error path | same file | status, media type, problem `type` | as above; see [Returning errors](returning-errors.md) |
| The walls themselves | `tests/api/test_architecture.py` | `eitri.analyze` and the AST | nothing |

A test may import a feature's `internal/` to construct the handler it is testing. Nothing in `src/` may, and a test of feature A never imports feature B's `internal/`: it fakes B on the bus, through B's contract, exactly as A sees it. That is the seam the test proves.

## Checklist before you finish

✅ **Verify each item.** The unchecked boxes below are requirements, not a record of checks already run.

- [ ] Every behaviour the change adds or alters has a test that failed before the change and passes after it.
- [ ] Each test proves one concept and is named as the sentence it proves; no "and" in a name, no `if` or loop in a body.
- [ ] Each test body reads Arrange, Act, Assert, with one act and no setup after it.
- [ ] Expensive components are faked on the bus with `replace=True`; no test loads the model or touches the network.
- [ ] Tests assert through the contract: `Result`, response status, media type, problem `type`; nothing private.
- [ ] Error paths are asserted as problem documents, not only as a status code.
- [ ] `pytest` is green with warnings as errors; no test depends on another's side effects or on ordering.
