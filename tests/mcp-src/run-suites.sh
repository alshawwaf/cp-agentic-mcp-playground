#!/bin/sh
# tests/mcp-src/run-suites.sh: runs inside the throwaway test container that tests/mcp-src/run.sh starts
# (no network, read-only root, no capabilities; /src/work = the built mcp-src tree, read-only).
# Generates this run's test certificates in /tmp, then runs each suite and prints a summary.
# Usage: run-suites.sh [SUITE...]   (default: every *.test.mjs; SUITE = file name without .test.mjs)
set -u
HERE=$(cd "$(dirname "$0")" && pwd -P) || exit 2
export TEST_CERTS_DIR="${TEST_CERTS_DIR:-/tmp/certs}"
export MCP_SRC_DIR="${MCP_SRC_DIR:-/src/work}"
SUITE_TIMEOUT=${MCP_TEST_SUITE_TIMEOUT:-240}   # seconds per suite (a full run takes about 30 s)

[ -f "$MCP_SRC_DIR/packages/management/dist/index.js" ] || {
  echo "mcp-src tests: no build in $MCP_SRC_DIR (run tests/mcp-src/run.sh, which builds first)" >&2
  exit 2
}
node "$HERE/lib/certs.mjs" "$TEST_CERTS_DIR" || exit 2

suites=$*
if [ -z "$suites" ]; then
  for f in "$HERE"/*.test.mjs; do
    b=$(basename "$f" .test.mjs)
    suites="$suites $b"
  done
fi

rc=0
summary=
for s in $suites; do
  f="$HERE/$s.test.mjs"
  if [ ! -f "$f" ]; then
    echo "mcp-src tests: no suite named $s" >&2
    rc=2
    continue
  fi
  printf '\n'
  timeout "$SUITE_TIMEOUT" node "$f"
  src=$?
  if [ "$src" = 0 ]; then
    summary="$summary
PASS  $s"
  else
    case $src in 124 | 137 | 143) echo "FAIL  $s: did not finish within ${SUITE_TIMEOUT}s (exit $src)" ;; esac
    summary="$summary
FAIL  $s"
    [ "$rc" = 0 ] && rc=1
  fi
done
printf '\nmcp-src security tests (node %s):%s\n' "$(node --version)" "$summary"
exit "$rc"
