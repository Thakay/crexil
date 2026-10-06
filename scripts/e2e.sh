#!/usr/bin/env bash
# Run the browser suites offline, each run in fresh throwaway state.
#
#   scripts/e2e.sh [normal|preview|all] [playwright arguments...]
#
#   normal   the harness suite: scripted executors, the normal build
#   preview  the crexil journeys against the isolated preview build
#   all      both, normal first (the default); both run even if one fails
#
# CREXIL_E2E_PORT picks the server port (default 8123). Every run gets a new
# folder from mktemp (CREXIL_E2E_DIR), removed after a passing run unless
# CREXIL_E2E_KEEP=1; a failing run keeps it for inspection. Where the kernel
# allows unprivileged user namespaces, the suites run in a network namespace
# with only loopback, so no request can leave the machine and two runs can
# share a port. Elsewhere they run without that guard, and say so.
# Needs `uv sync` in cockpit/server and `npm ci` in cockpit/web first.
set -euo pipefail
cd "$(dirname "$0")/.."

mode="${1:-all}"
[[ $# -gt 0 ]] && shift
case "$mode" in
    normal|preview|all) ;;
    *) echo "usage: scripts/e2e.sh [normal|preview|all] [playwright arguments...]" >&2; exit 2 ;;
esac

export CREXIL_E2E_PORT="${CREXIL_E2E_PORT:-8123}"
export CREXIL_E2E_DIR
CREXIL_E2E_DIR="$(mktemp -d "${TMPDIR:-/tmp}/crexil-e2e.XXXXXX")"
# The servers start with `uv run`; offline, a stale environment fails loudly
# instead of reaching for the network.
export UV_OFFLINE=1

isolated=0
if unshare --user --map-root-user --net true 2>/dev/null; then
    isolated=1
else
    echo "e2e: no network namespace here; the suites run without the offline guard" >&2
fi

run_suite() {  # $1 = config file, then playwright arguments
    local config="$1"
    shift
    if [[ $isolated == 1 ]]; then
        unshare --user --map-root-user --net bash -c \
            'ip link set lo up && cd cockpit/web && exec npx playwright test -c "$0" "$@"' \
            "$config" "$@"
    else
        (cd cockpit/web && npx playwright test -c "$config" "$@")
    fi
}

echo "e2e: $mode on port $CREXIL_E2E_PORT, state in $CREXIL_E2E_DIR"
status=0
if [[ $mode == normal || $mode == all ]]; then
    (cd cockpit/web && npm run build)
    run_suite playwright.config.ts "$@" || status=1
fi
if [[ $mode == preview || $mode == all ]]; then
    run_suite playwright.preview.config.ts "$@" || status=1
fi

if [[ $status == 0 && "${CREXIL_E2E_KEEP:-}" != 1 ]]; then
    rm -rf "$CREXIL_E2E_DIR"
else
    echo "e2e: run folder kept at $CREXIL_E2E_DIR"
fi
exit "$status"
