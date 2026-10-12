---
name: heimdall-review
description: Run Jev's review (heimdall review --base origin/main) of the committed branch, with the API key from the macOS keychain, and act on its verdict.
disable-model-invocation: true
allowed-tools: Bash(.claude/skills/heimdall-review/review.sh:*)
---

# Heimdall review

The review of the committed diff against `origin/main`, the same one the `Stop` hook runs last. Its key comes from the
keychain inside `review.sh` and never reaches this conversation. Only the user starts this skill: it reads a secret.

## The review's output

!`.claude/skills/heimdall-review/review.sh 2>&1; echo "review exit code: $?"`

## What to do with it

| Exit code | Verdict | Do |
|---|---|---|
| 0 | approve | Say so in one line. |
| 1 | request_changes | Fix each failing check or below-target file it names, following [What the hooks tell you](../../../harness/guides/hook-feedback.md): split the function, add the why, handle the error; never raise a target. Run the repo's checks, commit, and tell the user to run `/heimdall-review` again. |
| 2 | escalate | Report what the judge wants a human to look at; change nothing. |
| 3 | could not run | Report the reason. If no key was found, say to set `TYPESAFE_KEYCHAIN_SERVICE` to the keychain item's name (default `TYPESAFE_API_KEY`) or export `TYPESAFE_API_KEY`. Do not look up the key yourself. |

The review judges committed work only: uncommitted changes are invisible to it.
