# Directory Structure

This document provides an overview of the MCP Playground directory structure.

## Root Directory

```
cp-agentic-mcp-playground/
├── .github/              # GitHub Actions workflows
│   └── workflows/
│       ├── ci.yml                    # Continuous integration tests
│       └── security-scan.yml         # Security vulnerability scanning
│
├── docs/                 # Documentation
│   ├── BACKUP_RECOVERY.md           # Backup and disaster recovery guide
│   ├── PRODUCTION_DEPLOYMENT.md     # Production deployment guide
│   └── Lakera_Playground_Guide.md   # Legacy guide (reference)
│
├── scripts/              # Operational scripts
│   ├── README.md                    # Scripts documentation
│   ├── backup-volumes.sh            # Automated volume backups
│   ├── health-check.sh              # Service health monitoring
│   ├── n8n-provision.sh             # n8n auto-provisioning
│   ├── restore-volumes.sh           # Volume restoration
│   └── validate-env.sh              # Environment validation
│
├── tests/                # Test suite
│   ├── README.md                    # Test documentation
│   ├── integration-test.sh          # Integration tests
│   └── test-helpers.sh              # Shared test utilities
│
├── docker/               # Docker image definitions
│   └── n8n/
│       └── Dockerfile               # Custom n8n image with MCP CLIs
│
├── n8n/                  # n8n configuration
│   ├── backup/                      # Workflow/credential exports
│   ├── custom-nodes/                # Community nodes
│   └── shared/                      # Shared data between containers
│
├── open-webui/           # Open WebUI configuration
│   └── pipes/                       # n8n integration pipes
│
├── langflow/             # Langflow configuration
│   └── flows/                       # Example flows
│
├── flowise/              # Flowise configuration
├── quadrant/             # Qdrant backups
│   └── backup/
│
├── mcp-servers-source/   # MCP server source code
├── assets/               # Project assets (images, etc.)
├── .vscode/              # VS Code settings
│
├── .env                  # Environment configuration (gitignored)
├── .env-example          # Example environment file
├── .gitignore            # Git ignore patterns
├── docker-compose.yml    # Main Docker Compose stack
├── setup.sh              # Environment setup script
├── LICENSE               # MIT License
├── README.md             # Main documentation
└── DEVELOPER_GUIDE.md    # Developer setup guide
```

---

## Directory Purposes

### Core Configuration

- **Root**: Main configuration files (docker-compose.yml, .env, LICENSE)
- **.github/**: CI/CD automation with GitHub Actions
- **docker/**: Custom Docker image definitions

### Documentation

- **docs/**: Comprehensive guides for production deployment, backup/recovery, and operations
- **README.md**: Main project documentation with quick start and navigation
- **DEVELOPER_GUIDE.md**: Setup instructions for developers

### Operations

- **scripts/**: Production-ready operational scripts
  - Health monitoring
  - Backup/restore automation
  - Environment validation
  - n8n provisioning
- **tests/**: Automated test suite for CI/CD integration

### Service Configurations

- **n8n/**: n8n-specific files (workflows, credentials, custom nodes)
- **open-webui/**: Open WebUI pipes for n8n integration
- **langflow/**: Langflow example flows
- **flowise/**: Flowise configuration
- **quadrant/**: Qdrant backup storage

### Source Code

- **mcp-servers-source/**: Source code for Check Point MCP servers

---

## File Organization Principles

1. **Separation of Concerns**: Scripts, tests, configuration, and documentation are in separate directories
2. **Self-Documenting**: Each directory has a README explaining its contents
3. **Production-Ready**: Clear separation between development and production files
4. **Version Control**: Sensitive files (.env, backups) are gitignored

---

## Key Files

### Configuration
- `.env-example` - Template for environment variables
- `docker-compose.yml` - Multi-service stack definition
- `setup.sh` - Generates secure .env file

### Documentation
- `README.md` - Main project documentation
- `docs/PRODUCTION_DEPLOYMENT.md` - Production deployment guide
- `docs/BACKUP_RECOVERY.md` - Backup and DR procedures
- `scripts/README.md` - Scripts usage guide
- `tests/README.md` - Testing guide

### Automation
- `.github/workflows/ci.yml` - CI/CD pipeline
- `.github/workflows/security-scan.yml` - Security scanning
- `scripts/health-check.sh` - Health monitoring
- `scripts/backup-volumes.sh` - Automated backups
- `tests/integration-test.sh` - Integration tests

---

## Data Persistence

Docker volumes (not in repository):
- `n8n_storage/` - n8n data
- `postgres_storage/` - PostgreSQL database
- `ollama_storage/` - LLM models
- `qdrant_storage/` - Vector database
- `open-webui/` - Chat UI data
- `flowise/` - Flowise data
- `langflow/` - Langflow data

Backup location (gitignored):
- `backups/` - Volume backup archives

---

## Updating This Document

When adding new files or directories:
1. Update this structure document
2. Add to .gitignore if needed
3. Create README in new directories
4. Update main README navigation if user-facing
