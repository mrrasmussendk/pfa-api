#!/bin/sh
# Runs `heimdall review --base origin/main` (Jev's review of the committed diff) with TYPESAFE_API_KEY taken from
# the macOS keychain. The key goes straight into the review's environment and is never printed, so it stays out of
# the agent's context. An exported TYPESAFE_API_KEY wins; TYPESAFE_KEYCHAIN_SERVICE names the keychain item.
# Exit codes are the review's: 0 approve, 1 request_changes, 2 escalate, 3 could not run.
set -u
root="${CLAUDE_PROJECT_DIR:-$(pwd)}"
service="${TYPESAFE_KEYCHAIN_SERVICE:-TYPESAFE_API_KEY}"
if [ -z "${TYPESAFE_API_KEY:-}" ]; then
  if ! TYPESAFE_API_KEY="$(security find-generic-password -s "$service" -w 2>/dev/null)"; then
    echo "no keychain item with service '$service': set TYPESAFE_KEYCHAIN_SERVICE to its name, or export TYPESAFE_API_KEY" >&2
    exit 3
  fi
  export TYPESAFE_API_KEY
fi
cd "$root" || exit 3
exec "$root/.claude/hooks/python.sh" -m heimdall review --base origin/main "$@"
