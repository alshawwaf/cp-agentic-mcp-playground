#!/bin/bash
###############################################################################
# Environment Validation Script
# Validates .env file configuration
###############################################################################

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="${ENV_FILE:-$PROJECT_DIR/.env}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

WARNINGS=0
ERRORS=0

log_info() {
  echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
  echo -e "${GREEN}[✓]${NC} $1"
}

log_error() {
  echo -e "${RED}[✗]${NC} $1"
  ((ERRORS++))
}

log_warning() {
  echo -e "${YELLOW}[WARNING]${NC} $1"
  ((WARNINGS++))
}

# Check if .env exists
if [[ ! -f "$ENV_FILE" ]]; then
  log_error ".env file not found at: $ENV_FILE"
  echo ""
  echo "Run ./setup.sh to generate a .env file"
  exit 1
fi

log_info "=== Environment Validation ==="
log_info "Validating: $ENV_FILE"
echo ""

# Source the .env file
set -a
# shellcheck source=/dev/null
source "$ENV_FILE"
set +a

# Required variables
REQUIRED_VARS=(
  "POSTGRES_USER"
  "POSTGRES_PASSWORD"
  "POSTGRES_DB"
  "N8N_ENCRYPTION_KEY"
  "N8N_USER_MANAGEMENT_JWT_SECRET"
  "N8N_ADMIN_EMAIL"
  "N8N_ADMIN_PASSWORD"
  "N8N_BASIC_AUTH_USER"
  "N8N_BASIC_AUTH_PASSWORD"
)

log_info "Checking required variables..."
for var in "${REQUIRED_VARS[@]}"; do
  if [[ -z "${!var:-}" ]]; then
    log_error "Required variable $var is not set"
  else
    log_success "$var is set"
  fi
done

echo ""

# Validate password strength
log_info "Validating password strength..."

check_password_strength() {
  local var_name=$1
  local password="${!var_name:-}"
  
  if [[ -z "$password" ]]; then
    return
  fi
  
  # Check for default/weak passwords
  if [[ "$password" == "change_me" ]] || [[ "$password" == "admin" ]] || [[ "$password" == "password" ]]; then
    log_error "$var_name uses a default/weak password: '$password'"
    return
  fi
  
  # Check minimum length (12 characters recommended)
  if [[ ${#password} -lt 12 ]]; then
    log_warning "$var_name is shorter than 12 characters (${#password} chars)"
  else
    log_success "$var_name has adequate length"
  fi
}

check_password_strength "POSTGRES_PASSWORD"
check_password_strength "N8N_ADMIN_PASSWORD"
check_password_strength "N8N_BASIC_AUTH_PASSWORD"
check_password_strength "N8N_ENCRYPTION_KEY"
check_password_strength "N8N_USER_MANAGEMENT_JWT_SECRET"

echo ""

# Validate encryption keys
log_info "Validating encryption keys..."

if [[ ${#N8N_ENCRYPTION_KEY} -lt 32 ]]; then
  log_error "N8N_ENCRYPTION_KEY should be at least 32 characters (current: ${#N8N_ENCRYPTION_KEY})"
else
  log_success "N8N_ENCRYPTION_KEY has adequate length"
fi

if [[ ${#N8N_USER_MANAGEMENT_JWT_SECRET} -lt 32 ]]; then
  log_error "N8N_USER_MANAGEMENT_JWT_SECRET should be at least 32 characters (current: ${#N8N_USER_MANAGEMENT_JWT_SECRET})"
else
  log_success "N8N_USER_MANAGEMENT_JWT_SECRET has adequate length"
fi

echo ""

# Validate email format
log_info "Validating email addresses..."

if [[ "$N8N_ADMIN_EMAIL" =~ ^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$ ]]; then
  log_success "N8N_ADMIN_EMAIL format is valid"
else
  log_error "N8N_ADMIN_EMAIL format is invalid: $N8N_ADMIN_EMAIL"
fi

echo ""

# Port validation
log_info "Validating port numbers..."

validate_port() {
  local var_name=$1
  local port="${!var_name:-}"
  
  if [[ -z "$port" ]]; then
    log_error "$var_name is not set"
    return
  fi
  
  if [[ ! "$port" =~ ^[0-9]+$ ]] || [[ "$port" -lt 1 ]] || [[ "$port" -gt 65535 ]]; then
    log_error "$var_name has invalid port number: $port"
  else
    log_success "$var_name is valid: $port"
  fi
}

validate_port "N8N_PORT"
validate_port "POSTGRES_PORT"
validate_port "OLLAMA_PORT"
validate_port "FLOWISE_PORT"
validate_port "LANGFLOW_PORT"
validate_port "OPEN_WEBUI_PORT"
validate_port "QDRANT_PORT"

echo ""

# Summary
log_info "=== Validation Summary ==="
echo "Errors:   $ERRORS"
echo "Warnings: $WARNINGS"
echo ""

if [[ $ERRORS -gt 0 ]]; then
  log_error "Validation failed with $ERRORS error(s)"
  exit 1
elif [[ $WARNINGS -gt 0 ]]; then
  log_warning "Validation completed with $WARNINGS warning(s)"
  exit 0
else
  log_success "All validation checks passed!"
  exit 0
fi
