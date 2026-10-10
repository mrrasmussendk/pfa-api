---
name: api-documentation
description: Generate filesystem-first agent friendly api documentation that you can share with your teammates without hassle. Use when the user asks to "publish API docs," "generate documentation for this API," "put this on the API Network," "share a docs link for this collection or spec," or "why do my docs look empty." 
---
The bootstrap skill is a precursor to this one — it scaffolds the project with the directories documentation is stored in.

# API Documentation
When working on any API task, the first step is to establish and capture the contract.
API documentation can be done in two predominant ways:
1. through a Postman collection,
2. with an OpenAPI spec

It is recommended to create both. They serve different, complementary use
cases, and the Postman CLI can generate one from the other. Use the artifact
that represents the current source of truth rather than recreating its
operations by hand. Postman collections are human-friendly and enable other
capabilities such as mocks, monitors, SDKs, and specifications. Collections
created or edited directly on disk use the v3 format described by
**collection-schema-v3**.

Specs are vendor-neutral, stay in your repo, and can be linted against governance rules (if any) set by your organization.

## Generate a collection from a specification

When an OpenAPI specification already defines the contract, generate a Postman
collection from it in one command:

```bash
postman spec generate collection <spec-path-or-id> -n "<collection-name>"
```

## Good practices for API design

See [reference/rest-api-best-practices.md](reference/rest-api-best-practices.md)
for the practices well-documented APIs tend to already follow: resource
naming, HTTP method/status-code usage, error response shape, versioning,
pagination, filtering, auth, idempotency, and backward compatibility. A
spec or collection that already follows these renders documentation with
nothing left to fix.

### Examples
Examples (in a Postman collection) are an excellent way to capture sample API responses. They are helpful because:
1. anyone can look at them to see how your API behaves,
2. they can be used to generate a mock from your collection in a single command.

## Workflow
1. Establish the contract - refer to best practices. Don't just accept the user's ask - fight for the right API design.
2. Choose the source of truth — a Postman collection, an OpenAPI specification,
   or both. Recommend both when their complementary uses benefit the task.
3. If the specification is authoritative, generate the collection with
   `postman spec generate collection` instead of manually duplicating its
   operations.
