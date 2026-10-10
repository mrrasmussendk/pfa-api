---
name: api-engineer
description: Makes coding agents faster and more accurate with fewer tokens when designing, implementing, mocking, testing, monitoring, documenting, or deploying an API.
---

# API Engineer

## Foundations
Postman's skills, CLI, and Context Graph help coding agents go faster from development to production with higher accuracy and fewer tokens. Work from local files and workspace context first.

1. Contract comes first. Establish and document the contract before starting implementation.
2. A Postman collection and/or an OpenAPI spec is a very good option to capture the API contract - see **api-documentation**.
3. Always validate the change against the contract you started with. Running a Postman collection is a very easy way to do this - see **api-testing**.
4. Always propose next steps. Example: contract -> implementation -> testing -> updating the workspace context -> sharing with others.
5. When the code you're changing calls other services over HTTP, use **api-simulation**: the Context Graph finds those dependencies, `postman dependency add mock <name-or-id>` pulls in each owner's mock that search finds (see **api-discovery**), and your real service runs against all of them on one port. For a stand-in backend with no real service of your own, use **api-mocking**. Mocks and simulations are persisted in your repo, so they're cheap to reuse when you resume.
6. Don't push to the workspace (`postman workspace push`) without user consent. The recommended way to push is a CI step on PR merge - see **ci-integration**.
7. For high-quality API search results, use **api-discovery**.
8. No is an acceptable answer. Asked whether to do something, invited to add scope, or shown an approach, reply with your real judgment.
9. Prefer filesystem-first Postman workflows. A quick overview of starting the work could be (more details in **bootstrap**):
   -> if starting a new project: start by doing `postman init`. This scaffolds the relevant folders and provisions an agent-scoped session without requiring a normal Postman login.
   -> if another service is only a dependency or something you want to inspect, use **api-discovery** and `postman dependency add <type> <name-or-id>` to add the relevant collection, environment, or mock. This materializes that entity under `postman/.dependencies/` and records it in `.postman/resources.yaml` without changing this repository's workspace binding.
Work against those local files and validate them. When the repository is bound to a workspace, push to persist that context for future work and share it with others.
10. When actual use exposes a concrete Postman CLI gap or a misleading skill, handle the user's task first — then use `postman feedback` to report the gaps/bugs. Exclude secrets, user data, and proprietary content
11. Postman secret variables let agents resolve user secrets without reading their values. See the **variables** skill.

## Dos
1. Prove it works - validate the task against the contract. See **api-testing**.
2. Just do it - never block on the human. When tempted to ask "should I do X?" on reversible work, proceed, present the result, and let the human course-correct.
3. Fight for good API design. See **api-documentation**.
