# Backup and Recovery Guide

This guide covers backup strategies, automation, and disaster recovery procedures for the Check Point Agentic MCP Playground.

---

## Table of Contents

- [Overview](#overview)
- [What Gets Backed Up](#what-gets-backed-up)
- [Backup Methods](#backup-methods)
- [Automated Backups](#automated-backups)
- [Restore Procedures](#restore-procedures)
- [Disaster Recovery](#disaster-recovery)
- [RTO and RPO](#rto-and-rpo)

---

## Overview

The MCP Playground stores data in **Docker volumes**. Regular backups are essential for:
- Protection against data loss
- Disaster recovery
- Migration to new infrastructure
- Development/testing data snapshots

---

## What Gets Backed Up

### Docker Volumes

| Volume | Contents | Priority |
|--------|----------|----------|
| `n8n_storage` | n8n workflows, credentials, settings | **Critical** |
| `postgres_storage` | PostgreSQL database (n8n backend) | **Critical** |
| `ollama_storage` | Downloaded LLM models | High |
| `qdrant_storage` | Vector database collections | High |
| `open-webui` | Chat history, user data | Medium |
| `flowise` | Flow configurations | Medium |
| `langflow` | Flow data | Medium |

### Configuration Files

**Also back up these files from your project directory:**
- `.env` (credentials) - **Store securely, never commit to git**
- `docker-compose.yml` (if customized)
- `n8n/backup/` (exported workflows/credentials)
- Custom scripts in `scripts/`

---

## Backup Methods

### Method 1: Automated Script (Recommended)

Use the provided backup script:

```bash
# Backup all volumes
../scripts/backup-volumes.sh

# Selective backup
../scripts/backup-volumes.sh --volumes n8n_storage,postgres_storage

# Custom output directory
../scripts/backup-volumes.sh --output-dir /mnt/backup

# Set retention period
../scripts/backup-volumes.sh --retention-days 60
```

**Output:**
```
backups/mcp-playground-backup-2025-11-28-153045.tar.gz
```

### Method 2: Manual Docker Volume Backup

Backup a single volume manually:

```bash
docker run --rm \
  -v n8n_storage:/source:ro \
  -v $(pwd)/backups:/backup \
  busybox \
  tar czf /backup/n8n-storage-$(date +%Y-%m-%d).tar.gz -C /source .
```

### Method 3: n8n Native Export

Export workflows and credentials directly from n8n:

```bash
# From inside n8n container
docker exec n8n n8n export:workflow --output=/backup/workflows --all
docker exec n8n n8n export:credentials --output=/backup/credentials --all
```

**Advantage**: Encrypted credentials remain encrypted in exports

---

## Automated Backups

### Cron Job Setup

**Daily backups at 2 AM:**

```bash
# Edit crontab
crontab -e

# Add this line
0 2 * * * /path/to/cp-agentic-mcp-playground/scripts/backup-volumes.sh --retention-days 30 >> /var/log/mcp-backup.log 2>&1
```

**Hourly backups (critical data only):**

```bash
0 * * * * /path/to/cp-agentic-mcp-playground/scripts/backup-volumes.sh --volumes n8n_storage,postgres_storage >> /var/log/mcp-backup-hourly.log 2>&1
```

### Off-Site Backups

**Sync to remote storage:**

```bash
#!/bin/bash
# backup-and-sync.sh

# Run backup
/path/to/scripts/backup-volumes.sh

# Sync to S3
aws s3 sync ./backups/ s3://my-backup-bucket/mcp-playground/ --storage-class GLACIER

# Or use rsync to remote server
rsync -avz ./backups/ backup-server:/backups/mcp-playground/
```

**Schedule with cron:**

```bash
0 3 * * * /path/to/backup-and-sync.sh >> /var/log/mcp-backup-sync.log 2>&1
```

### Backup Verification

**Test restore quarterly:**

```bash
# Script to verify backup integrity
#!/bin/bash
set -e

BACKUP_FILE="backups/mcp-playground-backup-latest.tar.gz"

echo "Testing backup integrity..."
tar tzf "$BACKUP_FILE" > /dev/null
echo "✓ Backup archive is valid"

echo "Testing extraction..."
TEMP_DIR=$(mktemp -d)
tar xzf "$BACKUP_FILE" -C "$TEMP_DIR"
echo "✓ Backup can be extracted"

rm -rf "$TEMP_DIR"
echo "✓ Backup verification complete"
```

---

## Restore Procedures

### Full Restore

**Prerequisites:**
- Existing stack must be stopped
- Backup file must be available

**Steps:**

```bash
# 1. Stop all services
docker compose --profile cpu down

# 2. (Optional) Remove existing volumes to ensure clean restore
docker volume rm n8n_storage postgres_storage ollama_storage qdrant_storage open-webui flowise langflow

# 3. Run restore script
../scripts/restore-volumes.sh backups/mcp-playground-backup-2025-11-28-153045.tar.gz

# 4. Start services
docker compose --profile cpu up -d

# 5. Verify health
../scripts/health-check.sh --profile cpu
```

### Selective Restore (Single Volume)

Restore only one volume manually:

```bash
# Stop service using the volume
docker compose stop n8n

# Remove existing volume
docker volume rm n8n_storage

# Create fresh volume
docker volume create n8n_storage

# Extract backup
tar xzf backups/mcp-playground-backup-2025-11-28-153045.tar.gz

# Restore specific volume
docker run --rm \
  -v n8n_storage:/target \
  -v $(pwd)/n8n_storage:/source:ro \
  busybox \
  sh -c "cd /target && tar xzf /source/data.tar.gz"

# Restart service
docker compose start n8n
```

### Point-in-Time Recovery

Restore to a specific backup:

```bash
# List available backups
ls -lh backups/

# Choose a backup
../scripts/restore-volumes.sh backups/mcp-playground-backup-2025-11-25-020000.tar.gz
```

---

## Disaster Recovery

### Scenario 1: Database Corruption

**Symptoms:**
- n8n won't start
- PostgreSQL errors in logs

**Recovery:**

```bash
# Stop services
docker compose down

# Restore PostgreSQL volume only
docker volume rm postgres_storage
docker volume create postgres_storage

# Extract just postgres from backup
tar xzf backups/latest-backup.tar.gz postgres_storage/data.tar.gz
docker run --rm \
  -v postgres_storage:/target \
  -v $(pwd)/postgres_storage:/source:ro \
  busybox \
  sh -c "cd /target && tar xzf /source/data.tar.gz"

# Restart
docker compose --profile cpu up -d
```

### Scenario 2: Complete Infrastructure Loss

**Recovery to new host:**

```bash
# On new host:
# 1. Install Docker and Docker Compose
# 2. Clone repository or copy application files
# 3. Copy .env file from secure storage
# 4. Copy latest backup

# Restore
../scripts/restore-volumes.sh /path/to/backup.tar.gz

# Start stack
docker compose --profile cpu up -d
```

### Scenario 3: Accidental Data Deletion

**Recovery:**

```bash
# Don't create new data - stop immediately
docker compose stop

# Restore from most recent backup
../scripts/restore-volumes.sh backups/mcp-playground-backup-latest.tar.gz

# Resume
docker compose start
```

---

## RTO and RPO

### Recovery Time Objective (RTO)

**Expected recovery times:**

| Scenario | RTO | Notes |
|----------|-----|-------|
| Single volume restore | 10-15 min | Depends on volume size |
| Full stack restore | 20-30 min | Includes service startup |
| New infrastructure rebuild | 1-2 hours | Install Docker + restore |

### Recovery Point Objective (RPO)

**Data loss tolerance:**

| Backup Frequency | RPO | Use Case |
|------------------|-----|----------|
| Hourly (critical volumes) | 1 hour | Production |
| Daily (full backup) | 24 hours | Development |
| Weekly | 7 days | Lab/testing |

**Optimize RPO:**
- Use n8n's built-in versioning for workflows
- Enable PostgreSQL WAL archiving for point-in-time recovery
- Frequency = balance between storage costs and acceptable data loss

---

## Best Practices

### DO:
- ✓ Test restores regularly (monthly minimum)
- ✓ Store backups off-site (different physical location)
- ✓ Encrypt backup archives for sensitive data
- ✓ Document restore procedures and keep updated
- ✓ Monitor backup job success/failure
- ✓ Verify backup integrity automatically

### DON'T:
- ✗ Store only one copy of backups
- ✗ Keep backups on same host/disk as production
- ✗ Neglect to test restore procedures
- ✗ Forget to back up `.env` file
- ✗ Ignore backup failures in logs

---

## Advanced Topics

### Incremental Backups

For large volumes, use incremental backups:

```bash
# First backup (full)
../scripts/backup-volumes.sh

# Subsequent backups (incremental with rsync)
rsync -avz --link-dest=/backups/previous \
  /var/lib/docker/volumes/ \
  /backups/incremental-$(date +%Y-%m-%d)
```

### Encrypted Backups

Encrypt backups at rest:

```bash
# Create encrypted backup
../scripts/backup-volumes.sh
gpg --symmetric --cipher-algo AES256 backups/latest-backup.tar.gz

# Decrypt for restore
gpg --decrypt backups/latest-backup.tar.gz.gpg > backup.tar.gz
../scripts/restore-volumes.sh backup.tar.gz
```

### Database-Specific Backups

PostgreSQL native backup (alternative method):

```bash
# Dump database
docker exec postgres pg_dump -U admin n8n > n8n-db-$(date +%Y-%m-%d).sql

# Restore
docker exec -i postgres psql -U admin n8n < n8n-db-2025-11-28.sql
```

---

## Monitoring Backups

### Check Backup Health

```bash
#!/bin/bash
# check-backup-age.sh

BACKUP_DIR="./backups"
MAX_AGE_HOURS=48

LATEST_BACKUP=$(ls -t $BACKUP_DIR/mcp-playground-backup-*.tar.gz | head -1)

if [[ -z "$LATEST_BACKUP" ]]; then
  echo "ERROR: No backups found!"
  exit 1
fi

BACKUP_AGE_HOURS=$(( ($(date +%s) - $(stat -c %Y "$LATEST_BACKUP")) / 3600 ))

if [[ $BACKUP_AGE_HOURS -gt $MAX_AGE_HOURS ]]; then
  echo "WARNING: Latest backup is $BACKUP_AGE_HOURS hours old (max: $MAX_AGE_HOURS)"
  exit 1
else
  echo "OK: Latest backup is $BACKUP_AGE_HOURS hours old"
  exit 0
fi
```

Add to monitoring/alerting system.

---

## Support

For backup/restore issues, check logs:

```bash
# Backup script logs
cat /var/log/mcp-backup.log

# Docker volume logs
docker volume inspect n8n_storage
```

Report issues with backup details and error messages.
