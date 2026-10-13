# Project documentation

[Project overview and quick start](../README.md)

These pages explain how PFA API works and why it is structured this way. For instructions on making a change, use the [harness guides](../harness/README.md).

## Suggested reading order

1. **Try the service.** Start with the [README demonstration](../README.md#try-the-demo), then use the [service reference](service.md) to understand its contract and runtime behaviour.
2. **Follow a request.** The [architecture walkthrough](architecture.md#one-request-end-to-end) traces a similarity request from the HTTP route through the feature contracts and back.
3. **Inspect the development process.** [Development tooling](tooling.md) explains how architecture rules, tests, agent feedback, and review fit together.
4. **Check the evidence.** Compare the [behaviour scenarios](../tests/e2e/), their [Postman requests](../postman/collections/e2e/), and the [CI workflow](../.github/workflows/ci.yml) with the claims in the documentation.

## Reference pages

| Reference | Questions it answers |
|---|---|
| [Service](service.md) | What can the API do? How are requests validated, errors returned, models configured, and the service deployed? |
| [OpenAPI contract](openapi.json) | What are the exact request and response schemas? What contract is checked for compatibility? |
| [Architecture](architecture.md) | Where does a change belong? How do features communicate? Why are HTTP adapters separate from feature logic? |
| [Development tooling](tooling.md) | What do Eitri, Brokkr, Heimdall, and automated review check? How do scenarios and CI verify a change? |
| [Token estimator calibration](calibration.md) | How can the context-budget estimate be compared with a tokenizer, and what accuracy is targeted? |
| [Logging decision](decisions/0001-logging.md) | Why use RFC 5424 records, RFC 3339 timestamps, and W3C Trace Context? |
| [Diagrams](diagrams/README.md) | Where are the visual explanations and their editable Mermaid sources? |

## Keep the documentation aligned

Update the relevant reference when behaviour or design changes. Keep task instructions in [harness/](../harness/README.md), and link to the explanation here instead of repeating it. Document additional architectural decisions in [decisions/](decisions/).

The saved OpenAPI contract is generated from the application. After a route schema changes, run `python tools/openapi_compat.py --write` from the repository root. Update a diagram's Mermaid source and rendered SVG together; the [diagram guide](diagrams/README.md#updating-a-diagram) explains how.

The default test suite checks relative Markdown links and heading anchors, so renamed sections and moved files must have their incoming links updated as well.
