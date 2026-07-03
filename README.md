# Check Point Agentic MCP Playground

<p align="left">
  <img src="https://img.shields.io/badge/License-MIT-blue.svg" />
  <img src="https://img.shields.io/badge/Docker-Compose-blue?logo=docker" />
  <img src="https://img.shields.io/badge/n8n-Automation-orange?logo=n8n" />
  <img src="https://img.shields.io/badge/langflow-Workflow-green?logo=langflow" />
  <img src="https://img.shields.io/badge/Ollama-LLM-grey" />
  <img src="https://img.shields.io/badge/Check%20Point-MCP-magenta?" />
  <img src="https://img.shields.io/badge/Deploy-Dokploy%20%2B%20Traefik-blueviolet" />
</p>

> **An Agentic AI Playground built on Check Point MCP Servers**
>
> Bring up n8n + Ollama + Open WebUI + Langflow + Flowise + AI-Infra-Guard + a full fleet of Check Point MCP servers with:
>
> ```bash
> docker compose --profile cpu up -d
> ```
---

## Quick Overview

A **multi-service Docker Compose stack** that brings up a full AI + Check Point MCP (Model Context Protocol) environment:

- **n8n** – workflow automation + MCP tools orchestrator
- **PostgreSQL** – n8n backend database
- **Auto-provisioner** – creates the n8n instance owner **and** installs `n8n-nodes-mcp`
- **Ollama** – local LLMs; CPU **or** NVIDIA GPU; auto model pull
- **Open WebUI** – chat UI for Ollama / LLMs (with an n8n pipe)
- **Langflow** – visual AI flow builder
- **Flowise** – LLM orchestration UI
- **AI-Infra-Guard** – Tencent Zhuque Lab AI red-teaming platform (MCP security scanning, jailbreak eval)
- **Check Point MCP servers** – 13 dedicated HTTP sidecars on the Docker network

The goal is a **single lab stack** for building, testing, and demoing AI + Check Point workflows:

- Instance owner is **auto-configured**
- `n8n-nodes-mcp` is **pre-installed**
- MCP servers are reachable as **HTTP tools** from inside n8n

> **Deployment model.** This repo is deployed on the lab's bare-metal Ubuntu + [Dokploy](https://dokploy.com) host, where **Traefik** provides ingress and **Let's Encrypt** provides TLS. The web UIs are published as subdomains (e.g. `chat.<domain>`, `langflow.<domain>`, `flowise.<domain>`, `aig.<domain>`) via the external `dokploy-network`, **not** on host ports. You can still run it standalone with `docker compose up`, but no service ports are bound to the host by default — see [URLs & Access](#-urls--access).

---

## Navigation

- [📦 Quick Start](#-quick-start)
- [🔧 Tech Stack & Layout](#-tech-stack--layout)
- [⚙️ Environment--env](#%EF%B8%8F-environment-env)
- [🏗️ Build & Profiles](#%EF%B8%8F-build--profiles)
- [🎯 n8n Provision & Auto-Import](#-n8n-provision--auto-import)
- [🌐 URLs & Access](#-urls--access)
- [🤖 Ollama Models](#-ollama-models)
- [💾 Data & Persistence](#-data--persistence)
- [📚 Guides](#-guides)
- [🚀 Production Deployment](docs/operations/PRODUCTION_DEPLOYMENT.md)
- [💿 Backup & Recovery](docs/operations/BACKUP_RECOVERY.md)
- [🔍 Troubleshooting](#-troubleshooting)
- [🔄 Updating & Resetting](#%EF%B8%8F-updating--resetting)

---

## Quick Start

<details>
<summary><strong>1. Requirements</strong></summary>

- **Docker Engine** + **Docker Compose v2**
- Outbound Internet access from containers (for:
  - Pulling images
  - Installing community nodes
  - Pulling Ollama models)
- All commands executed from the folder containing `docker-compose.yml` and `.env`

**Optional – GPU Profile**

- NVIDIA GPU on the host
- **NVIDIA Container Toolkit** installed
- `nvidia-smi` works on the host

</details>

<details>
<summary><strong>2. Minimal Happy Path</strong></summary>

1. Run the setup script to generate a secure `.env` file:
   
   ```bash
   ./setup.sh
   ```
   
   (Or manually create a `.env` file if you prefer, see [Environment](#%EF%B8%8F-environment-env)).  
2. Build the custom n8n image:

   ```bash
   docker compose build n8n
   ```

3. Start the CPU stack:

   ```bash
   docker compose --profile cpu up -d
   ```

4. Wait 30–60 seconds for:
   - Postgres
   - n8n
   - Provisioner (owner + `n8n-nodes-mcp`)
   - Optional `n8n-import` (workflows/credentials)

5. Browse to **n8n**:

   - On the Dokploy/Traefik host, use the routed subdomain (e.g. `https://n8n.<domain>`).
   - Log in with the owner credentials from `.env`.
   - No host port is bound by default. To reach a service directly on a plain host, add a `ports:` mapping in `docker-compose.yml` (see [URLs & Access](#-urls--access)) or `docker compose exec` into the container.

</details>

<details>
<summary><strong>3. GPU Profile</strong></summary>

If you want Ollama to use GPU instead of CPU:

```bash
# Start GPU profile
docker compose --profile gpu-nvidia up -d

# Or CPU + GPU together
docker compose --profile cpu --profile gpu-nvidia up -d
```

To avoid repeating profiles each time:

```bash
export COMPOSE_PROFILES=cpu
docker compose up -d
```

</details>

---

## Tech Stack & Layout

### Stack Summary

| Component      | Purpose                                      |
|----------------|----------------------------------------------|
| n8n            | Automation engine + MCP orchestrator         |
| PostgreSQL     | n8n backend DB                               |
| n8n-provision  | Owner creation + `n8n-nodes-mcp` installation|
| n8n-import     | Optional workflow & credential auto-import   |
| Ollama         | Local LLM server (CPU / GPU)                 |
| Open WebUI     | Chat UI for Ollama                           |
| Flowise        | LLM orchestration / low-code builder         |
| Langflow       | Visual AI flow builder                       |
| AI-Infra-Guard | AI red-teaming: MCP security scan + jailbreak eval |
| MCP sidecars   | Check Point MCP tools as HTTP servers (13)   |

### Repository Layout

```bash
.
├─ docker-compose.yml           # Multi-service stack (CPU / GPU profiles)
├─ .env                         # Passwords / admin values / MCP creds (from .env-example)
├─ .env-example                 # Template environment file
├─ setup.sh                     # Interactive .env + credential bootstrap
├─ update.sh / update.ps1       # Pull + rebuild + restart helpers
├─ docker/
│  └─ n8n/
│     ├─ Dockerfile             # Custom n8n image (MCP CLIs + wrappers baked in)
│     └─ mcp-src/               # Vendored Check Point MCP server source (built into the image)
├─ scripts/                     # n8n-provision, health-check, backup/restore, validate-env
├─ tests/                       # Integration test suite (used by CI)
├─ n8n/
│  ├─ backup/
│  │  ├─ credentials_public/    # Credential templates auto-imported by n8n-import
│  │  └─ workflows/             # Example MCP-agent workflows auto-imported by n8n-import
│  ├─ custom-nodes/             # Extra n8n nodes (persisted, created at runtime)
│  └─ shared/                   # Shared data between n8n and MCP sidecars (runtime)
├─ aig/
│  └─ patches/llm.py            # AI-Infra-Guard LLM client patch (mounted into aig-agent)
├─ assets/                      # Demo GIF + exportable n8n tool workflows
├─ quadrant/                    # Legacy Qdrant backup dir (Qdrant is NOT in the compose stack)
└─ docs/                        # Guides + operations + development docs
```

> **Note.** `langflow/`, `open-webui/`, and `flowise_data/` are created at runtime (bind mounts) and are not committed. Qdrant is **not** part of the compose stack despite the leftover `quadrant/` directory.

---

## Environment (`.env`)

Run `./setup.sh` to generate a `.env` from `.env-example` (optionally with random secrets), or copy `.env-example` yourself. The variable names below must match what `docker-compose.yml` reads — the authoritative template is [`.env-example`](.env-example):

```env
# ───────── Postgres (n8n DB) ─────────
POSTGRES_USER=admin
POSTGRES_PASSWORD=change_me
POSTGRES_DB=n8n
POSTGRES_PORT=5432

# ───────── n8n ─────────
N8N_HOST=localhost
N8N_PORT=5678
N8N_ENCRYPTION_KEY=change_me_to_a_long_random_string
N8N_USER_MANAGEMENT_JWT_SECRET=change_me_to_a_random_secret

# owner/admin (POSTed by the provisioner to /rest/owner/setup)
N8N_ADMIN_EMAIL=admin@example.com
N8N_ADMIN_FIRST_NAME=Admin
N8N_ADMIN_LAST_NAME=User
N8N_ADMIN_PASSWORD=change_me

# basic auth (MUST match the provisioner)
N8N_BASIC_AUTH_USER=admin
N8N_BASIC_AUTH_PASSWORD=change_me
WEBHOOK_URL=http://localhost:5678/
N8N_EDITOR_BASE_URL=http://localhost:5678/
N8N_PUSH_BACKEND=websocket

# ───────── Ollama ─────────
OLLAMA_PORT=11434
OLLAMA_HOST=localhost
OLLAMA_KEEP_ALIVE=-1

# ───────── Other UIs ─────────
LANGFLOW_PORT=7860
FLOWISE_PORT=3001
OPEN_WEBUI_PORT=3000

# Auth is ON by default for the publicly-routed Langflow. Set true for a no-auth demo.
LANGFLOW_AUTO_LOGIN=false

# ───────── Documentation MCP ─────────
DOC_CLIENT_ID=
DOC_SECRET_KEY=
DOC_REGION=EU            # one of: EU, US, STG, Local

# ───────── Management-backed MCP (SMS host + API key) ─────────
MANAGEMENT_HOST=
MANAGEMENT_API_KEY=
TE_API_KEY=
REPUTATION_API_KEY=

# ───────── Spark Management MCP ─────────
SPARK_MGMT_CLIENT_ID=
SPARK_MGMT_SECRET_KEY=
SPARK_MGMT_REGION=US
SPARK_MGMT_INFINITY_PORTAL_URL=https://cloudinfra-gw-us.portal.checkpoint.com/auth/external

# ───────── Harmony SASE MCP ─────────
HARMONY_SASE_API_KEY=
HARMONY_SASE_MANAGEMENT_HOST=
HARMONY_SASE_REGION=

CPINFO_LOG_LEVEL=info

# ───────── AI-Infra-Guard (AI red teaming) ─────────
AIG_PORT=8088
AIG_LLM_API_KEY=ollama
AIG_LLM_BASE_URL=http://ollama-cpu:11434/v1
AIG_LLM_MODEL=huihui_ai/deepseek-r1-abliterated:latest

# ───────── Profiles ─────────
COMPOSE_PROFILES=true,cpu
```

> 💡 **Tip**  
> - `N8N_ADMIN_*` is used by the provisioner to call `/rest/owner/setup`.  
> - `N8N_BASIC_AUTH_*` must match what the provisioner uses to authenticate.  
> - The `MANAGEMENT_HOST` + `MANAGEMENT_API_KEY` pair backs several MCP sidecars (HTTPS Inspection, Quantum Management, Management Logs, Threat Prevention, GW CLI, GW Connection Analysis, Gaia, CPInfo Analysis).  
> - Only populate the MCP variables for the services you actually use (or comment out those services in `docker-compose.yml`).

---

## Build & Profiles

### Build the Custom n8n Image

The Check Point MCP sidecars reuse a **custom n8n base image** with MCP CLIs and wrappers baked in.

```bash
docker compose build n8n
```

This produces an image (e.g. `custom-mcp-n8n:custom`) which includes:

- All relevant `@chkp/*` MCP CLI packages
- Wrapper scripts in `/usr/local/bin` for each MCP service:
  - `mcp-documentation`
  - `mcp-https-inspection`
  - `mcp-quantum-management`
  - `mcp-management-logs`
  - `threat-emulation-mcp`
  - `threat-prevention-mcp`
  - `spark-management-mcp`
  - `reputation-service-mcp`
  - `harmony-sase-mcp`
  - `quantum-gw-cli-mcp`
  - `quantum-gw-connection-analysis-mcp`
  - `quantum-gaia-mcp`
  - `cpinfo-analysis-mcp`

### Start the Stack

**CPU stack:**

```bash
docker compose --profile cpu up -d
```

**GPU (NVIDIA) Ollama stack:**

```bash
docker compose --profile gpu-nvidia up -d
```

**Both profiles:**

```bash
docker compose --profile cpu --profile gpu-nvidia up -d
```

Set a default profile:

```bash
export COMPOSE_PROFILES=cpu
docker compose up -d
```

---

## n8n Provision & Auto-Import

### Provisioner (`n8n-provision`)

The `n8n-provision` sidecar runs **once** and performs:

1. Wait for `http://n8n:5678/healthz`
2. Create the owner via `/rest/owner/setup` (using `N8N_ADMIN_*`)
3. Login using `N8N_BASIC_AUTH_*` and owner credentials
4. Install `n8n-nodes-mcp` (idempotent – HTTP 400 “already installed” is fine)
5. Exit

Safe to re-run; it will **skip** already-completed steps.

### Auto-Import (`n8n-import`)

After the provisioner completes, the `n8n-import` container imports the assets committed in the repo:

- `./n8n/backup/credentials_public` – credential templates (edit these and replace `CHANGE_ME` with real keys)
- `./n8n/backup/workflows` – the example MCP-agent workflows

It runs:

```bash
n8n import:credentials --separate --input=/backup/credentials_public
n8n import:workflow    --separate --input=/backup/workflows
```

> `setup.sh` copies `credentials_public/` to `credentials/` locally so you can fill in real keys without touching the committed templates. The `n8n-import` service imports from `credentials_public`.

---

## URLs & Access

> **No host ports are bound.** All direct `ports:` bindings were removed to shrink the external attack surface. On the lab host, the web UIs are reached through **Traefik** at their subdomains. Standalone, reach a service by adding a temporary `ports:` mapping or via `docker compose exec`.

### Main Web UIs (Traefik subdomains)

These services join the external `dokploy-network` and are routed by Traefik with Let's Encrypt TLS:

| Service        | URL (routed)                 |
|----------------|------------------------------|
| Open WebUI     | `https://chat.<domain>`      |
| Flowise        | `https://flowise.<domain>`   |
| Langflow       | `https://langflow.<domain>`  |
| AI-Infra-Guard | `https://aig.<domain>`       |

n8n is served on `demo` (internal) and routed by the host's existing Dokploy workflow at `n8n.<domain>`. Ollama is internal-only (`ollama-cpu:11434` on the `demo` network) and is not routed externally.

To reach a service directly on a host without Traefik, add a mapping under that service in `docker-compose.yml`, e.g.:

```yaml
  open-webui:
    ports:
      - "3000:8080"
```

### MCP Servers – Internal Docker URLs

The 13 Check Point MCP sidecars are **internal-only** on the `demo` network (no host ports, no Traefik route). Use these base URLs from **n8n MCP HTTP nodes**:

- Documentation MCP → `http://mcp-documentation:3000`
- HTTPS Inspection MCP → `http://mcp-https-inspection:3001`
- Quantum Management MCP → `http://mcp-quantum-management:3002`
- Management Logs MCP → `http://mcp-management-logs:3003`
- Threat Emulation MCP → `http://threat-emulation-mcp:3004`
- Threat Prevention MCP → `http://threat-prevention-mcp:3005`
- Spark Management MCP → `http://spark-management-mcp:3006`
- Reputation Service MCP → `http://reputation-service-mcp:3007`
- Harmony SASE MCP → `http://harmony-sase-mcp:3008`
- Quantum GW CLI MCP → `http://quantum-gw-cli-mcp:3009`
- Quantum GW Connection Analysis MCP → `http://quantum-gw-connection-analysis-mcp:3010`
- Quantum Gaia MCP → `http://quantum-gaia-mcp:3011`
- CPInfo Analysis MCP → `http://cpinfo-analysis-mcp:3012`

### n8n MCP Node Configuration

In n8n’s MCP client nodes (e.g., `mcpClientTool`):

- **Connection Type / Mode**: `http`
- **Base URL**: `http://<mcp-service-name>:<port>` (e.g., `http://threat-emulation-mcp:3004`)
- **Do NOT** set “Package” or “Command” to `@chkp/...`.

If package fields are set, n8n will try `npx @chkp/...` in the container and you’ll see:
- `npm warn exec The following package was not found and will be installed`
- `Error [ERR_MODULE_NOT_FOUND]: Cannot find package '@chkp/mcp-utils'`

HTTP mode + sidecars = clean, predictable behavior.

---

## 🔌 n8n Pipe for Open WebUI

The included `n8n_pipe.py` allows Open WebUI to talk to n8n workflows.

### Configuration

1.  **Default URL**: The pipe is pre-configured to send requests to `http://n8n:5678/webhook/n8n-pipe`.
2.  **Multiple Workflows**: You can use this pipe with **any** n8n workflow.
    - In Open WebUI, go to **Admin Panel** -> **Functions** (or **Pipes**).
    - Select the **N8N Pipe**.
    - Edit the **N8N URL** valve to point to your desired workflow's webhook URL (e.g., `http://n8n:5678/webhook/my-custom-agent`).
    - You can even duplicate the pipe file to have multiple named pipes pointing to different agents!

### Workflow Requirements

For a workflow to work with this pipe, it must:
1.  Start with a **Webhook** node.
2.  Method: `POST`.
3.  Path: Matches the URL you configured (e.g., `n8n-pipe`).
4.  Respond using a **Respond to Webhook** node.

---

## Ollama Models

The `ollama-pull-models-*` sidecar waits for the Ollama API and then pulls the configured model. The stack currently pulls:

- `huihui_ai/foundation-sec-abliterated:latest`

AI-Infra-Guard's agent additionally uses `huihui_ai/deepseek-r1-abliterated:latest` (via `AIG_LLM_MODEL`), pointed at the local Ollama by default.

To change which models are pulled, edit the `command:` in the `ollama-pull-models-*` services inside `docker-compose.yml`.

To inspect or prune models inside the Ollama container:

```bash
ollama list
ollama rm <model-name>
```

---

## Data & Persistence

The stack uses named Docker volumes so you can destroy containers and keep data.

| Volume            | Description                                   |
|-------------------|-----------------------------------------------|
| `n8n_storage`     | n8n config, user data, some cached metadata   |
| `postgres_storage`| PostgreSQL database for n8n                   |
| `ollama_storage`  | Ollama models and data                        |
| `open-webui`      | Open WebUI data                               |
| `flowise`         | Flowise data                                  |
| `langflow`        | Langflow data                                 |
| `aig_data` / `aig_db` / `aig_logs` / `aig_uploads` | AI-Infra-Guard state, DB, logs, uploads |

Example: backup `n8n_storage` to a local tarball:

```bash
docker run --rm -v n8n_storage:/data -v "$(pwd)":/backup busybox \
  tar czf /backup/n8n_storage.tgz -C /data .
```

---

## Troubleshooting

### A) `n8n-import`: “Mismatching encryption keys”

Make sure **all n8n-related containers** (`n8n`, `n8n-import`, `n8n-provision`) use the **same** `N8N_ENCRYPTION_KEY` value from `.env`.

### B) Provisioner: HTTP 400 “Package already installed”

Normal on re-runs. As long as you see `n8n-nodes-mcp` in **Settings → Community Nodes**, you’re good.

### C) MCP node shows `@chkp/...` install errors

If logs show:

- `npm warn exec The following package was not found and will be installed`
- `Error [ERR_MODULE_NOT_FOUND]: Cannot find package '@chkp/mcp-utils'`

…then the node is in **package mode**. Switch it to **HTTP** and point at the correct MCP URL, e.g.:

```text
http://management-logs-mcp:3003
```

### D) Cannot reach MCP from inside Docker

From the `n8n` container, use the **service name**, not `localhost`:

```bash
docker compose exec n8n sh
# Inside
curl http://mcp-documentation:3000/
```

If that works, the MCP sidecar is healthy and reachable.

### E) Postgres connection issues

Check the DB logs:

```bash
docker compose logs postgres | tail -n 50
```

Ensure:

- `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` match between `postgres`, `n8n`, and `n8n-import`.

### F) GPU not detected

- Ensure you use the `gpu-nvidia` profile.
- Verify NVIDIA toolkit:

  ```bash
  docker run --rm --gpus all nvidia/cuda:12.3.2-base nvidia-smi
  ```

If this fails, fix GPU drivers / toolkit before using the GPU profile.

---

## Updating & Resetting

### Updating

- **n8n-nodes-mcp** – update via n8n UI (Settings → Community Nodes) or adjust the version in your provisioner / Dockerfile and rebuild.
- **Check Point MCP CLIs** – adjust versions in `docker/n8n/Dockerfile` and rebuild:

  ```bash
  docker compose build n8n
  ```

- **Base Images** – To update the underlying `n8n` version (fixes "n8n is not running the latest version"):

  ```bash
  # This pulls the latest base image AND rebuilds your custom image in one step
  docker compose build --pull n8n
  
  # Then restart the service
  docker compose --profile cpu up -d n8n
  ```

  > **Note**: Do not run `docker compose pull n8n`. This will fail because it tries to pull your *local custom image* from Docker Hub. Always use `build --pull`.

(adjust profiles as needed).

### Full Reset (Lab-Style)

If you want a **clean slate** (fresh DB, no workflows, no credentials, no chat history):

```bash
# WARNING: deletes all volumes for selected profiles
docker compose --profile cpu down -v
docker compose --profile cpu up -d
```

This re-creates the entire environment from zero, re-runs the provisioner, and re-imports any assets in `./n8n/backup`.

---

## 📚 Guides

Detailed documentation for specific workflows and agents:

- **[Lakera Playground Guide](docs/guides/n8n_Lakera_Playground_Guide.md)**: A complete guide to the Lakera Guard workflow, including a technical deep dive into each node and security logic.
- **[Threat Prevention Agent Guide](docs/guides/CheckPoint_Threat_Prevention_Guide.md)**: Documentation for the Check Point Threat Prevention agent, covering the AI agent, MCP client, and policy management.
- **[MCP Gateway Agent Guide](docs/guides/MCP_Gateway_Agent_Guide.md)**: Two ways to connect agents to the Check Point MCP servers — direct sidecar vs. the Docker MCP Gateway (aggregation + Bearer auth) — with walkthroughs, exercises, and lab-connectivity troubleshooting.

### 🧩 Gateway-ready Check Point MCP servers

The vendored Check Point MCP servers carry a local patch that gives each
Streamable HTTP session its own server instance — required for fronting them
with an MCP gateway (stock packages are single-client and break on concurrent
sessions). **[docker/n8n/mcp-src/PATCHES.md](docker/n8n/mcp-src/PATCHES.md)**
documents the why, the per-package capability matrix, and how to make more
packages gateway-ready. Built npm tarballs of all servers are attached to the
repo's **GitHub Releases** so students can grab and reuse them directly.

---

## 🚀 Hardening & Operations

This stack is built for **lab and demo** use. Before exposing it more widely, apply the hardening below and read the [Production Deployment Guide](docs/operations/PRODUCTION_DEPLOYMENT.md) (which starts with its own "for production, additional hardening is required" warning). The repo ships operational helpers to support that:

### Checklist

1. **Generate Secure Configuration**
   ```bash
   ./setup.sh
   ./scripts/validate-env.sh
   ```

2. **Run Health Checks**
   ```bash
   ./scripts/health-check.sh --profile cpu
   ```

3. **Set Up Automated Backups**
   ```bash
   # Add to crontab for daily backups
   0 2 * * * /path/to/scripts/backup-volumes.sh --retention-days 30
   ```

4. **Enable CI/CD**
   - GitHub Actions workflows are in `.github/workflows/`
   - Automated testing and security scanning on every push

### Production Documentation

- **[Production Deployment Guide](docs/operations/PRODUCTION_DEPLOYMENT.md)** - Comprehensive guide covering:
  - Security hardening & SSL/TLS configuration
  - Secret management (Docker Secrets, Vault, etc.)
  - Scaling & high availability
  - Monitoring & observability
  - Compliance (SOC2, GDPR, etc.)

- **[Backup & Recovery Guide](docs/operations/BACKUP_RECOVERY.md)** - Complete backup strategy:
  - Automated volume backups
  - Disaster recovery procedures
  - RTO/RPO definitions
  - Point-in-time recovery

### Automated Testing

Run integration tests locally:

```bash
# Run full test suite
./tests/integration-test.sh

# Test specific profile
PROFILE=cpu ./tests/integration-test.sh
```

CI/CD automatically runs tests on every push and pull request.

---

## Security Best Practices

1.  **Use the Setup Script**: Always use `./setup.sh` to generate strong, random passwords for your environment.
2.  **Protect `.env`**: Your `.env` file contains sensitive credentials. Never commit it to version control.
3.  **API Keys**: Be careful when entering API keys. Ensure you trust the environment where you are running this stack.
4.  **Network Isolation**: This stack is designed for local development. If deploying to a shared network, ensure proper firewall rules are in place.

