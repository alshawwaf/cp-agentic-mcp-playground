#!/bin/bash
###############################################################################
# Test Helper Functions
# Shared utilities for integration tests
###############################################################################

# Colors
export RED='\033[0;31m'
export GREEN='\033[0;32m'
export YELLOW='\033[1;33m'
export BLUE='\033[0;34m'
export NC='\033[0m'

# Test counters
export TESTS_RUN=0
export TESTS_PASSED=0
export TESTS_FAILED=0

# Logging functions
log_info() {
  echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
  echo -e "${GREEN}[✓]${NC} $1"
}

log_error() {
  echo -e "${RED}[✗]${NC} $1"
}

log_warning() {
  echo -e "${YELLOW}[WARNING]${NC} $1"
}

# Test assertion functions
assert_equals() {
  local expected=$1
  local actual=$2
  local test_name=$3
  
  ((TESTS_RUN++))
  
  if [[ "$expected" == "$actual" ]]; then
    log_success "PASS: $test_name"
    ((TESTS_PASSED++))
    return 0
  else
    log_error "FAIL: $test_name (expected: '$expected', got: '$actual')"
    ((TESTS_FAILED++))
    return 1
  fi
}

assert_true() {
  local condition=$1
  local test_name=$2
  
  ((TESTS_RUN++))
  
  if [[ $condition -eq 0 ]]; then
    log_success "PASS: $test_name"
    ((TESTS_PASSED++))
    return 0
  else
    log_error "FAIL: $test_name"
    ((TESTS_FAILED++))
    return 1
  fi
}

assert_container_running() {
  local container_name=$1
  local test_name="Container '$container_name' is running"
  
  ((TESTS_RUN++))
  
  if docker ps --format '{{.Names}}' | grep -q "^${container_name}$"; then
    log_success "PASS: $test_name"
    ((TESTS_PASSED++))
    return 0
  else
    log_error "FAIL: $test_name"
    ((TESTS_FAILED++))
    return 1
  fi
}

assert_http_ok() {
  local url=$1
  local test_name="HTTP endpoint $url returns 2xx"
  
  ((TESTS_RUN++))
  
  local status_code
  status_code=$(curl -s -o /dev/null -w "%{http_code}" "$url" 2>/dev/null || echo "000")
  
  if [[ "$status_code" =~ ^2[0-9][0-9]$ ]]; then
    log_success "PASS: $test_name (status: $status_code)"
    ((TESTS_PASSED++))
    return 0
  else
    log_error "FAIL: $test_name (status: $status_code)"
    ((TESTS_FAILED++))
    return 1
  fi
}

# Wait for service to be ready
wait_for_service() {
  local service_name=$1
  local check_command=$2
  local timeout=${3:-120}
  local interval=${4:-5}
  
  log_info "Waiting for $service_name (timeout: ${timeout}s)..."
  
  local elapsed=0
  while [[ $elapsed -lt $timeout ]]; do
    if eval "$check_command" &> /dev/null; then
      log_success "$service_name is ready (${elapsed}s)"
      return 0
    fi
    sleep $interval
    elapsed=$((elapsed + interval))
  done
  
  log_error "$service_name failed to become ready within ${timeout}s"
  return 1
}

wait_for_http() {
  local url=$1
  local timeout=${2:-120}
  local service_name=${3:-"HTTP endpoint"}
  
  wait_for_service "$service_name" "curl -sf '$url' > /dev/null 2>&1" "$timeout"
}

wait_for_container() {
  local container_name=$1
  local timeout=${2:-60}
  
  wait_for_service "Container $container_name" "docker ps --format '{{.Names}}' | grep -q '^${container_name}$'" "$timeout"
}

# Print test summary
print_test_summary() {
  echo ""
  echo "========================================"
  echo "TEST SUMMARY"
  echo "========================================"
  echo "Tests run:    $TESTS_RUN"
  echo "Tests passed: $TESTS_PASSED"
  echo "Tests failed: $TESTS_FAILED"
  echo "========================================"
  
  if [[ $TESTS_FAILED -eq 0 ]]; then
    log_success "All tests passed!"
    return 0
  else
    log_error "Some tests failed!"
    return 1
  fi
}

# Cleanup function
cleanup_stack() {
  local profile=${1:-cpu}
  
  log_info "Cleaning up Docker Compose stack..."
  docker compose --profile "$profile" down -v 2>/dev/null || true
  log_success "Cleanup complete"
}
