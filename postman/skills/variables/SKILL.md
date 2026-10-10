---
name: variables
description: How to parameterize a collection with variables and where each value belongs — a plain value goes in an environment or collection variable, a secret (token, password, API key) goes in the team's shared vault via `postman secret create` so its value never passes through the coding harness or lands in a committed file. Covers the variable scopes and their resolution order, reading and writing variables at runtime from pre-request and post-response scripts (`pm.environment`, `pm.collectionVariables`, `pm.variables`), and composing variables out of other variables. Use when the user asks where to put a base URL, token, or password, how to reference a secret without exposing it, how `{{variables}}` resolve, or how to set a value in a script. Covers `postman secret`.
---

# Variables

A variable lets a request name a value — `{{base_url}}`, `{{authToken}}` —
instead of hardcoding it, so one collection runs against staging and
production and a secret never has to be typed into a request. The only real
decision is **where the value lives**, and it turns on one question: is it a
secret?

## Core foundations
1. One collection, many targets. Parameterize everything that changes between
   environments so the same requests run everywhere — a diff in values, not in
   requests.
2. Where a value lives is a security decision, not a convenience one. A plain
   value is a variable; a secret belongs in the vault, nowhere else.
3. A secret's value should never reach this harness, the terminal, or a
   committed file. `type: secret` only hides it in the UI — the value still
   sits in clear text in the YAML. The vault is the only place it stays out of
   the repo. (ref: postman secret command) 
4. Resolution is narrowest-wins: `local (script) > data (iteration row) >
   environment > collection > global`. This is what lets a collection ship a
   default and an environment override just the few that differ.
5. Variables compose. A value can reference another (`usersUrl =
   {{base_url}}/users`), and a script can set one at runtime to chain requests
   (capture a token, use it on the next call). Resolution happens at send
   time, not at definition time.

## Non-secret variables

A base URL, a non-sensitive id, a feature flag, a default page size — store it
as a variable and commit it. Reference it as `{{key}}` in a URL, header, query
param, or body. Scope picks the file:

- **Collection variable** — travels with the collection; use it for a default
  that belongs to the API itself and rarely changes.
- **Environment variable** — swapped per target; use it for anything that
  differs between staging and production. One file per environment, selected
  at run time.


You can either directly edit into collection / environment file or use the cli commands to achieve the same.
```bash
postman environment new "Staging"
postman environment var set base_url https://staging.example.com \
  --environment "postman/environments/Staging.environment.yaml"
postman environment lint postman/environments --fail-severity warning
```

Set one at runtime from a script when you need to chain requests:

```javascript
// post-response: capture for later requests
pm.environment.set("authToken", pm.response.json().access_token);
// pre-request: read it back (any scope, resolution order applies)
const base = pm.variables.get("base_url");
```

Use `pm.environment`/`pm.collectionVariables` to target a scope, `pm.variables`
to read across all of them, and `pm.variables.set` for a local value that
lives only for the current run.

## Secret keys

Passport is how people, services, and agents call APIs without ever holding
the secret. `postman secret` is the vault side of the same idea: it stores and
links secrets without putting their values in agent context or repository
files. Run `postman secret -h` for the supported vault operations and flags.

A bearer token, password, API key, client secret, or signing key goes in the
team's **shared vault**:

```bash
# interactive masked prompt (default when stdin is a terminal)
postman secret create STRIPE_API_KEY --workspace <workspaceId>
```
By default only the creator of secret can view the secret. Others in workspace can resolve the secret in their runs but can not see the secret value.

You then link the secret to an environment variable to use it in a request —
the CLI writes the link for you from `secret create`'s output:

```bash
postman environment var set api_key --secret-id <secret-id> --vault-id <vault-id> \
  --environment "postman/environments/Staging.environment.yaml"
```

The variable now points at the vault instead of holding a value; the file
carries only the reference, never the secret:

```yaml
values:
  - key: api_key
    enabled: true
    type: secret
    description: API key
    secret: true
    source:
      provider: postman
      postman:
        type: cloud
        secretId: <secret-id>
        vaultId: <vault-id>
```
