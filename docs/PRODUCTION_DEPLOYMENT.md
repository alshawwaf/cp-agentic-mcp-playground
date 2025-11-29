# Production Deployment Guide

This guide covers deploying the Check Point Agentic MCP Playground in a production environment.

> [!WARNING]
> **This stack was designed for development and lab environments.** For production use, additional hardening and infrastructure changes are required.

---

## Table of Contents

- [Prerequisites](#prerequisites)
- [Security Hardening](#security-hardening)
- [SSL/TLS Configuration](#ssltls-configuration)
- [Secret Management](#secret-management)
- [Scaling Considerations](#scaling-considerations)
- [High Availability](#high-availability)
- [Monitoring](#monitoring)
- [Maintenance](#maintenance)

---

## Prerequisites

### Infrastructure Requirements

- **Host OS**: Linux (Ubuntu 22.04 LTS or RHEL 9 recommended)
- **Docker**: Docker Engine 24.0+ 
- **Docker Compose**: v2.20+
- **CPU**: Minimum 8 cores (16+ recommended for production load)
- **RAM**: Minimum 16GB (32GB+ recommended)
- **Storage**: 
  - 100GB+ for Docker volumes
  - SSD recommended for database performance
- **Network**: Static IP address, firewall configuration

### Optional (GPU Deployment)

- NVIDIA GPU with 8GB+ VRAM
- NVIDIA Container Toolkit installed
- CUDA 12.0+ compatible drivers

---

## Security Hardening

### 1. Environment Configuration

**Never use default credentials in production!**

```bash
# Generate strong secrets
./setup.sh

# Validate configuration
./scripts/validate-env.sh
```

Key requirements:
- All passwords must be **32+ characters** and randomly generated
- Use a password manager or secrets vault
- Never commit `.env` to version control

### 2. Network Isolation

**Recommended network topology:**

```
Internet → Reverse Proxy (nginx/Traefik) → Docker Network → Services
```

**Firewall rules:**
- Only expose reverse proxy ports (80, 443) to public internet
- All other service ports (5678, 3000, etc.) should be **internal only**
- Use Docker network isolation (`demo` network is internal by default)

**Update docker-compose.yml for production:**

```yaml
services:
  n8n:
    ports:
      # REMOVE this in production, use reverse proxy instead
      # - "${N8N_PORT}:5678"
    expose:
      - "5678"  # Internal only
```

### 3. Basic Auth & Access Control

n8n's basic auth is enabled by default. For production:

- Use **strong** `N8N_BASIC_AUTH_PASSWORD` (32+ chars)
- Consider implementing additional authentication layers (OAuth, SAML)
- Use n8n's built-in user management for team access

### 4. API Key Protection

MCP servers require sensitive API keys. Best practices:

- Use Docker secrets (see [Secret Management](#secret-management))
- Rotate API keys regularly (every 90 days minimum)
- Audit API key usage via MCP server logs

### 5. Container Security

**Scan images for vulnerabilities:**

```bash
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
  aquasec/trivy image custom-mcp-n8n:custom
```

**Run containers as non-root** (already configured in base images)

**Enable Docker Content Trust:**

```bash
export DOCKER_CONTENT_TRUST=1
```

---

## SSL/TLS Configuration

### Option 1: Nginx Reverse Proxy

Create `nginx/nginx.conf`:

```nginx
upstream n8n {
    server localhost:5678;
}

server {
    listen 443 ssl http2;
    server_name n8n.example.com;

    ssl_certificate /etc/nginx/ssl/cert.pem;
    ssl_certificate_key /etc/nginx/ssl/key.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    location / {
        proxy_pass http://n8n;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # WebSocket support for n8n
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}

# Redirect HTTP to HTTPS
server {
    listen 80;
    server_name n8n.example.com;
    return 301 https://$server_name$request_uri;
}
```

Run nginx with Docker:

```bash
docker run -d \
  --name nginx-proxy \
  --network demo \
  -p 80:80 \
  -p 443:443 \
  -v $(pwd)/nginx/nginx.conf:/etc/nginx/nginx.conf:ro \
  -v $(pwd)/nginx/ssl:/etc/nginx/ssl:ro \
  nginx:alpine
```

### Option 2: Traefik (Automatic Let's Encrypt)

Add `docker-compose.production.yml`:

```yaml
version: '3.8'

services:
  traefik:
    image: traefik:v2.10
    command:
      - "--providers.docker=true"
      - "--entrypoints.web.address=:80"
      - "--entrypoints.websecure.address=:443"
      - "--certificatesresolvers.letsencrypt.acme.email=admin@example.com"
      - "--certificatesresolvers.letsencrypt.acme.storage=/letsencrypt/acme.json"
      - "--certificatesresolvers.letsencrypt.acme.httpchallenge.entrypoint=web"
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - "/var/run/docker.sock:/var/run/docker.sock:ro"
      - "./letsencrypt:/letsencrypt"
    networks:
      - demo

  n8n:
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.n8n.rule=Host(`n8n.example.com`)"
      - "traefik.http.routers.n8n.entrypoints=websecure"
      - "traefik.http.routers.n8n.tls.certresolver=letsencrypt"
```

Deploy:

```bash
docker compose -f docker-compose.yml -f docker-compose.production.yml up -d
```

---

## Secret Management

### Option 1: Docker Secrets (Swarm Mode)

```yaml
secrets:
  postgres_password:
    external: true
  n8n_encryption_key:
    external: true

services:
  postgres:
    secrets:
      - postgres_password
    environment:
      - POSTGRES_PASSWORD_FILE=/run/secrets/postgres_password
```

Create secrets:

```bash
echo "your-strong-password" | docker secret create postgres_password -
```

### Option 2: External Secrets Manager

For enterprise deployments, integrate with:
- **HashiCorp Vault**
- **AWS Secrets Manager**
- **Azure Key Vault**
- **Google Secret Manager**

Example with Vault:

```bash
# Store secret in Vault
vault kv put secret/mcp-playground/postgres password="..."

# Retrieve in entrypoint script
export POSTGRES_PASSWORD=$(vault kv get -field=password secret/mcp-playground/postgres)
```

---

## Scaling Considerations

### Horizontal Scaling

**Services that can scale horizontally:**
- MCP servers (run multiple replicas behind a load balancer)
- n8n (with shared database, requires careful queue configuration)

**Services that should NOT be scaled horizontally:**
- PostgreSQL (use read replicas instead)
- Qdrant (use clustering mode)

### Resource Limits

Add resource constraints to `docker-compose.production.yml`:

```yaml
services:
  postgres:
    deploy:
      resources:
        limits:
          cpus: '2'
          memory: 4G
        reservations:
          cpus: '1'
          memory: 2G
  
  n8n:
    deploy:
      resources:
        limits:
          cpus: '4'
          memory: 8G
        reservations:
          cpus: '2'
          memory: 4G
```

### Database Tuning

For PostgreSQL production settings, create `postgres/postgresql.conf`:

```conf
# Performance tuning
shared_buffers = 2GB
effective_cache_size = 6GB
maintenance_work_mem = 512MB
checkpoint_completion_target = 0.9
wal_buffers = 16MB
default_statistics_target = 100
random_page_cost = 1.1
work_mem = 20MB
max_connections = 200
```

Mount in docker-compose:

```yaml
postgres:
  volumes:
    - ./postgres/postgresql.conf:/etc/postgresql/postgresql.conf
  command: postgres -c config_file=/etc/postgresql/postgresql.conf
```

---

## High Availability

### Database HA

**Option 1: PostgreSQL Streaming Replication**

Set up primary-replica configuration:
- 1 primary (write)
- 2+ replicas (read)
- Use pgpool or HAProxy for connection pooling

**Option 2: Managed Database Service**
- Use AWS RDS, Azure Database, or Google Cloud SQL
- Built-in HA, backups, and monitoring

### Volume Persistence

**Production volume strategy:**
- Use NFS/NAS for shared storage across nodes
- Alternatively, use managed block storage (AWS EBS, Azure Disks)
- Implement automated backups (see [Backup Recovery Guide](BACKUP_RECOVERY.md))

---

## Monitoring

### Health Checks

Run health checks on a schedule:

```bash
# Crontab entry (every 5 minutes)
*/5 * * * * /path/to/scripts/health-check.sh --profile cpu >> /var/log/mcp-health.log 2>&1
```

### Logging

**Centralized logging with Loki + Promtail:**

```yaml
# Add to docker-compose.production.yml
services:
  loki:
    image: grafana/loki:latest
    ports:
      - "3100:3100"
    volumes:
      - ./loki:/etc/loki
      - loki_data:/loki

  promtail:
    image: grafana/promtail:latest
    volumes:
      - /var/log:/var/log:ro
      - ./promtail:/etc/promtail
```

**Application logs:**
- Use `docker compose logs` for debugging
- Configure log rotation to prevent disk fill
- Forward logs to external SIEM (Splunk, ELK, etc.)

### Metrics (Optional)

See [Monitoring Guide](MONITORING_GUIDE.md) for Prometheus + Grafana setup.

---

## Maintenance

### Regular Updates

```bash
# Pull latest images
docker compose pull

# Rebuild custom images
docker compose build n8n

# Restart services (rolling update)
docker compose --profile cpu up -d --no-deps --build n8n
```

### Backup Schedule

Automate backups with cron:

```bash
# Daily backup at 2 AM
0 2 * * * /path/to/scripts/backup-volumes.sh --retention-days 30
```

### Security Patching

- Subscribe to security advisories for all dependencies
- Apply patches within SLA (critical: 7 days, high: 30 days)
- Test patches in staging before production

### Disaster Recovery Test

**Quarterly DR drill:**

1. Take a backup
2. Destroy test environment
3. Restore from backup
4. Verify all services and data
5. Document time to recovery (RTO)

---

## Troubleshooting

### Common Production Issues

**Out of Memory**
- Check `docker stats` for memory usage
- Adjust resource limits in docker-compose
- Scale horizontally if needed

**Disk Space**
- Monitor volume usage: `docker system df -v`
- Clean old images: `docker image prune -a`
- Implement log rotation

**Network Issues**
- Verify DNS resolution inside containers
- Check firewall rules
- Test connectivity with `docker exec <container> curl <url>`

---

## Compliance & Auditing

### SOC 2 / ISO 27001 Considerations

- Enable audit logging for all API access
- Implement role-based access control (RBAC)
- Document change management procedures
- Regular penetration testing
- Data encryption at rest and in transit

### GDPR / Data Privacy

- Document data flows (use n8n workflow exports)
- Implement data retention policies
- Provide data export/deletion mechanisms
- Encrypt PII in databases

---

## Support & Escalation

For production issues:

1. Check logs: `docker compose logs -f`
2. Run health check: `./scripts/health-check.sh`
3. Review [Troubleshooting](../README.md#troubleshooting) in main README
4. Open GitHub issue with:
   - Environment details
   - Error logs
   - Steps to reproduce
