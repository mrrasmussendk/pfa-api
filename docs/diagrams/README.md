# Documentation diagrams

[Documentation index](../README.md) · [Project overview](../../README.md)

The documentation embeds SVG images so readers can see the diagrams without Mermaid support. Each image has an editable `.mmd` source beside it. Shared diagrams are reused across pages, so one updated pair updates every reference.

## Diagram index

| Diagram | What it explains | View | Edit |
|---|---|---|---|
| Agent harness | Preparation, observation, feedback, verification, and review | [SVG](harness.svg) | [Mermaid](harness.mmd) |
| Feature context budget | What counts toward the source an agent needs to understand a feature | [SVG](context-budget.svg) | [Mermaid](context-budget.mmd) |
| Dependencies | Allowed imports between the HTTP layer, application, features, and kernel | [SVG](dependencies.svg) | [Mermaid](dependencies.mmd) |
| Startup and readiness | How liveness and readiness behave while the model loads | [SVG](readiness.svg) | [Mermaid](readiness.mmd) |
| Review pipeline | Inputs, fault checks, policy, verification, and review outputs | [SVG](review-flow.svg) | [Mermaid](review-flow.mmd) |
| CI build and publication | Checks, packaging, container verification, and image publication | [SVG](ci-build.svg) | [Mermaid](ci-build.mmd) |
| PR review outcomes | Skipped reviews, comments, requested changes, and human escalation | [SVG](ci-review.svg) | [Mermaid](ci-review.mmd) |

The [architecture reference](../architecture.md), [service reference](../service.md), and [tooling reference](../tooling.md) provide the surrounding explanation. The [workflow files](../../.github/workflows/) are the executable definition of CI.

## Updating a diagram

1. Edit the `.mmd` source, including its accessible title and description where present.
2. Render the matching `.svg` with a Mermaid renderer. Keep HTML labels disabled and use a white background.
3. Open the SVG and check that text is readable, labels fit, and arrows are clear. Retain the `viewBox` so the image scales with the page.
4. Commit the source and SVG together. If a file is renamed, update this index and every page that embeds or links to it.

The diagrams use native SVG text and contain no JavaScript, remote fonts, or external assets. Preserve those properties when regenerating them.
