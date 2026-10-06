#!/usr/bin/env bash
# The static checks and the suites as one command.
#   scripts/check.sh           ruff · pytest · vitest · tsc -b · tsc-e2e   (about 140 s)
#   scripts/check.sh ruff …    ruff over the Python tree, args forwarded (`--fix`, `--statistics`)
#
# Ruff rules: cockpit/server/pyproject.toml [tool.ruff], passed explicitly so
# scripts/ and tools/ (outside that project) get the same set.
#
# tsc-e2e type-checks the browser specs and both Playwright configs
# (cockpit/web/tsconfig.e2e.json: the app's strict options plus Node's types).
# `tsc -b`, and with it the client build, covers src/ only.
#
# Dead-code comb (not part of this command; run it by hand now and then):
# `uvx vulture cockpit/server/app scripts --min-confidence 60` (route handlers
# and pydantic fields are false positives; judge the rest by grep) and
# `cd cockpit/web && npx --yes knip` (`uv` as an "unlisted binary" is the e2e
# specs shelling out, a false positive).
set -u
cd "$(dirname "$0")/.."
PY="cockpit/server scripts tools"              # where ruff looks
RUFF=(uv run --project cockpit/server ruff check --config cockpit/server/pyproject.toml)

case "${1:-}" in
  # shellcheck disable=SC2086
  ruff)    shift; exec "${RUFF[@]}" "$@" $PY ;;
  "")      ;;
  *)       echo "usage: scripts/check.sh [ruff <args>]" >&2; exit 64 ;;
esac

T0=$SECONDS
step() {                                       # step <label> <command…>
  local label=$1; shift
  echo "== $label"
  "$@" || { echo "check: $label FAILED (${SECONDS}s in)" >&2; exit 1; }
}
# shellcheck disable=SC2086
step ruff    "${RUFF[@]}" $PY
step pytest  bash -c 'cd cockpit/server && uv run pytest -q'
step vitest  bash -c 'cd cockpit/web && npx vitest run --reporter=dot'
step tsc     bash -c 'cd cockpit/web && npx tsc -b'
step tsc-e2e bash -c 'cd cockpit/web && npx tsc -p tsconfig.e2e.json'

echo "check: PASS in $((SECONDS - T0))s"
