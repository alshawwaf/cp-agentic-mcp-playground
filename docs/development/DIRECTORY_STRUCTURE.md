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
│   ├── guides/                  # User guides
│   │   ├── n8n_Lakera_Playground_Guide.md
│   │   └── CheckPoint_Threat_Prevention_Guide.md
│   │
│   ├── operations/              # Operational guides
│   │   ├── BACKUP_RECOVERY.md
│   │   └── PRODUCTION_DEPLOYMENT.md
│   │
│   └── development/             # Development guides
│       ├── DEVELOPER_GUIDE.md
│       └── DIRECTORY_STRUCTURE.md   # This file
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
│       ├── Dockerfile               # Custom n8n image with MCP CLIs baked in
│       └── mcp-src/                 # Vendored Check Point MCP server source (built into the image)
│
├── n8n/                  # n8n configuration
│   ├── backup/
│   │   ├── credentials_public/      # Credential templates auto-imported by n8n-import
│   │   └── workflows/               # Example MCP-agent workflows
│   ├── custom-nodes/                # Community nodes (created at runtime)
│   └── shared/                      # Shared data between n8n and MCP sidecars (runtime)
│
├── aig/                  # AI-Infra-Guard support files
│   └── patches/llm.py               # LLM client patch mounted into aig-agent
│
├── quadrant/             # Legacy Qdrant backup dir (Qdrant is NOT in the compose stack)
│   └── backup/
│
├── assets/               # Demo GIF + exportable n8n tool workflows
├── .vscode/              # VS Code settings
│
├── .env                  # Environment configuration (gitignored)
├── .env-example          # Example environment file
├── .gitignore            # Git ignore patterns
├── docker-compose.yml    # Main Docker Compose stack
├── setup.sh              # Environment setup script
├── update.sh / update.ps1 # Pull + rebuild + restart helpers
├── implementation_plan.md # Design/implementation notes
├── LICENSE               # MIT License
└── README.md             # Main documentation
```

> Runtime-only bind-mount dirs (`langflow/`, `open-webui/`, `flowise_data/`) are created when the stack starts and are not committed. The MCP server source lives at `docker/n8n/mcp-src/` (there is no top-level `mcp-servers-source/`).

---

## Directory Purposes

### Core Configuration

- **Root**: Main configuration files (docker-compose.yml, .env, LICENSE)
- **.github/**: CI/CD automation with GitHub Actions
- **docker/**: Custom Docker image definitions

### Documentation

- **docs/**: Comprehensive guides for production deployment, backup/recovery, and operations
  - **guides/**: User guides for specific workflows
  - **operations/**: Operational guides (Backup, Deployment)
  - **development/**: Developer resources (Setup, Structure)
- **README.md**: Main project documentation with quick start and navigation

### Operations

- **scripts/**: Production-ready operational scripts
  - Health monitoring
  - Backup/restore automation
  - Environment validation
  - n8n provisioning
- **tests/**: Automated test suite for CI/CD integration

### Service Configurations

- **n8n/**: n8n-specific files (example workflows, credential templates, custom nodes, shared data)
- **aig/**: AI-Infra-Guard support files (LLM client patch)
- **quadrant/**: Legacy Qdrant backup directory — Qdrant is not part of the current compose stack

### Source Code

- **docker/n8n/mcp-src/**: Vendored source for the Check Point MCP servers, built into the custom n8n image

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
- `docs/operations/PRODUCTION_DEPLOYMENT.md` - Production deployment guide
- `docs/operations/BACKUP_RECOVERY.md` - Backup and DR procedures
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
- `open-webui/` - Chat UI data
- `flowise/` - Flowise data
- `langflow/` - Langflow data
- `aig_data` / `aig_db` / `aig_logs` / `aig_uploads` - AI-Infra-Guard state

Backup location (gitignored):
- `backups/` - Volume backup archives

---

## Updating This Document

When adding new files or directories:
1. Update this structure document
2. Add to .gitignore if needed
3. Create README in new directories
4. Update main README navigation if user-facing
