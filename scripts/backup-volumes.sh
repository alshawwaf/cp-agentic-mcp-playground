#!/bin/bash
###############################################################################
# Docker Volume Backup Script
# Backs up all Docker volumes used by the MCP Playground
###############################################################################

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
BACKUP_DIR="${BACKUP_DIR:-$PROJECT_DIR/backups}"
TIMESTAMP=$(date +%Y-%m-%d-%H%M%S)
BACKUP_NAME="mcp-playground-backup-${TIMESTAMP}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"

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

# Parse arguments
SELECTIVE_BACKUP=""
while [[ $# -gt 0 ]]; do
  case $1 in
    --volumes)
      SELECTIVE_BACKUP="$2"
      shift 2
      ;;
    --output-dir)
      BACKUP_DIR="$2"
      shift 2
      ;;
    --retention-days)
      RETENTION_DAYS="$2"
      shift 2
      ;;
    --help|-h)
      echo "Usage: $0 [OPTIONS]"
      echo ""
      echo "Options:"
      echo "  --volumes NAMES       Comma-separated list of volumes to backup (default: all)"
      echo "  --output-dir DIR      Backup output directory (default: ./backups)"
      echo "  --retention-days N    Number of days to keep backups (default: 30)"
      echo ""
      echo "Example:"
      echo "  $0 --volumes n8n_storage,postgres_storage"
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      exit 1
      ;;
  esac
done

# Create backup directory
mkdir -p "$BACKUP_DIR"

# Define volumes to backup
if [[ -n "$SELECTIVE_BACKUP" ]]; then
  IFS=',' read -ra VOLUMES <<< "$SELECTIVE_BACKUP"
else
  VOLUMES=(
    "n8n_storage"
    "postgres_storage"
    "ollama_storage"
    "qdrant_storage"
    "open-webui"
    "flowise"
    "langflow"
  )
fi

log_info "=== MCP Playground Volume Backup ==="
log_info "Timestamp: $TIMESTAMP"
log_info "Backup directory: $BACKUP_DIR"
log_info "Volumes to backup: ${VOLUMES[*]}"
echo ""

# Create temporary backup directory
TEMP_BACKUP_DIR=$(mktemp -d)
trap 'rm -rf "$TEMP_BACKUP_DIR"' EXIT

# Backup each volume
for volume in "${VOLUMES[@]}"; do
  log_info "Backing up volume: $volume"
  
  # Check if volume exists
  if ! docker volume inspect "$volume" &> /dev/null; then
    log_warning "Volume $volume does not exist, skipping"
    continue
  fi
  
  # Create backup of this volume
  volume_backup_dir="$TEMP_BACKUP_DIR/$volume"
  mkdir -p "$volume_backup_dir"
  
  # Use a temporary container to copy volume data
  if docker run --rm \
    -v "${volume}:/source:ro" \
    -v "${volume_backup_dir}:/backup" \
    busybox \
    sh -c "cd /source && tar czf /backup/data.tar.gz ." 2>&1 | grep -v "tar: removing leading"; then
    log_success "Backed up $volume"
  else
    log_error "Failed to backup $volume"
    exit 1
  fi
done

echo ""
log_info "Creating consolidated backup archive..."

# Create final backup tarball
cd "$TEMP_BACKUP_DIR"
tar czf "${BACKUP_DIR}/${BACKUP_NAME}.tar.gz" ./*

if [[ $? -eq 0 ]]; then
  BACKUP_SIZE=$(du -h "${BACKUP_DIR}/${BACKUP_NAME}.tar.gz" | cut -f1)
  log_success "Backup created: ${BACKUP_DIR}/${BACKUP_NAME}.tar.gz (${BACKUP_SIZE})"
else
  log_error "Failed to create backup archive"
  exit 1
fi

# Cleanup old backups
log_info "Cleaning up backups older than ${RETENTION_DAYS} days..."
find "$BACKUP_DIR" -name "mcp-playground-backup-*.tar.gz" -type f -mtime +${RETENTION_DAYS} -delete 2>/dev/null || true
log_success "Cleanup complete"

echo ""
log_success "=== Backup Complete ===" 
log_info "Backup location: ${BACKUP_DIR}/${BACKUP_NAME}.tar.gz"
log_info "To restore this backup, run:"
echo "  ./scripts/restore-volumes.sh ${BACKUP_DIR}/${BACKUP_NAME}.tar.gz"
