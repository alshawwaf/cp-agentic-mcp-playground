#!/bin/bash
###############################################################################
# Integration Test Suite
# Tests the full Docker Compose stack
###############################################################################

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Source test helpers
# shellcheck source=test-helpers.sh
source "$SCRIPT_DIR/test-helpers.sh"

PROFILE="${PROFILE:-cpu}"
SKIP_CLEANUP="${SKIP_CLEANUP:-0}"

# Trap to ensure cleanup on exit
cleanup() {
  local exit_code=$?
  
  if [[ $SKIP_CLEANUP -eq 0 ]]; then
    cleanup_stack "$PROFILE"
  else
    log_warning "Skipping cleanup (SKIP_CLEANUP=1)"
  fi
  
  exit $exit_code
}

trap cleanup EXIT INT TERM

main() {
  log_info "=== Integration Test Suite ==="
  log_info "Profile: $PROFILE"
  echo ""
  
  cd "$PROJECT_DIR"
  
  # Test 1: Validate docker-compose.yml
  log_info "Test Group: Docker Compose Configuration"
  docker compose config --quiet
  assert_true $? "docker-compose.yml is valid"
  echo ""
  
  # Test 2: Build custom n8n image
  log_info "Test Group: Image Build"
  log_info "Building custom n8n image..."
  docker compose build n8n --quiet
  assert_true $? "Custom n8n image builds successfully"
  echo ""
  
  # Test 3: Start the stack
  log_info "Test Group: Stack Startup"
  log_info "Starting Docker Compose stack..."
  docker compose --profile "$PROFILE" up -d
  assert_true $? "Stack starts without errors"
  echo ""
  
  # Test 4: Wait for core services
  log_info "Test Group: Core Service Health"
  
  wait_for_container "postgres" 60
  assert_container_running "postgres"
  
  wait_for_container "n8n" 90
  assert_container_running "n8n"
  
  wait_for_http "http://localhost:5678/healthz" 120 "n8n"
  assert_http_ok "http://localhost:5678/healthz"
  
  # Test Postgres connectivity
  docker exec postgres pg_isready -U admin -d n8n > /dev/null 2>&1
  assert_true $? "PostgreSQL accepts connections"
  
  echo ""
  
  # Test 5: Ollama
  log_info "Test Group: Ollama Service"
  
  if [[ "$PROFILE" == "cpu" ]]; then
    wait_for_container "ollama-cpu" 60
    assert_container_running "ollama-cpu"
    
    sleep 10  # Give Ollama a moment to fully initialize
    docker exec ollama-cpu sh -c 'OLLAMA_HOST=http://127.0.0.1:11434 ollama list' > /dev/null 2>&1
    assert_true $? "Ollama API is responsive"
  fi
  
  echo ""
  
  # Test 6: AI Services (profile-dependent)
  if [[ "$PROFILE" == "cpu" ]]; then
    log_info "Test Group: AI Services"
    
    wait_for_container "open-webui" 60
    assert_container_running "open-webui"
    
    wait_for_container "langflow" 60
    assert_container_running "langflow"
    
    wait_for_http "http://localhost:3000" 90 "Open WebUI"
    assert_http_ok "http://localhost:3000"
    
    wait_for_http "http://localhost:7860" 90 "Langflow"
    assert_http_ok "http://localhost:7860"
    
    echo ""
  fi
  
  # Test 7: Flowise and Qdrant
  log_info "Test Group: Additional Services"
  
  wait_for_container "flowise" 60
  assert_container_running "flowise"
  
  wait_for_http "http://localhost:3001" 90 "Flowise"
  assert_http_ok "http://localhost:3001"
  
  wait_for_container "qdrant" 60
  assert_container_running "qdrant"
  
  wait_for_http "http://localhost:6333/healthz" 60 "Qdrant"
  assert_http_ok "http://localhost:6333/healthz"
  
  echo ""
  
  # Test 8: MCP Servers (sample of critical ones)
  log_info "Test Group: MCP Servers (Sample)"
  
  declare -a critical_mcp_servers=(
    "mcp-documentation:7300"
    "threat-emulation-mcp:7304"
    "spark-management-mcp:7306"
  )
  
  for server_info in "${critical_mcp_servers[@]}"; do
    IFS=':' read -r container port <<< "$server_info"
    
    wait_for_container "$container" 60
    assert_container_running "$container"
    
    wait_for_http "http://localhost:${port}" 30 "$container"
    assert_http_ok "http://localhost:${port}"
  done
  
  echo ""
  
  # Test 9: n8n provisioning (check if owner was created)
  log_info "Test Group: n8n Provisioning"
  
  # Check if n8n-provision container completed successfully
  local provision_status
  provision_status=$(docker inspect n8n-provision --format='{{.State.ExitCode}}' 2>/dev/null || echo "255")
  assert_equals "0" "$provision_status" "n8n provisioner completed successfully"
  
  echo ""
  
  # Print summary
  print_test_summary
}

main
