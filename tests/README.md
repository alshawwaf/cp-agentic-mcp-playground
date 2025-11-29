# Tests Directory

This directory contains the automated test suite for the MCP Playground.

## Test Files

### `test-helpers.sh`
Shared test utilities and helper functions.

**Exports:**
- Logging functions: `log_info`, `log_success`, `log_error`, `log_warning`
- Assertion functions: `assert_equals`, `assert_true`, `assert_http_ok`, `assert_container_running`
- Wait helpers: `wait_for_service`, `wait_for_http`, `wait_for_container`
- Test reporting: `print_test_summary`
- Cleanup: `cleanup_stack`

**Usage:**
```bash
# Source in test scripts
source ./tests/test-helpers.sh

# Use assertions
assert_container_running "n8n"
assert_http_ok "http://localhost:5678/healthz"
```

---

### `integration-test.sh`
Comprehensive integration test suite for the Docker Compose stack.

**Usage:**
```bash
# Run all tests
./tests/integration-test.sh

# Test specific profile
PROFILE=cpu ./tests/integration-test.sh

# Skip cleanup (for debugging)
SKIP_CLEANUP=1 ./tests/integration-test.sh
```

**Test Coverage:**
- Docker Compose configuration validation
- Custom n8n image build
- Stack startup
- Core services: Postgres, n8n, Ollama
- AI services: Open WebUI, Langflow, Flowise, Qdrant
- MCP servers (13 services)
- n8n provisioning verification

**Exit Codes:**
- `0` - All tests passed
- `1` - One or more tests failed

---

## Running Tests

### Local Testing

1. **Make scripts executable:**
   ```bash
   chmod +x tests/*.sh
   ```

2. **Run integration tests:**
   ```bash
   ./tests/integration-test.sh
   ```

3. **Review output:**
   - Test progress is logged in real-time
   - Summary shows passed/failed counts
   - Failed tests include error details

### CI/CD Testing

Tests run automatically via GitHub Actions on:
- Push to `main` or `develop` branches
- Pull requests to `main`
- Manual workflow dispatch

See [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) for CI configuration.

---

## Test Output Example

```
=== Integration Test Suite ===
Profile: cpu

Test Group: Docker Compose Configuration
[✓] PASS: docker-compose.yml is valid

Test Group: Image Build
[✓] PASS: Custom n8n image builds successfully

Test Group: Stack Startup
[✓] PASS: Stack starts without errors

Test Group: Core Service Health
[✓] PASS: Container 'postgres' is running
[✓] PASS: Container 'n8n' is running
[✓] PASS: HTTP endpoint http://localhost:5678/healthz returns 2xx (status: 200)
[✓] PASS: PostgreSQL accepts connections

... (additional tests)

========================================
TEST SUMMARY
========================================
Tests run:    42
Tests passed: 42
Tests failed: 0
========================================
[✓] All tests passed!
```

---

## Writing New Tests

To add new tests:

1. **Source test helpers:**
   ```bash
   source ./tests/test-helpers.sh
   ```

2. **Use assertions:**
   ```bash
   # Test container is running
   assert_container_running "my-service"
   
   # Test HTTP endpoint
   assert_http_ok "http://localhost:8080"
   
   # Custom assertions
   assert_equals "expected" "actual" "Test description"
   ```

3. **Wait for services:**
   ```bash
   wait_for_container "my-service" 60
   wait_for_http "http://localhost:8080" 120 "My Service"
   ```

4. **Print summary:**
   ```bash
   print_test_summary
   ```

---

## Troubleshooting

### Tests Fail Locally

1. **Ensure stack is stopped:**
   ```bash
   docker compose --profile cpu down -v
   ```

2. **Clean Docker system:**
   ```bash
   docker system prune -af
   ```

3. **Run tests with verbose output:**
   ```bash
   set -x
   ./tests/integration-test.sh
   ```

### Tests Pass Locally but Fail in CI

- Check GitHub Actions logs for specific errors
- Verify CI environment has sufficient resources
- Ensure timeout values are appropriate for CI environment

---

## Related Documentation

- [CI/CD Workflow](../.github/workflows/ci.yml)
- [Main README](../README.md)
