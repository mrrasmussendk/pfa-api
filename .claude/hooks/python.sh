#!/bin/sh
# The interpreter the hooks run with: the project's .venv when there is one (where `pip install -e ".[dev]" -e ./tools`
# put pytest, Eitri and Heimdall), else `python`, else `python3`. macOS ships only `python3`, so a bare `python`
# in the hook command fails before the harness can say anything.
root="${CLAUDE_PROJECT_DIR:-$(pwd)}"
for py in "$root/.venv/bin/python" "$root/.venv/Scripts/python.exe"; do
  [ -x "$py" ] && exec "$py" "$@"
done
command -v python >/dev/null 2>&1 && exec python "$@"
command -v python3 >/dev/null 2>&1 && exec python3 "$@"
echo "no Python for the hooks: create .venv as the README's Quick start says" >&2
exit 1
