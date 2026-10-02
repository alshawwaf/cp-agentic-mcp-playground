# shellcheck shell=sh
# tests/test-helpers.sh: logging and assertion helpers for the shell tests. POSIX sh, sourced.
#
# The assertions only count and print: they always return 0, so a caller that runs under
# "set -e" is never stopped by a failed assertion. print_test_summary returns 1 when an
# assertion failed. Nothing here starts, stops or removes anything.

TESTS_RUN=0
TESTS_PASSED=0
TESTS_FAILED=0

log_info() { printf '[INFO] %s\n' "$*"; }
log_success() { printf '[ OK ] %s\n' "$*"; }
log_error() { printf '[FAIL] %s\n' "$*"; }
log_warning() { printf '[WARN] %s\n' "$*"; }

_test_pass() {
  TESTS_RUN=$((TESTS_RUN + 1))
  TESTS_PASSED=$((TESTS_PASSED + 1))
  log_success "PASS: $1"
}
_test_fail() {
  TESTS_RUN=$((TESTS_RUN + 1))
  TESTS_FAILED=$((TESTS_FAILED + 1))
  log_error "FAIL: $1"
}

# assert_equals EXPECTED ACTUAL DESCRIPTION
assert_equals() {
  if [ "$1" = "$2" ]; then _test_pass "$3"; else _test_fail "$3 (expected '$1', got '$2')"; fi
  return 0
}

# assert_true EXIT_STATUS DESCRIPTION: passes when EXIT_STATUS is 0.
assert_true() {
  if [ "$1" -eq 0 ] 2>/dev/null; then _test_pass "$2"; else _test_fail "$2 (exit status $1)"; fi
  return 0
}

# print_test_summary: the counts; returns 1 when an assertion failed.
print_test_summary() {
  printf '\n========================================\n'
  printf 'TEST SUMMARY\n'
  printf '========================================\n'
  printf 'Tests run:    %s\n' "$TESTS_RUN"
  printf 'Tests passed: %s\n' "$TESTS_PASSED"
  printf 'Tests failed: %s\n' "$TESTS_FAILED"
  printf '========================================\n'
  if [ "$TESTS_FAILED" -eq 0 ]; then
    log_success "All tests passed."
    return 0
  fi
  log_error "Some tests failed."
  return 1
}
