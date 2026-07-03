#!/bin/bash
###############################################################################
# Health Check Script for Check Point Agentic MCP Playground
# 
# This script validates that all services in the Docker Compose stack are
# healthy and responding correctly.
#
# Usage:
#   ./scripts/health-check.sh [--profile cpu|gpu-nvidia] [--timeout 300]
#
# Exit Codes:
#   0 - All services healthy
#   1 - One or more services unhealthy
#   2 - Script error (invalid arguments, etc.)
###############################################################################

set -euo pipefail

# Default configuration
PROFILE="${PROFILE:-cpu}"
TIMEOUT=300
VERBOSE=0
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Parse arguments
while [[ $# -gt 0 ]]; do
  case $1 in
    --profile)
      PROFILE="$2"
      shift 2
      ;;
    --timeout)
      TIMEOUT="$2"
      shift 2
      ;;
    --verbose|-v)
      VERBOSE=1
      shift
      ;;
    --help|-h)
      echo "Usage: $0 [--profile cpu|gpu-nvidia] [--timeout SECONDS] [--verbose]"
      exit 0
      ;;
    *)
      echo "Unknown argument: $1"
      exit 2
      ;;
  esac
done

# Logging functions
log_info() {
  echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
  echo -e "${GREEN}[✓]${NC} $1"
}

log_warning() {
  echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
  echo -e "${RED}[✗]${NC} $1"
}

log_verbose() {
  if [[ $VERBOSE -eq 1 ]]; then
    echo -e "${BLUE}[DEBUG]${NC} $1"
  fi
}

# Health check functions
check_service_running() {
  local service_name=$1
  local container_name=$2
  
  log_verbose "Checking if $service_name is running..."
  
  if docker ps --format '{{.Names}}' | grep -q "^${container_name}$"; then
    log_success "$service_name is running"
    return 0
  else
    log_error "$service_name is NOT running"
    return 1
  fi
}

check_http_endpoint() {
  local service_name=$1
  local url=$2
  local expected_status=${3:-200}
  
  log_verbose "Checking HTTP endpoint: $url"
  
  if command -v curl &> /dev/null; then
    local status_code
    status_code=$(curl -s -o /dev/null -w "%{http_code}" "$url" 2>/dev/null || echo "000")
    
    if [[ "$status_code" == "$expected_status" ]] || [[ "$status_code" =~ ^2[0-9][0-9]$ ]]; then
      log_success "$service_name HTTP endpoint is healthy ($url - $status_code)"
      return 0
    else
      log_error "$service_name HTTP endpoint is unhealthy ($url - $status_code)"
      return 1
    fi
  else
    log_warning "curl not available, skipping HTTP check for $service_name"
    return 0
  fi
}

check_postgres() {
  log_verbose "Checking PostgreSQL..."
  
  if docker exec postgres pg_isready -U admin -d n8n &> /dev/null; then
    log_success "PostgreSQL is healthy"
    return 0
  else
    log_error "PostgreSQL is unhealthy"
    return 1
  fi
}

check_ollama() {
  local container_name="ollama-${PROFILE}"
  if [[ "$PROFILE" == "gpu-nvidia" ]]; then
    container_name="ollama-gpu"
  fi
  
  log_verbose "Checking Ollama ($container_name)..."
  
  if docker exec "$container_name" sh -c 'OLLAMA_HOST=http://127.0.0.1:11434 ollama list' &> /dev/null; then
    log_success "Ollama is healthy"
    return 0
  else
    log_error "Ollama is unhealthy"
    return 1
  fi
}

check_qdrant() {
  log_verbose "Checking Qdrant..."
  
  if check_http_endpoint "Qdrant" "http://localhost:6333/healthz" 200; then
    return 0
  else
    # Fallback to collections endpoint
    if check_http_endpoint "Qdrant" "http://localhost:6333/collections" 200; then
      return 0
    fi
    return 1
  fi
}

# Main health check
main() {
  log_info "Starting health check for Check Point Agentic MCP Playground"
  log_info "Profile: $PROFILE | Timeout: ${TIMEOUT}s"
  echo ""
  
  local failed_checks=0
  local start_time=$(date +%s)
  
  # Change to project directory
  cd "$PROJECT_DIR"
  
  # Core services
  log_info "=== Core Services ==="
  
  check_service_running "PostgreSQL" "postgres" || ((failed_checks++))
  check_postgres || ((failed_checks++))
  
  check_service_running "n8n" "n8n" || ((failed_checks++))
  check_http_endpoint "n8n" "http://localhost:5678/healthz" || ((failed_checks++))
  
  # Check Ollama (profile-specific)
  if [[ "$PROFILE" == "cpu" ]]; then
    check_service_running "Ollama (CPU)" "ollama-cpu" || ((failed_checks++))
  elif [[ "$PROFILE" == "gpu-nvidia" ]]; then
    check_service_running "Ollama (GPU)" "ollama-gpu" || ((failed_checks++))
  fi
  check_ollama || ((failed_checks++))
  
  echo ""
  log_info "=== AI Services ==="
  
  if [[ "$PROFILE" == "cpu" ]]; then
    check_service_running "Open WebUI" "open-webui" || ((failed_checks++))
    check_http_endpoint "Open WebUI" "http://localhost:3000" || ((failed_checks++))
    
    check_service_running "Langflow" "langflow" || ((failed_checks++))
    check_http_endpoint "Langflow" "http://localhost:7860" || ((failed_checks++))
  fi
  
  check_service_running "Flowise" "flowise" || ((failed_checks++))
  check_http_endpoint "Flowise" "http://localhost:3001" || ((failed_checks++))
  
  check_service_running "Qdrant" "qdrant" || ((failed_checks++))
  check_qdrant || ((failed_checks++))
  
  echo ""
  log_info "=== MCP Servers ==="
  
  # MCP servers with their ports
  declare -A mcp_servers=(
    ["mcp-documentation"]="7300"
    ["mcp-https-inspection"]="7301"
    ["mcp-quantum-management"]="7302"
    ["mcp-management-logs"]="7303"
    ["threat-emulation-mcp"]="7304"
    ["threat-prevention-mcp"]="7305"
    ["spark-management-mcp"]="7306"
    ["reputation-service-mcp"]="7307"
    ["harmony-sase-mcp"]="7308"
    ["quantum-gw-cli-mcp"]="7309"
    ["quantum-gw-connection-analysis-mcp"]="7310"
    ["quantum-gaia-mcp"]="7311"
    ["cpinfo-analysis-mcp"]="7312"
  )
  
  for server in "${!mcp_servers[@]}"; do
    port="${mcp_servers[$server]}"
    check_service_running "$server" "$server" || ((failed_checks++))
    check_http_endpoint "$server" "http://localhost:${port}" || ((failed_checks++))
  done
  
  echo ""
  log_info "=== Health Check Summary ==="
  
  local end_time=$(date +%s)
  local duration=$((end_time - start_time))
  
  if [[ $failed_checks -eq 0 ]]; then
    log_success "All services are healthy! (completed in ${duration}s)"
    exit 0
  else
    log_error "$failed_checks check(s) failed (completed in ${duration}s)"
    exit 1
  fi
}

# Run main function
main
