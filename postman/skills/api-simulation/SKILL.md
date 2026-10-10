---
name: api-simulation
description: The local development loop for a service that calls other APIs — find its dependencies in the code and the Context Graph, give each one a mock (the owning team's, or one generated from its contract), serve them together from one `.sim.yaml` on one port, run the real service against it, read the simulation's log to see what each dependency served, and ship it with the simulation in the PR. Can also inject latency, error, rate-limit or chaos per dependency. Use when the user is building or changing an endpoint that calls other services, or asks to "simulate my dependencies," "mock everything this service calls," or "run this end to end locally." Covers `postman simulation`. Builds on api-discovery for finding dependencies and their owners' mocks, and on api-mocking for the rest.
---

# API Simulation

## Overview

A simulation serves several mocks on one local port. Each member has a
**routeKey**, its address: with `routing: path` (the default) a dependency
lives at `http://localhost:<port>/<routeKey>`, and the routeKey is stripped
before the mock sees the request. The service under test runs for real and is
never a member.

```yaml
# postman/simulations/feeds-dev.sim.yaml
simulation: feeds-dev
routing: path
mocks:
  - routeKey: acs
    path: ../mocks/acs/config.yaml
  - routeKey: workspaces
    path: ../.dependencies/mocks/workspace-service/config.yaml
```

Running a local `.sim.yaml` works signed out. Running one by id, the Context
Graph, `search`, `dependency` and anything in a workspace need `postman login`.
`postman simulation` needs Postman CLI 1.70.0 or later (`postman update`);
earlier releases have only the older `postman simulate run`.

## Commands

Most commands work either on a local `.sim.yaml` or on a simulation in a
workspace:

| Command | With a `.sim.yaml` path | With a simulation id |
| --- | --- | --- |
| `create -n <name> --mock <ref>=<routeKey>` | Writes `postman/simulations/<name>.sim.yaml` from mock paths | Creates one in the workspace from mock ids (`-w`). Don't mix the two |
| `list` | Lists `postman/simulations/` | `-w <workspaceId>` lists the workspace's |
| `get <pathOrId>` | Reads the file | Fetches it |
| `run <pathOrId>` | Serves the members on one port | Fetches the members, then serves them locally |
| `push <path>` | Uploads each member mock, then the simulation, to the linked workspace. A member pulled with `dependency add` goes up as a copy | — |
| `deploy <id>` | — | Serves it at a URL, **public by default** (rule 7). `--private` requires an API key |
| `delete <pathOrId>` | Deletes the file, not its mocks | Deletes it and takes its URL down |

## Process

1. **Find what the change calls.** If a `.sim.yaml` in `postman/simulations/`
   already covers the change, skip to step 4. Otherwise read the code path
   you're changing: its HTTP clients, their base-URL settings, and any call
   the change adds. Only HTTP dependencies become members; queues, databases
   and caches run as real local instances. When signed in, check that list
   against the Context Graph, which also sees calls made through shared code
   and other repositories:
   `postman context-graph ask "What services and APIs does <repo-name> call?" --wait`.
   Use the repository's name from `git remote get-url origin`, not the
   folder's: a wrong name answers "no dependencies", not an error. A call the
   change adds isn't in the graph yet.

2. **Get each dependency's mock from its owner first.** This needs
   `postman login`; signed out, ask the user to sign in rather than writing
   mocks (rule 5). For every dependency, in this order:
   1. Find the owner's workspace:
      `postman context-graph ask "Which Postman workspace holds the collections and mocks for <service>?" --wait`.
      If the workspace it names has neither, find the owner's with
      `postman search workspaces "<service>"`.
   2. List that workspace's mocks:
      `postman search mocks --filter "workspaceId=<id>"`. A name match in
      another workspace is not ownership.
   3. If the owner has one, pull it with `postman dependency add mock <mockId>`
      and use the copy as it is, even if the owner also deployed it.
   4. Only if the owner has none, make one: pull their collection with
      `postman dependency add collection <id>` and run `postman mock generate`
      on it (see `api-mocking`). Keep the endpoints this path calls and answer
      `501` for the rest, so a missed call fails loudly. Make it stateful when
      the change writes something and reads it back.

3. **Compose the simulation.**

   ```bash
   postman simulation create -n feeds-dev \
     --mock ./postman/mocks/acs=acs \
     --mock ./postman/.dependencies/mocks/workspace-service=workspaces
   ```

   Each `=<routeKey>` names a member: lowercase letters, digits, `-` and `_`.
   Schema: [reference/sim_yaml_schema.md](reference/sim_yaml_schema.md).

4. **Run it and point the service at it.**

   ```bash
   postman simulation run postman/simulations/feeds-dev.sim.yaml --port 4900 --output ndjson > sim.ndjson 2> sim.err &
   ```

   Keep stderr out of `sim.ndjson` so every line parses; start-up errors land
   in `sim.err`. It's up when the `listening` event lists each member's
   address. Set each upstream base URL to `http://localhost:4900/<routeKey>`,
   never to a mock server's URL.

   Stop it with
   `pkill -INT -f "simulation run postman/simulations/feeds-dev.sim.yaml"`:
   every member stops, and the closing `summary` event counts the requests
   served and failed. `kill $!` reaches only the `postman` launcher, which
   ignores it, so the simulation keeps serving. In a terminal of its own,
   Ctrl+C works too.

5. **Develop and test, reading the simulation log after each call.** Call
   the changed route on the real service with `postman request` or its
   collection (see `api-testing`). Every request a member served is a
   `request` event in `sim.ndjson`, with its `routeKey`, `method`, `path`,
   `statusCode`, `duration` in milliseconds, and both bodies. Check that each
   upstream call you expect is there, on the right member, with the status you
   expect. Restart the service after a code change, and the simulation after a
   mock edit. A response from the simulation itself means wiring, not code:
   - `404 Unknown simulation member`: the path has no valid routeKey. The body
     lists the valid ones. It isn't logged.
   - `501`: an upstream call you haven't modelled yet. Add it to your own
     mock. For a pulled one, fork it into `postman/mocks/` first.
   - `502 Proxy error`: the member's handler crashed. See `sim.ndjson`.

6. **Optionally, fail a dependency.** Worth doing when the change has to
   survive an upstream that errors or is slow, but not required. Copy the
   baseline `.sim.yaml`, give that member a condition, run the service
   against it, and report what it did.

   ```yaml
     - routeKey: workspaces
       path: ../.dependencies/mocks/workspace-service/config.yaml
       scenarios:
         - overrides:
             conditions:
               error: { status_code: 503 }
   ```

   The conditions are `latency`, `error`, `rate_limit` and `chaos`; the schema
   has all four. Each applies to every route on that member, and the first to
   fail a request ends it, so fail a dependency one way at a time. An injected
   error, `429` or chaos failure never reaches the mock and isn't logged, so
   judge it by what the service returned.

7. **Ship.** Commit your mocks, the `.sim.yaml` files and
   `.postman/resources.yaml`, but not `sim.ndjson`: it records request
   headers and bodies, tokens included. After a clone,
   `postman dependency install` restores the pulled mocks. Running the
   simulation in CI is optional: start it as in step 4 before the test step,
   and stop it after.

## Critical Rules

1. **The service under test is never a member.** If nothing real runs, use
   `api-mocking`.
2. **Unique routeKeys and an explicit `--port`.** A member's own `port:` is
   ignored. Without `--port`, a busy 3000 silently moves to a random port.
3. **Members share one `pm.state` store.** Two stateful mocks that use the
   same key read and overwrite each other's records, so give each its own keys.
4. **Only `overrides.conditions` inject faults.** A bare
   `scenarios: - path: …/default.js` entry, as the Postman app writes, adds
   none. `overrides.bypass` is ignored.
5. **Never write or generate a mock for a dependency before searching its
   owner's workspace.** A mock you write encodes your assumptions; the
   owner's encodes their contract and their state.
6. **Pull an owner's mock even when it's deployed.** Only members take faults
   and log requests, and a private mock server needs an `x-api-key` your
   service won't send. Never edit `postman/.dependencies/`:
   `dependency update` overwrites it. To change a pulled mock, copy it into
   `postman/mocks/` and call it a fork.
7. **Confirm before a public deploy.** `simulation deploy` without
   `--private`, or with `-y`, serves every member's mock to anyone with the
   URL. Ask the user first; a local `run` needs no deploy at all.

## Verification

Starting is not the same as being used. Before you report, read the whole
log: every member on the path should have `request` events with the statuses
you expect. A member with none isn't wired or isn't on this path.

Report:

- **How the simulation performed:** for each member, the requests it served
  and their statuses, and anything unexpected, such as a `501`, a `502` or a
  member that was never called.
- **Where each mock came from:** for each dependency, the owner workspace you
  searched, what you found there, and whether its mock was pulled, forked or
  generated here.
- **What ran for real.**
