---
name: datasets
description: Query CSV and JSON files, spreadsheet exports, and live databases (MySQL, PostgreSQL, SQL Server, or anything with a JDBC driver JAR) as one SQL surface; join across them; save a query as a named view so it can be rerun without re-pasting the SQL; then drive a collection run one iteration per row — feeding it the rows a query returns instead of a hardcoded data file — or read rows from scripts via `pm.datasets()`. Use when the user wants to run or loop a collection over rows of test data, parameterize a run from a CSV or spreadsheet or database table, query or join data across files and tables, save or rerun a query without pasting it again, use a query's result rows as the input for a run in place of hardcoded JSON or a data file, point Postman at a JDBC driver, work out where database credentials get stored, or names a Postman dataset or view. Covers `postman dataset` (`source`, `view`, `query`, `jdbc`) and `--iteration-data-dataset`/`--iteration-data-view`/`--dataset` on `collection run`.
---

# Datasets

## Required CLI version

This skill documents the dataset commands as they behave **after**
postman-cli#1323 (AUTO-987, the spurious `No authorization data found`
notice) and postman-cli#1340 (AUTO-999/AUTO-998, one datasource per
worksheet plus the help-text fixes). Both are needed; 1.62.0 has neither.
Check with `postman --version` before trusting the worksheet and
auth-notice rules below, and on an older CLI expect `source add --file
book.xlsx` to add a workbook as a single unqueryable source (in a cloud
dataset too), `source update --file book.xlsx` to be accepted and rewrite the
source to `format: csv`, and every logged-out `--iteration-data-dataset` run
to print the notice.

## Overview

A dataset is a Postman entity that names one or more *datasources* and
presents them as SQL tables. It lives either in a Postman workspace or in the
repository, and the same commands work on both — with one carve-out, a
spreadsheet source, covered below. It is not a data file — it is
a layer over data files and databases, and the value is in that layer:
heterogeneous
sources (a CSV and a Postgres table) become joinable in one query, and a
saved *view* turns a query into a named, reusable result set that a
collection run can iterate.

Local and cloud are the same commands. Every verb that acts on an *existing*
dataset — `get`, `query`, `delete`, and every `source` and `view`
subcommand — takes either a `.dataset.yaml` path or a cloud dataset id and
routes accordingly. Two verbs name a location instead of an existing
dataset: `list` takes a path or directory (its cloud form is
`-w <workspaceId>`), and `create` takes the path to write (its cloud form is
`-w` with no path, since the id does not exist yet). `jdbc inspect` takes
neither — it reads a driver JAR and touches no dataset at all.

There is no `dataset push` verb — but that does not mean local and cloud are
sealed off from each other. Datasets are a workspace entity, so `postman
workspace push` persists them in the bound workspace alongside collections,
environments and the rest, making that context available to future work and
for sharing with others. `postman workspace pull` brings them back down. Reach
for those when the whole repo should move; the `dataset` verbs below are for
working on one dataset in place.

## Core knowledge

- **Datasets are the current way to drive a run from data.** They supersede
  passing a flat file with `-d`/`--iteration-data`: a dataset gives the same
  row-per-iteration behaviour, and on top of it SQL to filter and shape rows,
  joins across several sources, a live database instead of an export, named
  views that can be rerun, and `pm.datasets()` access from scripts. Reach for
  a dataset by default when someone wants to run a collection over rows of
  data; `-d` remains available for a one-off file and stays the lighter option
  when nothing more is wanted.

- **A spreadsheet becomes one source per worksheet, not one source — and
  only in a local dataset.** The engine reads Excel and OpenDocument
  workbooks (`xlsx`, `xls`, `ods`) as well as CSV and JSON, and
  `source add --file book.xlsx` enumerates the sheets and adds each as its own
  datasource — matching what the Postman app does. There is no flag for
  picking a sheet, by design. Three boundaries come before anything else:

  - **Local datasets only.** Adding a workbook to a *cloud* dataset is
    refused with `SPREADSHEET_CLOUD_UNSUPPORTED`, with or without `--upload`:
    a cloud add posts one datasource with no worksheet selector and the cloud
    service is never handed the bytes to fan the workbook out itself, so it
    would persist exactly the unqueryable source the local path exists to
    prevent. CSV and JSON work on both. This is the carve-out to
    "local and cloud are the same commands" — put the workbook in a local
    `.dataset.yaml`.
  - **`xlsx`, `xls` and `ods` only.** `.xlsm`, `.xlsb` and `.numbers` are
    unmistakably workbooks that the engine cannot read, so extension
    inference refuses them with `WORKBOOK_FORMAT_UNSUPPORTED` rather than
    letting them fall through to the `csv` default and write a source that
    fails every query. Re-save as `.xlsx`, or pass `--format csv` only if the
    file really is delimited text.
  - **`-n` is optional for a workbook, and ignored.** It is required for
    every other source kind, but worksheet sources are named after their
    sheets, so a `-n` passed here changes nothing and earns a
    `NAME_UNUSED_FOR_SPREADSHEET` warning.

  Each source is named `source_<sheet>`, with anything outside
  `[a-zA-Z0-9_]` replaced by `_`, and `_2`/`_3` appended on collision —
  collision being judged on the engine's *table* key rather than on the
  literal name, so two sheets that normalise onto one table are given
  distinct names instead of registering one table twice and failing every
  query on the dataset. A workbook with People, Orders and "Sales Q3 2026"
  therefore gives `source_People`, `source_Orders` and
  `source_Sales_Q3_2026`. The `-n` you passed does **not** appear in them —
  the scheme matches the Postman app (and DCS server-side), so a workbook
  added from the CLI and the same one added from the app produce identical
  source names and survive a push/pull round trip.

  Those are *source* names, and a source name is not always a table name.
  **Take the queryable name from the command's own output instead of deriving
  it.** `source add` prints a line per sheet —
  `"source_People" (id: …) from worksheet "People"` — and appends
  `, queryable as "<table>"` whenever the table differs, so a `売上` sheet is
  reported as `source___`, queryable as `source`, and `FROM source` is the
  query. Under `--json` the same thing arrives as `sources[]`, one
  `{id, name, worksheet, table}` entry per worksheet and present for every
  workbook add however many sheets it found; `table` is the field a `FROM`
  clause takes. Still don't copy `name` into a `FROM` clause verbatim, and
  note that a `source_` prefix is correct here and wrong for a source you
  named yourself, so do not carry the habit across.

  **`source update` cannot make a worksheet source, and now refuses to try.**
  Both halves are hard refusals with a code, and nothing is written:
  `--file <workbook>` fails `SPREADSHEET_FILE_NOT_UPDATABLE` (`.xlsm`,
  `.xlsb` and `.numbers` included) and `--format xlsx|xls|ods` fails
  `SPREADSHEET_FORMAT_NOT_UPDATABLE`, because the command patches `format`
  and `location` field-wise and has no way to pin a worksheet. What is still
  *allowed* and still destructive is `update --file other.csv` on a source
  that already has a worksheet: that patch carries no `source_options`, so
  the stale `spreadsheet.worksheet` selector stays pinned to a file with no
  worksheets and the source stops being queryable (AUTO-1000). Remove the
  source and re-add the workbook with `source add`.

- **`--format` is not validated.** Its help text lists the real formats, but
  the flag accepts any string and writes it straight into the manifest, so a
  typo becomes a source that fails only later at query time. Pass it only to
  override inference deliberately; otherwise let the file extension speak.
- **A datasource's table name is its `name` *normalised*, not its `name`.**
  Before exposing a source as a table the engine lowercases the name,
  collapses every run of `_` to a single `_`, and strips leading and trailing
  `_`. So `-n users` really is `FROM users`, but `-n Sales__Q3_` is
  `FROM sales_q3` — and `FROM Sales__Q3_`, the name you passed and the name
  the CLI echoes back, fails `SQL_UNKNOWN_TABLE`. Case alone is safe (SQL
  identifiers are case-insensitive, so `FROM MixedCase` still finds
  `mixedcase`); underscore runs and edge underscores are not. Keep source
  names already-normalised and the two can never diverge. A workbook add
  reports each sheet's table itself (`queryable as …`, or `sources[].table`
  under `--json`); for anything else, on the federated path
  `SELECT name FROM sqlite_master WHERE type='table'` lists the real ones.
  **The CLI's own `-h` examples say `FROM source_users`, and they are
  wrong** — there is no `source_` prefixing for a source you named
  yourself, and `source_users` fails `SQL_UNKNOWN_TABLE`. Trust the normalised source
  name, not the example text.
- **Federated vs native is the central query decision.** With no `--source`,
  the query runs through a federated SQLite layer that can join across every
  *federatable* source in the dataset — which is all of them except JDBC, per
  the next rule. With `--source <name>`, it is sent to that one
  datasource in *its own SQL dialect* — which is the only thing that works
  for JDBC sources, and what you want for dialect-specific SQL
  (`now() - interval '1 day'`). `dataset query -s` and
  `dataset view create -s` take the same reference. They are different
  execution paths, not fallbacks for each other — adding or dropping
  `--source` to make a failing query work changes what the query *means*.
- **JDBC is the one source type that cannot federate.** A native
  `--type mysql|postgresql|sqlserver` source *does* join against a CSV in one
  federated query, which is the main reason to build a mixed dataset. A
  `--type jdbc` source does not appear in the federated layer at all: query
  it without `--source` and it fails `SQL_UNKNOWN_TABLE`, exactly as a
  misspelled table would. So a JDBC source cannot be joined to anything —
  if you need that join, add the database as its native type instead.
- **Local does not mean free, and the plan gate keys off source type, not
  dataset location.** CSV/JSON sources run fully offline, logged out. Any
  *database* source — including one inside a purely local YAML — forces
  authentication and an entitlement check: MySQL and PostgreSQL need a paid
  plan, and **JDBC and SQL Server need Enterprise**
  (`… data sources require an Enterprise plan.`, HTTP 402).
- **A view's result set is iteration data.** `collection run
  --iteration-data-dataset <pathOrId> --iteration-data-view <nameOrId>`
  runs one iteration per row, with each column bound as a variable
  (`{{name}}`). Both flags are required together, and the pair is mutually
  exclusive with `-d/--iteration-data`.
- **On a dataset run, `No authorization data found` means a source really
  does need auth.** A file-backed dataset needs none, and a logged-out run
  over one is silent. So if that line appears on an
  `--iteration-data-dataset` run, read it as signal: a **database** source is
  in play — `jdbc`, `mysql`, `postgres`/`postgresql` or `sqlserver`, and a
  `postmancloudfile` source is *not* one of those and does not trigger it —
  or you passed a cloud dataset id or a cloud collection, or the manifest
  could not be read, those last three all landing on the same fail-closed
  branch. What it is *not* is a verdict on the run — a plain
  `collection run` with no dataset flags still prints it whenever you are
  logged out, because it comes from the run command rather than anything
  dataset-related.
- **`--dataset <pathOrDir>` is the other consumption path** — repeatable,
  and it exposes datasets to scripts as `pm.datasets(<id>)` rather than
  driving iterations. Resolution is lazy: a run that never calls
  `pm.datasets` parses nothing and needs no auth. An unparseable YAML is
  skipped with a `[pm.datasets] skipped …` warning, not a failed run — so a
  silently absent dataset looks like a script bug.
- **Query parameters are positional.** `$1, $2` in the SQL, `-p` values in
  order. Views can be parameterized too, but a parameterized view **cannot**
  drive iteration data — `collection run` has nowhere to pass `-p`, and it
  fails as an opaque execution error. Keep iteration views parameter-free.
- **Dataset commands never touch `.postman/resources.yaml`.** Unlike mocks,
  there is no repo-level registry entry to commit or clean up — the
  `.dataset.yaml` and its `data_dir` are the whole artifact.
- **`data_dir` is not only your data.** The local engine writes its own state
  in there next to the copied files — `data.db`, `meta.db`, `daemon.log`,
  `config/`, and a uuid-named directory. Commit the manifest and the source
  files; ignore the rest, or the repo starts carrying a query cache and a log.
  `daemon.log` is also the first place to look when the engine itself, rather
  than a query, is what failed.

## Process

1. **Scaffold.** `postman dataset create ./postman/datasets/NAME/NAME.dataset.yaml
   --name "NAME"` writes a four-line manifest with a generated id and
   `data_dir: .resources`. The cloud form is `create --name "NAME" -w <workspaceId>`
   with no path.
2. **Attach sources.** `postman dataset source add -d <dataset> -n <table>
   --file ./users.csv` **copies** the file into `data_dir` — pass
   `--ref-only` to reference it in place instead — but note it records an
   **absolute** path, so a `--ref-only` dataset is machine-local and does not
   survive being committed and cloned elsewhere. Extensions
   pick the format only when `--format` is absent; contents are never
   sniffed, so a `.txt` holding JSON needs `--format json`. For a cloud
   dataset, `--file` alone registers a *local-filesystem* source read by the
   local engine; `--upload` is what actually puts the data in the cloud.
   Either way a cloud dataset takes csv and json only — a workbook is refused
   there.
3. **For a *JDBC* source, start from `jdbc inspect`.**
   `postman dataset jdbc inspect ./drivers/pg.jar` maps straight onto the
   `source add` flags: `suggestedUrlTemplate` → `--url-template`,
   `templateVariables` → `--var`, `connectionProperties` → `--prop`,
   `driverClass` → `--driver-class`. A second source on the same database
   reuses all of it with `--from-source <name>`. A connection test runs
   before the write, so a JDBC source that cannot connect is never persisted
   (`--no-test` opts out). A native `--type mysql|postgresql|sqlserver`
   source needs none of this — no driver JAR, no inspect step, just
   `--host/--port/--database/--user/--password` — and it is **not**
   connection-tested before the write, so run `source test` yourself after
   adding one.
4. **Explore with ad-hoc SQL before saving anything.**
   `postman dataset query <dataset> -q "SELECT …"`. Get the query right
   here — a view is just a query you have already proven.
5. **Save the query as a view.** `postman dataset view create -d <dataset>
   -n "Active" -q "SELECT …"`, then `view run "Active" -d <dataset>` to
   confirm the rows. This is the step that makes the dataset usable by a
   run.
6. **Drive the run.** `postman collection run <collection>
   --iteration-data-dataset <dataset> --iteration-data-view "Active"`.
   Confirm the iteration count matches the row count — that is the only
   proof the wiring works.

## Critical rules

1. **Secrets are only avoidable on the JDBC path, and that decides which
   source type to use.** A credential passed as a **literal** lands in three
   places: `ps` output, shell history, **and clear text in the dataset YAML**
   (or the cloud request body). The CLI warns about exactly those three, and
   only for literals. What differs by source type is whether there is an
   alternative:
   - **JDBC (`--var`, `--prop`): yes, and it avoids all three.**
     `--var name=vault:<vaultId>/<secretId>` stores a `{$vaultId,$secretId}`
     pointer and resolves it at query time, so the secret itself reaches none
     of the three — only the reference travels through argv. `--vars-file`
     reads the same values from a JSON file and `--vars-file -` from stdin,
     which is the one way to keep a value out of `ps` and shell history; it
     accepts vault refs too, and a *literal* passed that way still lands in
     the YAML in clear text. Local Vault secrets are not supported — Shared
     Vault only. A literal secret in `--url-template` is rejected outright: a
     literal has no `{{name}}` to route through `--var`, so nothing could
     mask it.
   - **Native `--type mysql|postgresql|sqlserver` (`--user`, `--password`):
     no.** The CLI says so on every write —
     `Secret references for database credentials are not yet supported by
     Postman CLI.` There is no vault form of these flags. The credentials
     land in the YAML in clear text or the source does not exist.

   So when credentials must not sit in a committed file, reach for
   `--type jdbc` with Vault refs rather than the native type for the same
   database. Otherwise treat that YAML as a secret-bearing file and keep it
   out of version control.
2. **"The dataset operation failed with a server error. Please retry." is
   usually not a server error and retrying will not help.** It is the
   generic wrapper over engine errors, including your SQL being wrong.
   Re-run with `--debug` to get the real code — `engineCode=SQL_UNKNOWN_TABLE`
   for a bad table name, and so on. Read that before changing anything.
3. **When a query fails, isolate the layer with
   `postman dataset source test -d <dataset> -n <source>`.** It opens and
   closes a real connection using the stored config, resolving Vault
   references the way a query would, which separates "the source is broken"
   from "the SQL is wrong". File and URL sources have no connection and
   report `SOURCE_NOT_TESTABLE` — that is the expected answer, not a fault.
4. **`--dataset` on a cloud collection does not scope access.** A script can
   call `pm.datasets(<anyId>)` for any dataset the logged-in session can
   read. Only run collections you trust against a logged-in cloud session.
5. **`dataset delete` is permanent and deliberately asymmetric** — it
   removes the manifest but preserves the source files, because references
   can cross directory boundaries. An empty `data_dir` is removed unless
   `--keep-resources` is passed. Confirm before running it on a cloud id,
   where there is no file left behind to recover from.
6. **Pass `--json` when parsing.** Both success and failure go to stdout, so
   `--json 2>/dev/null | jq .` works either way, and failures carry a stable
   `error.code` (`CONNECTION_FAILED`, `DRIVER_CLASS_AMBIGUOUS`,
   `CONFIG_VALUE_MISSING`, …) plus `remediation`. Branch on the code, never
   on the prose.

## Anti-patterns

- **Don't add a database source to make a demo "more realistic."** It
  converts a zero-setup offline dataset into one that needs login, a paid
  plan, network reachability, and (for JDBC/SQL Server) Enterprise. Use CSV
  unless the live data is the point.
- **Don't run `postman dataset list` with no arguments** expecting the local
  datasets. Bare `list` is the *cloud* form and errors without `-w` outside
  a Postman-managed project; pass a path or directory for local ones.
- **Don't hand-edit `.dataset.yaml` to add a source.** `source add` runs the
  connection test (JDBC), id generation, and secret validation that a
  hand-edited entry skips — and the resolver re-inspects every YAML
  precisely so a hand-edited file cannot bypass the source gate.

## Verification

A dataset is not working because `create` and `source add` exited 0 — those
prove only that the write went through: a manifest locally, a workspace
entity in the cloud, and on the JDBC path a connection that opened, unless
`--no-test` skipped that test. None of it proves a query returns rows. Run an actual query and state
the row count and columns you got back. For a run, state the iteration count
and confirm it equals the view's row count; three rows producing one
iteration means the view, not the collection, is what to look at. Say which
execution path ran (federated or `--source` native) and whether the dataset
was local or cloud — that determines whether the numbers reflect live data
or a copied snapshot in `data_dir`.
