#!/bin/bash
###############################################################################
# Docker Volume Restore Script
# Restores Docker volumes from a backup archive
###############################################################################

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

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

# Check if backup file is provided
if [[ $# -lt 1 ]]; then
  echo "Usage: $0 BACKUP_FILE.tar.gz"
  echo ""
  echo "Example:"
  echo "  $0 backups/mcp-playground-backup-2025-11-28-153000.tar.gz"
  exit 1
fi

BACKUP_FILE="$1"

# Validate backup file exists
if [[ ! -f "$BACKUP_FILE" ]]; then
  log_error "Backup file not found: $BACKUP_FILE"
  exit 1
fi

log_info "=== MCP Playground Volume Restore ==="
log_info "Backup file: $BACKUP_FILE"
echo ""

# Extract backup to temporary directory
TEMP_RESTORE_DIR=$(mktemp -d)
trap 'rm -rf "$TEMP_RESTORE_DIR"' EXIT

log_info "Extracting backup archive..."
tar xzf "$BACKUP_FILE" -C "$TEMP_RESTORE_DIR"
log_success "Backup extracted"

# Confirm with user
log_warning "This will REPLACE existing volume data!"
read -p "Are you sure you want to proceed? (yes/no): " -r
if [[ ! $REPLY =~ ^[Yy][Ee][Ss]$ ]]; then
  log_info "Restore cancelled"
  exit 0
fi

echo ""

# Restore each volume
for volume_dir in "$TEMP_RESTORE_DIR"/*; do
  if [[ ! -d "$volume_dir" ]]; then
    continue
  fi
  
  volume_name=$(basename "$volume_dir")
  backup_data="$volume_dir/data.tar.gz"
  
  if [[ ! -f "$backup_data" ]]; then
    log_warning "No backup data found for $volume_name, skipping"
    continue
  fi
  
  log_info "Restoring volume: $volume_name"
  
  # Create volume if it doesn't exist
  if ! docker volume inspect "$volume_name" &> /dev/null; then
    log_info "Creating volume: $volume_name"
    docker volume create "$volume_name"
  fi
  
  # Restore volume data
  if docker run --rm \
    -v "${volume_name}:/target" \
    -v "${backup_data}:/backup/data.tar.gz:ro" \
    busybox \
    sh -c "cd /target && rm -rf ./* && tar xzf /backup/data.tar.gz" 2>&1 | grep -v "tar: removing leading"; then
    log_success "Restored $volume_name"
  else
    log_error "Failed to restore $volume_name"
    exit 1
  fi
done

echo ""
log_success "=== Restore Complete ==="
log_info "All volumes have been restored from backup"
log_info "You can now start your services with:"
echo "  docker compose --profile cpu up -d"
