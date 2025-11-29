# Scripts Directory

This directory contains operational scripts for managing the MCP Playground.

## Scripts

### Health & Monitoring

#### `health-check.sh`
Comprehensive health check for all services in the Docker Compose stack.

**Usage:**
```bash
./scripts/health-check.sh --profile cpu [--verbose]
```

**Features:**
- Checks 20+ services (n8n, Postgres, Ollama, Qdrant, all MCP servers)
- HTTP endpoint validation
- Database connectivity checks
- Color-coded output
- Exit codes for CI/CD integration

---

### Backup & Recovery

#### `backup-volumes.sh`
Automated backup of Docker volumes.

**Usage:**
```bash
# Backup all volumes
./scripts/backup-volumes.sh

# Selective backup
./scripts/backup-volumes.sh --volumes n8n_storage,postgres_storage

# Custom options
./scripts/backup-volumes.sh --output-dir /path/to/backups --retention-days 60
```

**Features:**
- Backs up all 7 Docker volumes
- Timestamped archives
- Automatic retention management
- Selective backup support

#### `restore-volumes.sh`
Restore Docker volumes from backup archives.

**Usage:**
```bash
./scripts/restore-volumes.sh backups/mcp-playground-backup-YYYY-MM-DD-HHMMSS.tar.gz
```

**Features:**
- Backup validation
- User confirmation before restore
- Per-volume restoration
- Progress reporting

---

### Configuration

#### `validate-env.sh`
Validates `.env` file configuration for production readiness.

**Usage:**
```bash
./scripts/validate-env.sh
```

**Checks:**
- Required variables are set
- Password strength (length, complexity)
- Default/weak password detection
- Encryption key length validation
- Email format validation
- Port number validity

---

### Provisioning

#### `n8n-provision.sh`
Automatically provisions n8n instance on first startup.

**Executed by:** `n8n-provision` Docker Compose service

**Actions:**
1. Waits for n8n to be healthy
2. Creates owner account via `/rest/owner/setup`
3. Installs `n8n-nodes-mcp` community package
4. Logs in and verifies installation

---

## Best Practices

- All scripts should be executable: `chmod +x scripts/*.sh`
- Run health checks regularly (e.g., via cron)
- Automate backups with scheduled cron jobs
- Validate environment before production deployment
- Review script logs for troubleshooting

## Related Documentation

- [Production Deployment Guide](../docs/PRODUCTION_DEPLOYMENT.md)
- [Backup & Recovery Guide](../docs/BACKUP_RECOVERY.md)
