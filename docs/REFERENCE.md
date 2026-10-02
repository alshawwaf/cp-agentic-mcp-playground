# Check Point Agentic MCP Lab reference

This is the operator reference for the Check Point Agentic MCP Lab. The [README](../README.md) covers what the lab is and the short path to a running lab. This page holds the detail: every service, setting, check, and fix.

The page describes what [`docker-compose.yml`](../docker-compose.yml), [`.env-example`](../.env-example), [`setup.sh`](../setup.sh) and the scripts in [`scripts/`](../scripts/) do today. If this page and the compose file ever disagree, the compose file is right. The behavior described here was tested end to end on a fresh install in October 2026 (see [Test results](#test-results)).

## Contents

1. [Lab components](#lab-components): component sets, profiles, services, networks, volumes, images, local access
2. [Configuration](#configuration): `.env`, `setup.sh`, the admin password, every setting
3. [Secrets and 1Password](#secrets-and-1password): generated secrets, `op run`, rotation, security notes
4. [Model (lab-chat)](#model-lab-chat): provider selection, Azure OpenAI, local Ollama
5. [MCP servers](#mcp-servers): the 14 Check Point MCP servers, credentials, certificates, file folders
6. [MCP Gateway](#mcp-gateway): token, Docker access, tool scoping, adding a server
7. [Seeding and upgrades](#seeding-and-upgrades): what each importer does, edited agents, `update.sh`
8. [Observability](#observability): Langfuse
9. [RAG, evals, and exercises](#rag-evals-and-exercises)
10. [Security lab and AI-Infra-Guard](#security-lab-and-ai-infra-guard): containment, scans
11. [Doctor and acceptance tests](#doctor-and-acceptance-tests)
12. [Troubleshooting](#troubleshooting)
13. [Backup and restore](#backup-and-restore)
14. [Scripts](#scripts)
15. [CI](#ci)
16. [Related repositories](#related-repositories)
17. [Guides](#guides-index)
18. [License](#license)

---

## Lab components

### Component sets

`./setup.sh` shows the CPUs and memory of the machine and what Docker can use. It then offers two component sets, marks the one this machine can run as recommended, and writes the choice to `COMPOSE_PROFILES` in `.env`.

| | Standard lab (default) | Complete lab |
|---|---|---|
| `COMPOSE_PROFILES` | blank | `complete` |
| Runs | n8n, Flowise, the MCP Gateway and all 14 Check Point MCP servers, `lab-chat` (LiteLLM), Langfuse, and RAG (Qdrant) | Everything in the Standard lab, plus Langflow and Open WebUI with local models |
| Services | 30 (23 long-running, 7 one-shot jobs) | 34 (adds `langflow`, `open-webui`, `openwebui-provision`, `ollama-pull-chat-models`) |
| Docker needs | 4 CPUs and 8 GB of memory | 6 CPUs and 16 GB of memory |

- Set the resources in Docker Desktop > Settings > Resources. Docker Desktop set to 16 GB reports about 15.1 GB. Setup and `doctor.sh` count 15 GiB or more as 16 GB
- A lab host (`DOMAIN` set) always gets the Complete lab as the recommendation. On Dokploy, set `COMPOSE_PROFILES=complete` in the project's environment settings
- The profile was formerly named `full`. `./setup.sh` renames it to `complete` in `.env` and keeps the other profiles
- Switch later: run `./setup.sh` again and choose the other set, or edit `COMPOSE_PROFILES` in `.env`. Then run `docker compose up -d`
- With no cloud model key, `lab-chat` runs on a local Ollama model. Local models need Docker with 16 GB of memory and run slowly on a CPU (see [Ollama: local fallback and memory](#ollama-local-fallback-and-memory))

Every long-running service has a CPU cap (`cpus`) and a memory cap (`mem_limit`). A busy service is throttled or restarted instead of starving the host. The memory caps of the Standard lab's long-running services add up to about 7.6 GiB with the default `OLLAMA_MEM_LIMIT=1g`. The one-shot jobs add about 1.7 GiB while they run. The Complete lab adds 4.5 GiB of caps (Langflow 2.5 GiB, Open WebUI 2 GiB), and setup raises `OLLAMA_MEM_LIMIT` to `6g` for it. Caps are upper bounds: actual use is lower.

### Profiles

Set profiles in `COMPOSE_PROFILES` in `.env`, comma-separated, for example `COMPOSE_PROFILES=complete,exercises,security-lab`. To start a profile once: `docker compose --profile exercises up -d`.

| Profile | Adds | Notes |
|---|---|---|
| (none) | The Standard lab | Always on |
| `complete` | `langflow`, `open-webui`, `openwebui-provision`, `ollama-pull-chat-models` | The Complete lab |
| `langflow` | `langflow` | Langflow only, the heaviest builder (up to 2.5 GiB) |
| `local-chat` | `open-webui`, `openwebui-provision`, `ollama-pull-chat-models` | Open WebUI with a local chat model. Needs `OLLAMA_MEM_LIMIT=6g` |
| `local-models` | `ollama-pull-chat-models` | Pulls `OLLAMA_MODELS` even when a cloud key is set |
| `evals` | `evals-run` | One-shot: `docker compose --profile evals run --rm evals-run` |
| `aig-ui-access` | `aig-ui` | The published side of the UI proxy only (no other service joins it). |
| `ai-red-team` | `aig-webserver`, `aig-agent`, `aig-provision`, `aig-ui` | AI-Infra-Guard. The scanner stays on internal networks; `aig-ui` is the opt-in way into its web UI (see [Security lab and AI-Infra-Guard](#security-lab-and-ai-infra-guard)) |
| `security-lab` | `vuln-mcp` | The intentionally vulnerable MCP server, on its own internal network |
| `exercises` | `ips-cve-mcp` | Build Your Own MCP exercise |
| `policypilot` | `policypilot-mcp` | Needs a locally built PolicyPilot image and its data |
| `gpu-nvidia` | `ollama-gpu` | NVIDIA hosts only |

`doctor.sh --preflight` warns about a profile name that matches no service.

### Long-running services

All ports are internal. No service publishes a host port, and the CI policy check fails any `ports:` key in `docker-compose.yml` (see [Local access and lab hosts](#local-access-and-lab-hosts)). Every third-party image is pinned by tag and digest (`name:tag@sha256:...`). The table shows the tag.

| Service | Image | Port | Profile | Networks | CPUs | Memory |
|---|---|---|---|---|---|---|
| `postgres` | `postgres:16.15-alpine` | 5432 | default | `lab` | 1.0 | 512m |
| `ollama-cpu` | `ollama/ollama:0.31.1` | 11434 | default | `lab`, `dokploy-network` | `OLLAMA_CPUS` (2) | `OLLAMA_MEM_LIMIT` (1g) |
| `ollama-gpu` | `ollama/ollama:0.31.1` | 11434 | `gpu-nvidia` | `lab`, `dokploy-network` | 4.0 | 8g |
| `n8n` | `ghcr.io/alshawwaf/cp-agentic-n8n:${LAB_IMAGE_TAG}` (n8n 2.41.5) | 5678 | default | `lab`, `security-lab` | 1.5 | 1g |
| `flowise` | `flowiseai/flowise:3.1.4` | `FLOWISE_PORT` (3020) | default | `lab`, `dokploy-network`, `security-lab` | 1.0 | 1g |
| `langflow` | `langflowai/langflow:1.10.1` | 7860 | `langflow`, `complete` | `lab`, `dokploy-network`, `security-lab` | 1.5 | 2560m |
| `open-webui` | `ghcr.io/open-webui/open-webui:v0.11.4` | 8080 | `local-chat`, `complete` | `lab`, `dokploy-network` | 1.0 | 2g |
| `langfuse` | `langfuse/langfuse:2.95.11` | 3000 | default | `lab`, `dokploy-network` | 0.5 | 512m |
| `litellm` | `ghcr.io/berriai/litellm:v1.103.1` | 4000 | default | `lab`, `ai-red-team` | 1.0 | 1g |
| `qdrant` | `qdrant/qdrant:v1.19.1` | 6333 | default | `lab` | 0.5 | 640m |
| `docker-socket-proxy` | `tecnativa/docker-socket-proxy:v0.5.0` | 2375 | default | `lab` | 0.25 | 64m |
| `mcp-gateway` | `docker/mcp-gateway:v0.44.1` | 8080 | default | `lab` | 0.5 | 192m |
| 14 Check Point MCP servers | `ghcr.io/alshawwaf/cp-agentic-n8n:${LAB_IMAGE_TAG}` | 3000 to 3013 | default | `lab` | 0.25 each | 128m each |
| `ips-cve-mcp` | `ghcr.io/alshawwaf/cp-agentic-ips-cve-mcp:${LAB_IMAGE_TAG}` | 3013 | `exercises` | `lab` | 0.25 | 128m |
| `vuln-mcp` | `python:3.12.14-alpine` | 3099 | `security-lab` | `security-lab` | 0.25 | 96m |
| `aig-webserver` | `zhuquelab/aig-server:v4.6.3` | 8088 | `ai-red-team` | `ai-red-team` | 1.0 | 1g |
| `aig-agent` | `zhuquelab/aig-agent:v4.6.3` | 8000 | `ai-red-team` | `ai-red-team`, `security-lab` | 1.5 | 2g |
| `aig-ui` | `nginxinc/nginx-unprivileged:1.30.5-alpine` | 8088 | `ai-red-team` | `aig-ui-access`, `ai-red-team` | 0.25 | 64m |
| `policypilot-mcp` | `${POLICYPILOT_IMAGE}` (default `policypilot:custom`) | 3020 | `policypilot` | `lab` | 0.5 | 512m |

The MCP servers are listed one by one in [MCP servers](#mcp-servers). Long-running services restart `unless-stopped`. The two credential-gated servers, `spark-management-mcp` and `harmony-sase-mcp`, use `restart: on-failure`: while they are not configured they print one "not configured" line, exit 0, and stay stopped.

Internal URLs on the `lab` network:

| Service | URL |
|---|---|
| n8n | `http://n8n:5678` |
| Flowise | `http://flowise:3020` (the `FLOWISE_PORT` value) |
| Langflow | `http://langflow:7860` |
| Open WebUI | `http://open-webui:8080` |
| Langfuse | `http://langfuse:3000` |
| LiteLLM (`lab-chat`) | `http://litellm:4000/v1` |
| Qdrant | `http://qdrant:6333` |
| Ollama | `http://ollama-cpu:11434` |
| MCP Gateway | `http://mcp-gateway:8080/mcp` |
| PostgreSQL | `postgres:5432` |

### One-shot jobs

One-shot jobs use `restart: "no"`. Every `docker compose up -d` runs them again, and they are safe to re-run.

| Service | Image | Profile | Networks | CPUs / memory | Starts after | What it does |
|---|---|---|---|---|---|---|
| `ollama-pull-models-cpu` | `ollama/ollama:0.31.1` | default | `lab` | 0.5 / 128m | `ollama-cpu` healthy | Pulls `nomic-embed-text`, plus `OLLAMA_CHAT_MODEL` when `lab-chat` runs on Ollama. Exit 1 when a model fails 5 tries |
| `ollama-pull-chat-models` | `ollama/ollama:0.31.1` | `local-chat`, `local-models`, `complete` | `lab` | 0.5 / 128m | `ollama-cpu` healthy | Pulls `OLLAMA_MODELS` |
| `n8n-provision` | `curlimages/curl:8.22.0` | default | `lab` | 0.25 / 64m | `n8n` healthy | Creates the n8n owner from the lab admin and checks the sign-in ([`scripts/n8n-provision.sh`](../scripts/n8n-provision.sh), mode `owner`) |
| `n8n-import` | lab image | default | `lab`, `security-lab` | 1.0 / 768m | `n8n-provision` succeeded | Seeds n8n (mode `import`, see [n8n-import](#n8n-import)) |
| `flowise-db-init` | `postgres:16.15-alpine` | default | `lab` | 0.25 / 128m | `postgres` healthy | Creates the `flowise` database and gives `./flowise_data` to uid 1000 |
| `langfuse-db-init` | `postgres:16.15-alpine` | default | `lab` | 0.25 / 128m | `postgres` healthy | Creates the `langfuse` database |
| `openwebui-provision` | `curlimages/curl:8.22.0` | `local-chat`, `complete` | `lab` | 0.25 / 64m | `open-webui` healthy | Creates the Open WebUI admin |
| `builders-import` | `python:3.12.14-alpine` | default | `lab`, `security-lab` | 0.5 / 256m | `flowise` healthy (and `langflow` when it runs) | Seeds Flowise and Langflow (see [builders-import](#builders-import)) |
| `rag-ingest` | `python:3.12.14-alpine` | default | `lab` | 0.5 / 256m | `qdrant` and `ollama-cpu` healthy | Embeds the RAG corpus into the Qdrant collection `cp_docs` |
| `evals-run` | `python:3.12.14-alpine` | `evals` | `lab` | 0.5 / 256m | `n8n-import` succeeded, `litellm` healthy | Scores the n8n agents (see [Evals](#evals)) |
| `aig-provision` | `curlimages/curl:8.22.0` | `ai-red-team` | `ai-red-team` | 0.25 / 64m | `aig-webserver` healthy | Registers `lab-chat` as a model in AI-Infra-Guard |

`docker compose ps -a` shows the one-shots. After a good start, each shows `Exited (0)`. `./scripts/doctor.sh --post-start` checks this for you.

### Start order

- `n8n` is healthy only when `http://localhost:5678/healthz/readiness` answers. n8n 2.41 opens its port before its database migrations finish, and an owner created that early is lost. The readiness check waits until the database is connected and migrated
- `mcp-gateway` waits for `docker-socket-proxy` and all 11 servers it fronts to be healthy, because it lists their tools only once, at start
- `builders-import` waits for Flowise, and for Langflow when the `langflow` or `complete` profile is on. Without Langflow it logs `Langflow is not running (add langflow to COMPOSE_PROFILES); skipped.` and succeeds
- `evals-run` waits for `n8n-import` and `litellm`

### Networks and containment

| Network | Type | Members | Purpose |
|---|---|---|---|
| `lab` | Created by Compose as `<project>_lab` | Every service except `vuln-mcp`, `aig-webserver`, `aig-agent` and `aig-provision` | The lab network. It has outbound internet access: model providers, Check Point cloud APIs, image and model pulls |
| `dokploy-network` | External (`external: true`) | `ollama-cpu`, `ollama-gpu`, `flowise`, `open-webui`, `langflow`, `langfuse` | The lab host's reverse-proxy network. `./setup.sh` creates an empty local one when it is missing |
| `security-lab` | Internal (`internal: true`) | `vuln-mcp`, `n8n`, `n8n-import`, `flowise`, `langflow`, `builders-import`, `aig-agent` | Only the vulnerable MCP server and its clients. No internet, no route to the Docker host |
| `ai-red-team` | Internal (`internal: true`) | `aig-webserver`, `aig-agent`, `aig-provision`, `aig-ui`, `litellm` | AI-Infra-Guard. LiteLLM joins only to serve `lab-chat`; `aig-ui` joins only to forward the web UI. No internet, no route to the Docker host |

- The Compose project name defaults to the directory name. In a clone named `cp-agentic-mcp-playground`, the lab network is `cp-agentic-mcp-playground_lab`. Find yours with `docker network ls | grep _lab`
- Ollama joins `dokploy-network` so that standalone apps on a lab host can reach `http://ollama-cpu:11434` by name. It still has no host port
- A container on an internal network has no default route. A live test from inside `vuln-mcp`, `aig-webserver` and `aig-agent` proved it: 1.1.1.1 on port 443, `host.docker.internal` (it does not resolve), the Docker host gateway, and github.com were all unreachable. See [Security lab and AI-Infra-Guard](#security-lab-and-ai-infra-guard)
- The network was named `demo` before the naming update. See [Upgrades](#upgrades)

### Volumes and folders

Compose prefixes named volumes with the project name, for example `cp-agentic-mcp-playground_n8n_storage`.

| Volume | Used by | Holds |
|---|---|---|
| `n8n_storage` | `n8n`, `n8n-import` | n8n settings and its encryption key file |
| `postgres_storage` | `postgres` | The `n8n` (`POSTGRES_DB`), `flowise` and `langfuse` databases |
| `ollama_storage` | `ollama-cpu`, `ollama-gpu` | Downloaded models |
| `langflow` | `langflow` | The Langflow database, secret key and flows (`LANGFLOW_CONFIG_DIR=/app/langflow`) |
| `open-webui` | `open-webui` | Open WebUI users, chats, settings and Functions |
| `qdrant_data` | `qdrant` | RAG vectors |
| `aig_data`, `aig_db`, `aig_logs`, `aig_uploads` | `aig-webserver` | AI-Infra-Guard state |
| `policypilot_data` | `policypilot-mcp` | The PolicyPilot sidecar database |

| Folder (bind mount) | Mounted into | Notes |
|---|---|---|
| `./n8n/shared` | `n8n` at `/data/shared`, `threat-emulation-mcp` at `/data/shared` (read-only), `cpinfo-analysis-mcp` at `/data/cpinfo`, `evals-run` at `/out` | Files for Threat Emulation and CPInfo Analysis, and the eval reports. Git-ignored |
| `./flowise_data` | `flowise` at `/home/node/.flowise`, `flowise-db-init` | Flowise encryption key, session secrets and uploads. Git-ignored |
| `./n8n/backup` | `n8n-import` at `/backup` (read-only) | The committed workflows and credential templates |
| `./scripts` | `n8n-provision`, `n8n-import`, `openwebui-provision` (read-only); `aig-provision` mounts only `aig-provision.sh` | Provisioning scripts |
| `./integrations` | `builders-import`, `evals-run` (read-only). Sub-folders: `litellm` into `litellm`, `rag-cp-docs` into `rag-ingest`, `mcp-security-lab` into `vuln-mcp` (all read-only) | Seeded flows, LiteLLM config script, RAG corpus, security lab server |
| `./certs` | The MCP servers at `/certs` (read-only), except `threat-emulation-mcp` and `cpinfo-analysis-mcp` | Certificates for self-signed servers (see [Certificates](#certificates-for-self-signed-servers)) |
| `./mcp-gateway/catalog.yaml` | `mcp-gateway` (read-only) | The gateway catalog |
| `/var/run/docker.sock` | `docker-socket-proxy` only (read-only) | Docker API, read through the proxy |

### Images and versions

- **The lab image.** `ghcr.io/alshawwaf/cp-agentic-n8n` runs `n8n`, `n8n-import` and the 14 MCP servers. [`docker/n8n/Dockerfile`](../docker/n8n/Dockerfile) builds it `FROM n8nio/n8n:2.41.5` (pinned by digest), with a `node:24.21.0-alpine` builder stage. Each `wrap` line puts one MCP server command in `/usr/local/bin`. The built npm tarballs stay in the image at `/opt/artifacts`
- **Vendored MCP sources.** All 14 servers are built from sources vendored from [CheckPointSW/mcp-servers](https://github.com/CheckPointSW/mcp-servers) (MIT) in [`docker/n8n/mcp-src/`](../docker/n8n/mcp-src/), with lab patches. Policy Insights was added from upstream commit `ef34749` (the npm 0.3.5 release). [`PATCHES.md`](../docker/n8n/mcp-src/PATCHES.md) lists every patch
- **Tags.** [`publish-image.yml`](../.github/workflows/publish-image.yml) publishes `:<commit sha>` for every build and moves `:latest` to the head of `main`. Both platforms, `linux/amd64` and `linux/arm64`, are built. `LAB_IMAGE_TAG` (default `latest`) chooses the tag. Set it to a commit SHA to pin one build
- **Pulls.** Every service sets `pull_policy: missing`: `docker compose up -d` uses an image that is already present and does not re-pull `latest` (`policypilot-mcp` needs its locally built image). `./update.sh` runs `docker compose pull` to get newer builds
- **Local build.** Build the lab image yourself with `docker build -t ghcr.io/alshawwaf/cp-agentic-n8n:local docker/n8n`, then set `LAB_IMAGE_TAG=local` in `.env` and run `docker compose up -d`
- **Check what runs.** `docker compose exec n8n n8n --version` prints the n8n version. The image's `org.opencontainers.image.revision` label holds the commit it was built from
- **Flowise.** The `3.1.4` image fixes the Custom MCP command-execution advisory (GHSA-vcwp-f9rq-3887). Because of upstream issue #6688, this image reports server version 3.1.2 and cannot start on SQLite, so Flowise always uses the lab Postgres (database `flowise`)
- **Langflow.** Pinned to `1.10.1` (CVE-2026-33017, an unauthenticated remote code execution flaw, affected builds before 1.9). The lab flows are validated against this version. Bump it deliberately
- **Langfuse.** `2.95.11` is the last v2 release: one container on the lab Postgres. Langfuse v3 needs ClickHouse, Redis and object storage
- **Qdrant.** `1.19.1` cannot open storage written by 1.12, so it uses a new volume, `qdrant_data`. `rag-ingest` fills it
- **Postgres.** `16.15`. A major upgrade needs a dump and restore. n8n logs that Postgres 16 has "compatibility support only"

### Local access and lab hosts

**On your own computer.** No port is published. To open an app in a browser, create `docker-compose.override.yml` next to `docker-compose.yml` and publish ports on `127.0.0.1` only. Git ignores this file, and Compose merges it automatically.

```yaml
services:
  n8n:        { ports: ["127.0.0.1:5678:5678"] }   # http://localhost:5678 (WEBHOOK_URL, N8N_EDITOR_BASE_URL)
  flowise:    { ports: ["127.0.0.1:3020:3020"] }   # http://localhost:3020 (FLOWISE_PORT)
  langfuse:   { ports: ["127.0.0.1:3100:3000"] }   # http://localhost:3100 (LANGFUSE_URL)
  langflow:   { ports: ["127.0.0.1:7860:7860"] }   # Complete lab: http://localhost:7860
  open-webui: { ports: ["127.0.0.1:8080:8080"] }   # Complete lab: http://localhost:8080
```

Then run `docker compose up -d`.

- Bind to `127.0.0.1`, never to all interfaces: the apps then stay off your network
- Never publish a port for `vuln-mcp`, `aig-webserver` or `aig-agent`. They sit only on internal networks on purpose. The AI-Infra-Guard web UI opens through `aig-ui` instead (see [AI-Infra-Guard](#ai-infra-guard-profile-ai-red-team))
- When `DOMAIN` is blank, setup sets `LANGFUSE_URL=http://localhost:3100`, so Langfuse sign-in redirects go to the port above
- An override file that names the network `demo` must use `lab` now

**On a lab host.** Set `DOMAIN` (for example `lab.example.com`), `N8N_HOST=n8n.<DOMAIN>`, `WEBHOOK_URL` and `N8N_EDITOR_BASE_URL` (`https://n8n.<DOMAIN>/`), and `COMPOSE_PROFILES=complete`.

| App | Address | Route defined by |
|---|---|---|
| n8n | `https://n8n.<DOMAIN>` | The lab host installer's Dokploy domain settings |
| Flowise | `https://flowise.<DOMAIN>` | The installer's Dokploy domain settings (compose sets only `traefik.enable` and the network) |
| Langflow | `https://langflow.<DOMAIN>` | The installer's Dokploy domain settings |
| Open WebUI | `https://chat.<DOMAIN>` | The installer's Dokploy domain settings |
| Langfuse | `https://trace.<DOMAIN>` | Traefik labels in `docker-compose.yml`: a `web` and a `websecure` router (`letsencrypt` resolver), and a middleware that lets only `https://hub.<DOMAIN>` embed Langfuse |

AI-Infra-Guard has no route: its web UI has no sign-in. On a lab host, the instructor reaches it through an SSH tunnel to the `127.0.0.1` port of `aig-ui` (see [AI-Infra-Guard](#ai-infra-guard-profile-ai-red-team)).

---

## Configuration

### How .env works

- [`.env-example`](../.env-example) is the template. `./setup.sh` writes `.env` from it. Compose reads `.env` from the project directory
- Format: one `NAME=value` per line, no spaces around `=`. A blank value means "not set"
- Compose takes a variable from the shell environment before `.env`. `doctor.sh` does the same when it checks settings
- Values may be 1Password references (`op://vault/item/field`). See [1Password](#1password)
- No service reads `.env` as a whole (`env_file` is never used). Compose passes each container only the variables it needs, so builders never see provider keys
- After a change: `docker compose up -d`. Compose recreates the services whose settings changed and runs the one-shot jobs again, so the importers re-sync the new values

### setup.sh

`./setup.sh` is a POSIX `sh` script. It runs with `/bin/sh` on macOS (BSD tools) and Linux (GNU, Debian `dash`, Alpine BusyBox).

| Mode | Command | What it does |
|---|---|---|
| Guided | `./setup.sh` | Asks questions. Typed keys are hidden. Each step can be skipped |
| 1Password | `./setup.sh --1password` (alias `--op`) | Guided, but writes `op://` references for your keys (see [1Password](#1password)) |
| Unattended | `./setup.sh --non-interactive` (aliases `-y`, `--ci`) | No questions. Keys come from environment variables with the names in `.env-example`. `COMPOSE_PROFILES` from the environment chooses the component set (blank = Standard lab). Used by CI and `update.sh` |
| Help | `./setup.sh --help` | Prints the usage |

`--1password` cannot be combined with `--non-interactive`. For unattended runs, put `op://` references in the environment or in `.env`.

The guided steps:

1. **Lab components.** Shows "This machine: N CPUs, M GB of memory" and "Docker can use X CPUs and Y GB", then offers the Standard lab and the Complete lab. Exactly one is marked "(recommended for this machine)". Choosing the Complete lab on a smaller machine asks for confirmation. Below the Standard minimum, setup prints the Docker Desktop > Settings > Resources hint
2. **Lab model.** Choose Azure OpenAI, OpenAI, Anthropic, Google Gemini, or the local model only. Azure asks for the key, endpoint and deployment name
3. **Check Point products.** Grouped by product: the Management server (on-premises or Smart-1 Cloud), Gaia, Documentation, Threat Emulation, Reputation Service, Spark Management, SASE. Setup checks that groups are complete
4. **Optional integrations and lab host.** Lakera Guard, the SCIM token, DevHub, PolicyPilot, the IPS key for the Build Your Own MCP exercise, and `DOMAIN`
5. **Lab admin and lab secrets.** The admin email and password, then every internal secret (see [Generated secrets](#generated-secrets))
6. **Write .env and check it.** Writes `.env` with mode 600, keeps the previous file as `.env.bak` (mode 600), creates missing external Docker networks, and runs `./scripts/doctor.sh --preflight`

Behavior on every run:

- Existing values are kept, blank ones are filled, and setup asks before it changes a value. A second run with nothing new leaves `.env` byte-identical (`.env is up to date (no changes).`)
- In unattended mode, a value already in `.env` wins over the environment: setup prints `Kept the value already in .env (the environment differs)` and names the settings
- Setup never prints a secret. It prints how to view the generated admin password: `grep '^N8N_ADMIN_PASSWORD=' .env`
- Settings that nothing reads any more are removed (see [Retired settings](#retired-settings)). Old defaults are offered for update, for example `ANTHROPIC_MODEL=claude-opus-4-8`, the former AI-Infra-Guard model settings, `COMPOSE_PROFILES=cpu` and lowercase `DOC_REGION`
- The published training values `cp-mcp-gateway-training-token` and `sk-cp-litellm-training-key` are always replaced with new random values
- Setup counts a lab as "started before" only when this project's `n8n_storage` or `postgres_storage` volume exists. For such a lab it does not generate a new `N8N_ENCRYPTION_KEY`, and it keeps (guided mode: asks before it replaces) every value the apps already use (see [Troubleshooting](#setup-and-start))
- It ends with the next steps, through `op run` when `.env` holds references. Exit 1 means the preflight check found blockers

### Admin password

One lab admin, `N8N_ADMIN_EMAIL` and `N8N_ADMIN_PASSWORD`, signs in to n8n, Flowise, Langflow, Langfuse and (unless `OPEN_WEBUI_ADMIN_*` is set) Open WebUI. The n8n chat pages use the same sign-in.

The password rule (n8n and Flowise both enforce parts of it):

- 8 to 64 characters
- At least one uppercase letter, one lowercase letter and one digit
- At least one of `-` `_` `.` `!` `@` `%`
- Only letters, digits and those six symbols

`./setup.sh` generates a password that passes, and `doctor.sh --preflight` checks it.

**Each app keeps the password it was created with.** After the first start, a new value in `.env` does not change the apps. `n8n-provision` then fails its sign-in check with `the owner cannot sign in with N8N_ADMIN_EMAIL / N8N_ADMIN_PASSWORD`, and `n8n-import` does not run. To change the password on a running lab:

1. Change it in each app: n8n, Flowise, Langflow, Langfuse and Open WebUI
2. Put the same value in `.env` (or in 1Password)
3. Run `docker compose up -d`. `n8n-import` re-syncs the "Lab Agents Chat" credential that protects the n8n chat pages

### Settings

The sections below follow `.env-example`. "Default" is the value in `.env-example`, or the value that applies when a setting is commented out there (shown as `# NAME`; set by Compose or by the service that reads it). `setup` means `./setup.sh` generates the value.

#### 1. Required: lab admin and internal secrets

`doctor.sh --preflight` requires these 15 settings: `N8N_ADMIN_EMAIL`, `N8N_ADMIN_PASSWORD`, `POSTGRES_USER`, `POSTGRES_DB`, `POSTGRES_PASSWORD`, `N8N_ENCRYPTION_KEY`, `N8N_USER_MANAGEMENT_JWT_SECRET`, `LITELLM_MASTER_KEY`, `MCP_GATEWAY_TOKEN`, `NEXTAUTH_SECRET`, `SALT`, `LANGFUSE_ENCRYPTION_KEY`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `WEBUI_SECRET_KEY`. Compose refuses to start without `POSTGRES_PASSWORD`, `N8N_ENCRYPTION_KEY`, `N8N_USER_MANAGEMENT_JWT_SECRET`, `NEXTAUTH_SECRET`, `SALT`, `LANGFUSE_ENCRYPTION_KEY` and `MCP_GATEWAY_TOKEN`, with the message `<NAME> is not set. Run ./setup.sh`.

| Setting | Default | Read by | Notes |
|---|---|---|---|
| `N8N_ADMIN_EMAIL` | `admin@lab.local` | `n8n-provision`, `n8n-import`, `builders-import`, `langflow`, `langfuse`, `openwebui-provision`, `evals-run` | The lab admin |
| `N8N_ADMIN_PASSWORD` | setup | Same as above | See [Admin password](#admin-password) |
| `N8N_ADMIN_FIRST_NAME`, `N8N_ADMIN_LAST_NAME` | `Lab`, `Admin` | `n8n-provision`; the first name also names the Langfuse user | |
| `OPEN_WEBUI_ADMIN_EMAIL`, `OPEN_WEBUI_ADMIN_PASSWORD` | blank | `openwebui-provision` | Blank = the lab admin |
| `POSTGRES_USER`, `POSTGRES_DB` | `admin`, `n8n` | `postgres`, `n8n`, `n8n-import`, `flowise`, the two DB init jobs, `langfuse` | Keep the names. `POSTGRES_DB` is the n8n database; `flowise` and `langfuse` sit next to it |
| `POSTGRES_PASSWORD` | setup | Same as above | Letters, digits and `. _ ~ -` only (it is part of the Langfuse database URL). Never change it after the first start |
| `N8N_ENCRYPTION_KEY` | setup | `n8n`, `n8n-import` | Encrypts n8n credentials. Never change it after the first start |
| `N8N_USER_MANAGEMENT_JWT_SECRET` | setup | `n8n` | Signs n8n sessions |
| `MCP_GATEWAY_TOKEN` | setup | `mcp-gateway` (as `MCP_GATEWAY_AUTH_TOKEN`), `n8n-import`, `builders-import` | The Bearer token for `http://mcp-gateway:8080/mcp`. At least 16 characters. Safe to change: the importers re-sync it |
| `NEXTAUTH_SECRET`, `SALT` | setup | `langfuse` | Never change `SALT` after the first start |
| `LANGFUSE_ENCRYPTION_KEY` | setup | `langfuse` | 64 hex characters. Never change it after the first start |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | setup | `langfuse` (first-start project keys), `litellm`, `builders-import` | `pk-lf-...` and `sk-lf-...` |
| `WEBUI_SECRET_KEY` | setup | `open-webui` | Signs Open WebUI sessions, so recreating the container does not sign everyone out |

#### 2. Lab model

See [Model (lab-chat)](#model-lab-chat) for how these work together.

| Setting | Default | Read by | Notes |
|---|---|---|---|
| `LAB_MODEL_PROVIDER` | `auto` | `litellm`, `ollama-pull-models-cpu` | `auto`, `azure`, `openai`, `anthropic`, `gemini` or `ollama` |
| `LITELLM_MASTER_KEY` | setup (`sk-` and 48 hex characters) | `litellm`, `n8n-import`, `builders-import`, `aig-provision`, `aig-agent` | Required. The key every agent sends to `lab-chat`. `doctor.sh` requires `sk-` and at least 19 characters |
| `AZURE_OPENAI_API_KEY` | blank | `litellm` | Azure needs all three Azure settings |
| `AZURE_OPENAI_ENDPOINT` | blank | `litellm` | `https://<resource>.openai.azure.com`, or an API Management gateway URL with its path |
| `AZURE_OPENAI_DEPLOYMENT` | blank | `litellm` | The deployment name, not the model name. Name it after the model, for example `gpt-5.1` |
| `# AZURE_OPENAI_API_VERSION` | `2024-10-21` | `litellm` | `v1` only if your endpoint accepts a Bearer token on `/openai/v1` |
| `# LAB_REASONING_EFFORT` | `auto` | `litellm` | `auto`, `none`, `minimal`, `low`, `medium` or `high`. See [Azure OpenAI notes](#azure-openai-notes) |
| `OPENAI_API_KEY` | blank | `litellm` | |
| `# OPENAI_MODEL` | `gpt-5.1` | `litellm` | |
| `# OPENAI_BASE_URL` | blank | `litellm` | A proxy or OpenAI-compatible server. A URL with only scheme and host gets `/v1` added |
| `ANTHROPIC_API_KEY` | blank | `litellm` | |
| `# ANTHROPIC_MODEL` | `claude-sonnet-5` | `litellm` | |
| `GEMINI_API_KEY` | blank | `litellm` | A Google AI Studio key |
| `# GEMINI_MODEL` | `gemini-2.5-flash` | `litellm` | |
| `# OLLAMA_CHAT_MODEL` | `qwen3.5:4b` | `litellm`, `ollama-pull-models-cpu` | The local model |
| `# OLLAMA_API_BASE` | `http://ollama-cpu:11434` | `litellm`, `open-webui` | Set `http://ollama-gpu:11434` for the `gpu-nvidia` profile |
| `# OLLAMA_NUM_CTX` | `32768` | `litellm` | Context window in tokens. 2048 to 262144; below 16384 the agents with many tools lose their instructions |
| `# OLLAMA_THINK` | `false` | `litellm` | `false`, `true` or `auto` (the model default) |
| `# LANGFUSE_HOST` | `http://langfuse:3000` | `litellm`, `builders-import` | |

#### 3. Check Point products

Each product is optional. An agent whose product is not set answers that it is not configured. See [MCP servers](#mcp-servers).

| Setting | Default | Read by | Notes |
|---|---|---|---|
| `MANAGEMENT_HOST` | blank | The seven Management-backed servers | On-premises Security Management Server or Multi-Domain Server: host name or IP only (no `https://`), reachable from the Docker host |
| `MANAGEMENT_PORT` | `443` | Same | Management API port |
| `S1C_URL` | blank | Same | Smart-1 Cloud Web API URL, for example `https://<tenant>.maas.checkpoint.com/<id>/web_api`. Leave `MANAGEMENT_HOST` blank: when both are set, the servers use `MANAGEMENT_HOST` |
| `MANAGEMENT_API_KEY` | blank | Same (as `API_KEY`) | Smart-1 Cloud accepts an API key only |
| `MANAGEMENT_USERNAME`, `MANAGEMENT_PASSWORD` | blank | Same (as `USERNAME`, `PASSWORD`) | On-premises only. Set both or neither. An API key wins when both are set |
| `# MANAGEMENT_CA_CERT` | blank | Same | For example `/certs/sms.pem`. See [Certificates](#certificates-for-self-signed-servers) |
| `# MANAGEMENT_TLS_SERVERNAME` | blank | Same | The host name the certificate names, when you connect by IP |
| `GAIA_GATEWAY_IP`, `GAIA_GATEWAY_PORT`, `GAIA_USERNAME`, `GAIA_PASSWORD` | blank, `443`, blank, blank | `quantum-gaia-mcp` | One Security Gateway's Gaia REST API. IP address or host name |
| `# GAIA_ALLOWED_GATEWAYS` | blank | `quantum-gaia-mcp` | More gateways that may receive the Gaia credentials, comma-separated |
| `# GAIA_CA_CERT`, `# GAIA_TLS_SERVERNAME` | blank | `quantum-gaia-mcp` | Self-signed gateway certificate, for example `/certs/gateway.pem` |
| `DOC_CLIENT_ID`, `DOC_SECRET_KEY` | blank | `mcp-documentation` | A Check Point portal API key (portal.checkpoint.com: Global Settings > API Keys > New) |
| `DOC_REGION` | `EU` | `mcp-documentation` | `EU` or `US` |
| `TE_API_KEY` | blank | `threat-emulation-mcp` | Threat Prevention API key for the Threat Emulation service |
| `REPUTATION_API_KEY` | blank | `reputation-service-mcp` | |
| `SPARK_MGMT_CLIENT_ID`, `SPARK_MGMT_SECRET_KEY` | blank | `spark-management-mcp` | The server stops as "not configured" while one is blank |
| `SPARK_MGMT_REGION`, `SPARK_MGMT_INFINITY_PORTAL_URL` | `US`, `https://cloudinfra-gw-us.portal.checkpoint.com/auth/external` | `spark-management-mcp` | |
| `HARMONY_SASE_API_KEY`, `HARMONY_SASE_MANAGEMENT_HOST`, `HARMONY_SASE_ORIGIN` | blank | `harmony-sase-mcp` | SASE administrator portal: Settings > API support. All three are needed |
| `IPS_CLIENT_ID`, `IPS_ACCESS_KEY` | blank | `ips-cve-mcp` | Profile `exercises`. A Check Point portal API key for the IPS service |
| `# IPS_REGION` | `EU` | `ips-cve-mcp` | `EU` or `US` |
| `# IPS_CVE_VARIANT` | `solution` | `ips-cve-mcp` build | `scaffold` builds the unfinished exercise |
| `IPS_AUTH_URL`, `IPS_SERVICE_URL` | blank | `ips-cve-mcp` | Optional host overrides; set both for the same region |
| `# IPS_MCP_BEARER_TOKEN` | blank | `ips-cve-mcp` | Optional Bearer token the server requires from its clients. Leave it blank while the MCP Gateway fronts the server |

#### 4. Optional integrations

| Setting | Default | Read by | Notes |
|---|---|---|---|
| `LAKERA_API_KEY` | blank | `n8n-import`, `builders-import` | Lakera Guard. Without it, "Guarded Agent (Lakera Guard)" and "Lakera Guard Screening Agent" block every message and explain how to set it |
| `LAKERA_PROJECT_ID` | blank | `n8n-import` | Optional Lakera project, so Guard applies that project's policy |
| `IDP_SCIM_TOKEN` | blank | `n8n-import`, `builders-import` | "Identity Provisioning Agent (SCIM)". Also needs `DOMAIN` |
| `DEVHUB_MCP_TOKEN` | blank | `n8n-import`, `builders-import` | "DevHub Operations Agent" (`https://hub.<DOMAIN>/api/mcp`) |
| `PILOT_MCP_TOKEN` | blank | `n8n-import`, `builders-import`, `policypilot-mcp` | The PolicyPilot agents (`https://policypilot.<DOMAIN>/mcp`) |
| `POLICYPILOT_IMAGE` | `policypilot:custom` | `policypilot-mcp` | A locally built image |
| `PILOT_ENCRYPTION_KEY` | blank | `policypilot-mcp` | Must match the key the PolicyPilot portal used |
| `PILOT_SESSION_SECRET` | setup | `policypilot-mcp` | Used only while `PILOT_ENCRYPTION_KEY` is blank |
| `PILOT_DATABASE_URL` | `sqlite:////data/policypilot.db` | `policypilot-mcp` | |
| `QDRANT_API_KEY` | blank | `qdrant`, `rag-ingest`, `n8n-import`, `builders-import` | Blank = no authentication on the internal network. Set it to require the key; every lab client then sends it |
| `# RAG_MIN_SCORE` | `0.5` | `rag-ingest` | The threshold the `--search` probe reports against (see [Visible RAG](#visible-rag)) |

#### 5. Lab host

| Setting | Default | Read by | Notes |
|---|---|---|---|
| `DOMAIN` | blank | `langfuse` (routes, sign-in URL, embedding), `n8n-import`, `builders-import` | Blank on your own computer. Agents that need it are imported but not published |
| `N8N_HOST` | `localhost` | `n8n-import`, `builders-import` | When `DOMAIN` is blank and `N8N_HOST` is `n8n.<domain>`, both importers use `<domain>`. A loopback name never becomes a domain |
| `WEBHOOK_URL`, `N8N_EDITOR_BASE_URL` | `http://localhost:5678/` | `n8n` | Compose passes `WEBHOOK_URL` as both `WEBHOOK_URL` and `N8N_WEBHOOK_URL` |
| `LANGFUSE_URL` | blank (setup writes `http://localhost:3100` when `DOMAIN` is blank) | `langfuse` (`NEXTAUTH_URL`) | Blank = `https://trace.<DOMAIN>` |
| `FLOWISE_PORT` | `3020` | `flowise`, `builders-import` | The lab host's reverse proxy uses 3020 |

#### 6. Advanced

| Setting | Default | Read by | Notes |
|---|---|---|---|
| `COMPOSE_PROFILES` | blank | Docker Compose | See [Profiles](#profiles) |
| `LAB_IMAGE_TAG` | `latest` | `n8n`, `n8n-import`, the MCP servers, `ips-cve-mcp` | A commit SHA pins one build |
| `SEED_OVERWRITE` | `0` | `builders-import` | `1` for one run replaces edited Flowise and Langflow agents |
| `N8N_SEED_OVERWRITE` | `0` | `n8n-import` | `1` for one run replaces edited n8n workflows |
| `NIGHTLY_SELF_QA` | `0` | `n8n-import` | `1` publishes the "Nightly Agent Self-Check" workflow |
| `GENERIC_TIMEZONE` | `UTC` | `n8n` (also `TZ`), `aig-webserver`, `aig-agent` | Time zone of n8n schedules |
| `OLLAMA_MODELS` | `qwen3.5:4b,nomic-embed-text` | `ollama-pull-chat-models` | What the `complete`, `local-chat` and `local-models` profiles pull |
| `OLLAMA_KEEP_ALIVE` | `10m` | `ollama-cpu`, `ollama-gpu` | How long an idle model stays loaded (`-1` = forever) |
| `OLLAMA_MAX_LOADED_MODELS` | `2` | `ollama-cpu`, `ollama-gpu` | |
| `OLLAMA_MEM_LIMIT` | `1g` | `ollama-cpu` | `1g` holds the embedding model only. A local chat model needs `6g` |
| `OLLAMA_CPUS` | `2` | `ollama-cpu` | |
| `OPEN_WEBUI_DEFAULT_MODELS` | `qwen3.5:4b` | `open-webui` | Must be in `OLLAMA_MODELS`. Open WebUI stores it on its first start |
| `LANGFLOW_AUTO_LOGIN` | `false` | `langflow` | `true` removes the Langflow sign-in. Keep `false` whenever others can reach the lab |
| `FLOWISE_API_KEY`, `LANGFLOW_API_KEY` | blank | `builders-import` | Optional keys the importer uses instead of the admin sign-in |
| `LANGFUSE_DATABASE_URL` | blank | `langfuse` | Blank = the lab Postgres. A `postgresql://` URL only for another server |
| `LANGFUSE_ORG_ID` | `check-point-agentic-lab` | `langfuse` | Used on the first start only. Setup writes `cp-playground` for labs created before this setting existed |
| `LANGFUSE_TELEMETRY_ENABLED` | `false` | `langfuse` | |
| `N8N_PUSH_BACKEND` | `websocket` | `n8n` | |
| `CPINFO_LOG_LEVEL` | `info` | `cpinfo-analysis-mcp` | |
| `# EVALS_BASE_URL` | `http://n8n:5678` | `evals-run` | For a remote lab, an `https://` URL |
| `# AIG_LLM_MODEL`, `# AIG_LLM_BASE_URL`, `# AIG_LLM_API_KEY` | `lab-chat`, `http://litellm:4000/v1`, `LITELLM_MASTER_KEY` | `aig-agent` | The scanner's model. Keep the defaults |

Compose also reads one setting that `.env-example` does not list: `RUG_PULL_RESET_SECONDS` (default `600`, `vuln-mcp`), the seconds after the last `currency_convert` call before the Security Lab rug pull turns clean again.

---

## Secrets and 1Password

### Generated secrets

`./setup.sh` generates every internal secret that is blank: `POSTGRES_PASSWORD`, `N8N_ENCRYPTION_KEY`, `N8N_USER_MANAGEMENT_JWT_SECRET`, `N8N_ADMIN_PASSWORD`, `LITELLM_MASTER_KEY`, `MCP_GATEWAY_TOKEN`, `NEXTAUTH_SECRET`, `SALT`, `LANGFUSE_ENCRYPTION_KEY`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `WEBUI_SECRET_KEY` and `PILOT_SESSION_SECRET`.

- Values come from `/dev/urandom`. `.env` is written with mode 600 (`umask 077`), and no secret is printed
- A placeholder such as `change_me` or an invalid format counts as blank on a lab that has not started. On a lab that has started, setup keeps such a value for the settings the apps already use (`POSTGRES_PASSWORD`, `N8N_ENCRYPTION_KEY`, `N8N_ADMIN_PASSWORD`, `NEXTAUTH_SECRET`, `SALT`, `LANGFUSE_ENCRYPTION_KEY` and the Langfuse key pair), because a new value would lock the apps out of their data. The guided mode asks first
- For a hand-made `.env`, `./setup.sh --non-interactive` fills the blanks. [`integrations/observability/gen_secrets.py`](../integrations/observability/gen_secrets.py) fills only the Langfuse secrets and `LITELLM_MASTER_KEY` (`--dry-run` lists what it would set)

### 1Password

`./setup.sh --1password` keeps your keys in 1Password. `.env` holds references, and `op run` resolves them when the lab starts.

1. Install the 1Password CLI (`op`) and sign in
2. Run `./setup.sh --1password`. It asks for the vault (default `Private`) and the item (default `checkpoint-ai-lab`). Each key you choose becomes `op://<vault>/<item>/<SETTING NAME>`
3. At the end, setup lists the fields to create in that item. Field name = setting name, for example `MANAGEMENT_API_KEY`
4. Optional: setup offers to store the generated lab secrets in a new item `<item>-secrets` (default No). It needs `op` signed in
5. Start, check and test the lab through `op run`:

```sh
op run --env-file=.env -- docker compose up -d
op run --env-file=.env -- ./scripts/doctor.sh --post-start
op run --env-file=.env -- tests/acceptance/run.sh
```

- `./update.sh` uses `op run` by itself when `.env` holds `op://` references
- Compose never passes `.env` as a whole into a container, so an unresolved `op://` string never reaches a service unnoticed. `doctor.sh --post-start` fails with `unresolved 1Password references inside:` when a container got one, and LiteLLM refuses to start with `holds an unresolved 1Password reference (op://...)`
- `doctor.sh --preflight` lists the references it cannot resolve in the current shell and tells you to run it under `op run`. It fails only when `op` is not installed
- The Complete lab with an Azure OpenAI model was tested with `op://` references in `.env` and `op run --env-file=.env -- docker compose up -d`

### Changing a key

| What | Do this |
|---|---|
| A model provider key or provider | Edit `.env` (or 1Password), then `docker compose up -d litellm`. The agents do not change |
| `MCP_GATEWAY_TOKEN` | Edit `.env`, then `docker compose up -d`. The gateway and both importers pick up the new value. An agent you edited in a builder keeps the old token until you re-seed it (see [Edited agents](#edited-agents)) |
| `LITELLM_MASTER_KEY` | Edit `.env`, then `docker compose up -d`. LiteLLM and both importers pick it up |
| `LAKERA_API_KEY`, `IDP_SCIM_TOKEN`, `DEVHUB_MCP_TOKEN`, `PILOT_MCP_TOKEN`, `QDRANT_API_KEY` | Edit `.env`, then `docker compose up -d`. To re-sync by hand: `docker compose run --rm n8n-import` and `docker compose run --rm builders-import` |
| A Check Point product key | Edit `.env`, then `docker compose up -d` (or `docker compose up -d <service>`) |
| `N8N_ADMIN_PASSWORD` | See [Admin password](#admin-password) |
| `POSTGRES_PASSWORD`, `N8N_ENCRYPTION_KEY`, `SALT`, `LANGFUSE_ENCRYPTION_KEY` | Do not change them after the first start |

`.env` is the source of truth. The importers re-sync credentials and variables from it on every run and replace edits made in the builders' UI.

### Security notes

This is a training lab. Keep it on your own computer or a lab host.

- **Keep `.env` private.** It holds every key. Git ignores `.env`, `.env.*` (except `.env-example`), `*.pem`, `*.key`, `./flowise_data`, `./n8n/shared` and `backups/`. Never commit them
- **No published ports.** Reach the apps through a lab host's reverse proxy or a `127.0.0.1` override
- **Sign-in everywhere.** The n8n chat pages use HTTP Basic auth with the lab admin (credential "Lab Agents Chat"). The Flowise prediction API needs the API key "Lab Agents API". Langflow sign-in is on. Langfuse sign-up is off. Open WebUI gets its admin at deploy time, and sign-up then closes
- **The builders never hold provider keys.** Only `litellm` gets them. The KEYS acceptance check proves it
- **Gateway token in Langflow.** Flowise reads the gateway token from its variable `MCP_GATEWAY_TOKEN`. Langflow has no variable for MCP headers, so `builders-import` writes the token into each seeded Langflow gateway flow. Anyone who can open those flows can read it
- **Flowise variables** are stored unencrypted in the Flowise database
- **Flowise address guard.** `HTTP_SECURITY_CHECK=false` lets Flowise reach the lab network. `HTTP_DENY_LIST` still denies loopback, link-local (cloud metadata) and multicast addresses. `CUSTOM_MCP_PROTOCOL=sse` stops flows from starting local MCP commands
- **Unauthenticated internals.** The MCP servers and Qdrant (with `QDRANT_API_KEY` blank) rely on the lab network. The gateway always needs its token
- **n8n cookies.** `N8N_SECURE_COOKIE=false`, because the lab serves n8n over plain HTTP inside the network
- **Docker access.** Only `docker-socket-proxy` mounts the Docker socket, read-only, and it refuses every write (see [Docker access](#docker-access))
- **TLS.** The Check Point MCP servers always verify certificates. Never turn verification off; add the certificate instead (see [Certificates](#certificates-for-self-signed-servers))
- **Data handling.** Tool results (rulebases, objects, logs, IP addresses) go to the model provider behind `lab-chat`. With a cloud provider, that is an external service. Use lab data only. Never send customer configurations or telemetry to a provider that is not approved for that data
- **Hardening for wider use.** See the [Production Deployment Guide](operations/PRODUCTION_DEPLOYMENT.md)

---

## Model (lab-chat)

Every seeded agent in n8n, Flowise and Langflow, the code-first agent and AI-Infra-Guard use one model name, `lab-chat`, at `http://litellm:4000/v1`, with the key `LITELLM_MASTER_KEY`. The evals reach it through the n8n agents. LiteLLM forwards `lab-chat` to the provider you choose. Embeddings for RAG stay on the local Ollama model `nomic-embed-text` and need no key.

| Builder | Model node | Credential or key |
|---|---|---|
| n8n | OpenAI Chat Model (`lmChatOpenAi`), model `lab-chat` | Credential "Lab Model (LiteLLM)" (`http://litellm:4000/v1`) |
| Flowise | ChatOpenAI, Base Path `http://litellm:4000/v1`, model `lab-chat` | Credential "Lab Model (LiteLLM)" |
| Langflow | OpenAIModel, `openai_api_base` `http://litellm:4000/v1`, model `lab-chat` | Global variable `LITELLM_MASTER_KEY` |

### How LiteLLM starts

The `litellm` service runs [`integrations/litellm/render_config.py`](../integrations/litellm/render_config.py) as its entrypoint. The script reads the settings that Compose passes from `.env`, picks the provider, writes the LiteLLM config to a tmpfs (`/tmp/lab-litellm/config.yaml`, mode 600), logs one line, and starts LiteLLM.

- The config holds no secret values: keys are `os.environ/NAME` references that LiteLLM resolves itself
- The log line names what serves `lab-chat`, for example `[lab-litellm] lab-chat -> local Ollama model qwen3.5:4b at http://ollama-cpu:11434, context 32768 tokens, thinking off (chosen automatically: no cloud model key is set). Langfuse tracing on (http://langfuse:3000).` See it with `docker compose logs litellm | grep lab-litellm`
- A setting error stops LiteLLM with `[lab-litellm] ERROR: ...` naming the variable, then `LiteLLM is not started, so lab-chat stays unavailable until this is fixed.`
- [`integrations/litellm/lab_auth.py`](../integrations/litellm/lab_auth.py) answers a wrong or missing key with HTTP 401 `Invalid or missing LiteLLM key. Send the LITELLM_MASTER_KEY value from the lab .env as 'Authorization: Bearer <key>'.` Refused requests stay out of Langfuse
- LiteLLM has no host port, no admin UI (`DISABLE_ADMIN_UI=True`) and no database. It uses its bundled model cost map (`LITELLM_LOCAL_MODEL_COST_MAP=True`), sends no telemetry, and makes no retries (`num_retries: 0`): the builders retry themselves
- `drop_params: true` drops parameters a model rejects, for example `temperature` on reasoning models

Commands:

```sh
docker compose up -d litellm                                # apply a change in .env
docker compose run --rm --no-deps litellm --check           # validate the settings, start nothing (exit 2 = error)
docker compose run --rm --no-deps litellm --print           # print the rendered config (no secrets)
docker compose exec litellm cat /tmp/lab-litellm/config.yaml # the config in use
```

`doctor.sh --preflight` runs the `--check` command for you.

### Provider selection

`LAB_MODEL_PROVIDER=auto` (the default) uses the first provider that is fully set, in this order:

| Order | Provider | Needs | Model setting (default) |
|---|---|---|---|
| 1 | Azure OpenAI | `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT` | `AZURE_OPENAI_DEPLOYMENT` (no default); `AZURE_OPENAI_API_VERSION` (`2024-10-21`) |
| 2 | OpenAI | `OPENAI_API_KEY` | `OPENAI_MODEL` (`gpt-5.1`); optional `OPENAI_BASE_URL` |
| 3 | Anthropic | `ANTHROPIC_API_KEY` | `ANTHROPIC_MODEL` (`claude-sonnet-5`) |
| 4 | Google Gemini | `GEMINI_API_KEY` | `GEMINI_MODEL` (`gemini-2.5-flash`) |
| 5 | Local Ollama | No key | `OLLAMA_CHAT_MODEL` (`qwen3.5:4b`) |

- A blank value, a placeholder or an unresolved `op://` reference counts as not set
- In `auto`, a partly set Azure configuration is skipped with a warning (`Azure OpenAI is only partly configured`)
- Set `azure`, `openai`, `anthropic`, `gemini` or `ollama` to force one. LiteLLM then refuses to start, naming the missing variable, when that provider is not set. It never falls back to another provider silently
- Anthropic: the lab drops `temperature`, `top_p` and `top_k` (current Claude models reject them) and sets `max_tokens` to 16384 unless the client sends its own
- `LITELLM_MASTER_KEY` is required, at least 16 characters, with no spaces. The old public value `sk-cp-litellm-training-key` is refused

### Azure OpenAI notes

These notes come from the test with a real Azure OpenAI deployment behind an Azure API Management gateway.

- **Endpoint.** Paste the resource URL (`https://<resource>.openai.azure.com`) or any URL copied from the portal. The lab keeps the base URL and cuts only a whole `/openai` path segment. A gateway path is kept: `https://apim.example.net/my-api/openai/v1` becomes `https://apim.example.net/my-api`, and a path such as `/openai-prod` stays intact
- **API version.** The default `2024-10-21` (dated GA) uses the deployment path and the `api-key` header, which Azure resources and API Management gateways accept. `v1` makes LiteLLM send a Bearer token. An API Management gateway then answers `Access denied due to missing subscription key`. Set `v1` only for an endpoint that accepts a Bearer token on `/openai/v1`
- **Reasoning and tools.** GPT-5.4 and later reject function tools on Chat Completions while reasoning is on (their default). LiteLLM then switches those calls to the Responses API (`/openai/responses`), which many gateways do not serve. `LAB_REASONING_EFFORT=auto` (the default) sends `reasoning_effort=none` for GPT-5 models, so every tool-using agent stays on Chat Completions
- **Deployment names.** `auto` detects GPT-5 from the deployment name (`gpt-5` or `gpt5`). If your deployment name does not contain it, set `LAB_REASONING_EFFORT=none`
- **Other levels.** `minimal`, `low`, `medium` and `high` need an endpoint that serves the Responses API, and `AZURE_OPENAI_API_VERSION=v1`

### Ollama: local fallback and memory

With no cloud key, `lab-chat` runs on the local Ollama model. It works with no key, but slowly on a CPU.

| Setting | Default | Effect |
|---|---|---|
| `OLLAMA_MEM_LIMIT` | `1g` | Holds the embedding model only. A local chat model needs `6g`. Setup sets `6g` when `lab-chat` runs on Ollama, and for the `complete`, `local-chat` and `local-models` profiles |
| `OLLAMA_CPUS` | `2` | CPUs Ollama may use |
| `OLLAMA_NUM_CTX` | `32768` | Fleet Commander Estate Agent sends about 18,000 tokens of tool descriptions. With Docker at 8 GB of memory you can lower it to 16384 |
| `OLLAMA_THINK` | `false` | Thinking makes local answers much slower |
| `OLLAMA_KEEP_ALIVE` | `10m` | An idle model is unloaded after 10 minutes |
| `OLLAMA_NUM_PARALLEL` | `1` (fixed in compose) | One request at a time per model |

- `ollama-pull-models-cpu` pulls `nomic-embed-text` (274 MB). It also pulls `OLLAMA_CHAT_MODEL` when `lab-chat` runs on Ollama. It logs which case applies
- Measured in the Standard lab test with `qwen3.5:4b`: a completion with a tool call took about nine seconds, and Ollama ran at its caps (2 CPUs, about 5.7 of 6 GiB). 6 GiB is tight for a local-only lab. A cloud key makes every agent much faster
- With `OLLAMA_MEM_LIMIT` below `6g` and a local chat model, `doctor.sh` warns: `Ollama cannot load a chat model. Set OLLAMA_MEM_LIMIT=6g in .env, then docker compose up -d`
- Larger models (for example `qwen3.5:9b`) need a GPU or much more memory
- GPU hosts: add `gpu-nvidia` to `COMPOSE_PROFILES` and set `OLLAMA_API_BASE=http://ollama-gpu:11434`. `ollama-gpu` shares the model volume. `rag-ingest` still embeds through `ollama-cpu`

Manage models inside the container:

```sh
docker compose exec ollama-cpu ollama list
docker compose exec ollama-cpu ollama pull qwen3.5:4b
```

### Open WebUI

Open WebUI (Complete lab, or the `local-chat` profile) chats with the local Ollama models at `OLLAMA_API_BASE` (default `http://ollama-cpu:11434`). It does not use `lab-chat` and is not traced. The default model for new chats is `OPEN_WEBUI_DEFAULT_MODELS` (`qwen3.5:4b`). Follow-up, tag and autocomplete generations are off, so they do not queue behind chats on a CPU. No n8n pipe ships with the lab.

---

## MCP servers

The 14 Check Point MCP servers run from the lab image. Each listens on Streamable HTTP on the `lab` network only. "Management access" means `MANAGEMENT_HOST` (or `S1C_URL`) plus `MANAGEMENT_API_KEY` (or, on-premises only, `MANAGEMENT_USERNAME` and `MANAGEMENT_PASSWORD`).

| Service | Direct URL | Product | Credentials | Gateway name | Tools |
|---|---|---|---|---|---|
| `mcp-documentation` | `http://mcp-documentation:3000` | Documentation | `DOC_CLIENT_ID`, `DOC_SECRET_KEY`, `DOC_REGION` | `documentation` | 1 |
| `mcp-https-inspection` | `http://mcp-https-inspection:3001` | HTTPS Inspection | Management access | `https-inspection` | 9 |
| `mcp-quantum-management` | `http://mcp-quantum-management:3002` | Management | Management access | `quantum-management` | 50 |
| `mcp-management-logs` | `http://mcp-management-logs:3003` | Management Logs | Management access | `management-logs` | 7 |
| `threat-emulation-mcp` | `http://threat-emulation-mcp:3004` | Threat Emulation | `TE_API_KEY` | `threat-emulation` | 5 |
| `threat-prevention-mcp` | `http://threat-prevention-mcp:3005` | Threat Prevention | Management access | `threat-prevention` | 25 |
| `spark-management-mcp` | `http://spark-management-mcp:3006` | Spark Management | `SPARK_MGMT_CLIENT_ID`, `SPARK_MGMT_SECRET_KEY` | Not fronted | Stops until configured |
| `reputation-service-mcp` | `http://reputation-service-mcp:3007` | Reputation Service | `REPUTATION_API_KEY` | `reputation-service` | 3 |
| `harmony-sase-mcp` | `http://harmony-sase-mcp:3008` | SASE | `HARMONY_SASE_API_KEY`, `HARMONY_SASE_MANAGEMENT_HOST`, `HARMONY_SASE_ORIGIN` | Not fronted | Stops until configured |
| `quantum-gw-cli-mcp` | `http://quantum-gw-cli-mcp:3009` | Gateway CLI | Management access | `gw-cli` | 26 |
| `quantum-gw-connection-analysis-mcp` | `http://quantum-gw-connection-analysis-mcp:3010` | Gateway Connection Analysis | Management access | Not fronted | 2 |
| `quantum-gaia-mcp` | `http://quantum-gaia-mcp:3011/mcp` | Gaia | `GAIA_*` | `gaia` | 42 |
| `cpinfo-analysis-mcp` | `http://cpinfo-analysis-mcp:3012` | CPInfo Analysis | None (reads files) | `cpinfo-analysis` | 12 |
| `policy-insights-mcp` | `http://policy-insights-mcp:3013` | Policy Insights | Management access | `policy-insights` | 10 |

- The 11 fronted servers list 190 tools in total, the same set directly and through the gateway. `doctor.sh --post-start` and the GW-TOOLS and DIRECT acceptance checks compare the counts with `SERVER_TOOLS` in [`scripts/flows/langflow_fix.py`](../scripts/flows/langflow_fix.py)
- Gaia is the only server reached at `/mcp`. The others answer at their root URL
- The seven Management-backed servers are Management, Management Logs, Threat Prevention, HTTPS Inspection, Policy Insights, Gateway CLI and Gateway Connection Analysis. CPInfo Analysis and Gaia never receive Management credentials
- Policy Insights needs Management API v2.1 (R82.10 or later) with Policy Insights enabled on the Security Management Server. Its upstream telemetry is removed; the upstream options `--no-telemetry` and `--telemetry-url` are rejected
- Gateway Connection Analysis, Spark Management and SASE are not fronted by the gateway, and no seeded agent uses them
- Every server runs with `cap_drop: ALL` and `no-new-privileges`, and has a TCP health check

### Management access

- **On-premises.** `MANAGEMENT_HOST` is a host name or IP only, reachable from the Docker host. In a CloudShare lab, use the server's public IP, not its `10.1.1.x` lab address, and make sure replies route back. `MANAGEMENT_PORT` defaults to 443
- **Smart-1 Cloud.** Set `S1C_URL` (the tenant's Web API URL, without `/login`) and `MANAGEMENT_API_KEY`. Smart-1 Cloud accepts an API key only. Find both in the Smart-1 Cloud portal: Settings > API & SmartConsole
- **API key on-premises.** SmartConsole > Manage & Settings > Permissions & Administrators > Administrators > Authentication Method: API Key
- **Missing credentials.** A management host without credentials does not crash the server. It starts without management access and logs one line, for example `WARNING: MANAGEMENT_HOST is set without MANAGEMENT_API_KEY (or MANAGEMENT_USERNAME and MANAGEMENT_PASSWORD), so the Management MCP server starts without management access.` `doctor.sh --preflight` warns about the same case
- **Reachability.** `./scripts/doctor.sh --preflight --online` tests TCP reachability of the Check Point hosts

### Certificates for self-signed servers

The Check Point MCP servers always verify TLS certificates. For a Security Management Server or Security Gateway with a self-signed certificate, follow [`certs/README.md`](../certs/README.md):

1. Save the certificate: `openssl s_client -connect <host>:443 -servername <host> </dev/null | openssl x509 > certs/sms.pem`
2. Confirm its SHA-256 fingerprint with the server's administrator: `openssl x509 -in certs/sms.pem -noout -fingerprint -sha256`
3. Set `MANAGEMENT_CA_CERT=/certs/sms.pem` in `.env` (for a gateway's Gaia API: `GAIA_CA_CERT=/certs/gateway.pem`)
4. If you connect by IP and the certificate names a host, set `MANAGEMENT_TLS_SERVERNAME` (or `GAIA_TLS_SERVERNAME`) to that host name
5. Recreate the servers: `docker compose up -d`

`./certs` is mounted read-only at `/certs`. `*.pem` files are git-ignored. The error `TLS certificate verification failed` means the file or the name does not match yet. Policy Insights against a self-signed server also needs `MANAGEMENT_CA_CERT`.

### Gaia

The Gaia server talks to one Security Gateway's Gaia REST API, not to the Management Server.

- Set `GAIA_GATEWAY_IP`, `GAIA_USERNAME` and `GAIA_PASSWORD`. `GAIA_GATEWAY_PORT` defaults to 443. While they are blank, the tools answer at once that Gaia is not configured
- The Gaia credentials go only to `GAIA_GATEWAY_IP` and the gateways in `GAIA_ALLOWED_GATEWAYS` (comma-separated). A tool call for any other gateway is refused, and nothing is sent to it. This stops a prompt injection from sending the lab's gateway password to an attacker's host
- `gateway_ip` must be an IP address or a DNS name, and the port must be 1 to 65535

### File folders: CPInfo Analysis and Threat Emulation

Put files for these servers in `./n8n/shared` on the host.

| Server | Path in the server | Allowed files |
|---|---|---|
| CPInfo Analysis | `/data/cpinfo` | Regular files under `CPINFO_ALLOWED_DIRS` (default `/data/cpinfo`) |
| Threat Emulation | `/data/shared` (read-only) | Regular files under `TE_ALLOWED_DIRS` (`/data/shared`) |

- A plain file name works: a relative path resolves against the first allowed folder
- Symbolic links and `..` are resolved before the check. Directories, FIFOs, sockets and devices are refused
- A path outside gets `Access denied`, whether or not it exists. `File not found` appears only for a missing file inside an allowed folder
- Threat Emulation uploads the file to the Threat Emulation cloud service. Use test files only

### Spark Management and SASE

Both stay stopped while their settings are blank. They print one line, for example `Spark Management MCP server is not configured. Set SPARK_MGMT_CLIENT_ID and SPARK_MGMT_SECRET_KEY in .env, then run: docker compose up -d spark-management-mcp`, and exit 0. `doctor.sh` reports them as `info ... stopped because it is not configured`, never as a failure. After you set the keys, run the `docker compose up -d <service>` command from that line.

### Server behavior

These come from the lab patches in [`PATCHES.md`](../docker/n8n/mcp-src/PATCHES.md).

- **One server instance per session**, so the gateway can hold concurrent sessions
- **Sessions close when idle.** `MCP_SESSION_IDLE_TIMEOUT_SECONDS` (default 1800) and `MCP_MAX_SESSIONS` (default 32) are server defaults; compose does not set them. An expired session gets HTTP 404, which tells MCP clients to start a new one. `/health` reports counts only
- **Credentials from one source.** A session uses either request headers or the server's environment, chosen at `initialize`. A client that sends a partial set of credential headers gets HTTP 400 `Invalid session configuration: ...`. The lab's agents send none
- **No secrets in logs.** Errors are sanitized and secrets redacted

---

## MCP Gateway

The [Docker MCP Gateway](guides/MCP_Gateway_Explained.md) puts 11 of the servers behind one endpoint.

| Setting | Value |
|---|---|
| Image | `docker/mcp-gateway:v0.44.1` (pinned by digest) |
| Endpoint | `http://mcp-gateway:8080/mcp`, on the `lab` network only |
| Transport | Streamable HTTP (`--transport=streaming`). Replies arrive as Server-Sent Events |
| Authentication | `Authorization: Bearer <MCP_GATEWAY_TOKEN>`. Compose passes the token as `MCP_GATEWAY_AUTH_TOKEN`. A missing or wrong token gets HTTP 401 |
| Catalog | [`mcp-gateway/catalog.yaml`](../mcp-gateway/catalog.yaml), mounted as `checkpoint-mcp.yaml` |
| Servers | `--servers=documentation,quantum-management,policy-insights,cpinfo-analysis,https-inspection,management-logs,gaia,gw-cli,reputation-service,threat-emulation,threat-prevention` |
| Tool schemas | `--preserve-tool-schema-dialect` passes each server's schemas through unchanged |
| Tools | 190 |
| Docker access | `DOCKER_HOST=tcp://docker-socket-proxy:2375` (no socket mount) |
| Start | Waits for `docker-socket-proxy` and all 11 servers to be healthy |
| Health check | `nc -z 127.0.0.1 8080`: the port only. `doctor.sh --post-start` counts the tools |
| Hardening | `cap_drop: ALL`, `no-new-privileges` |

### Token

- Compose refuses to start without `MCP_GATEWAY_TOKEN` (`MCP_GATEWAY_TOKEN is not set. Run ./setup.sh`). Without a fixed token the gateway would mint a new one on every restart and break every stored credential
- The token reaches the agents as the n8n credential "MCP Gateway Bearer", the Flowise variable `MCP_GATEWAY_TOKEN`, and the MCP header of each seeded Langflow gateway flow
- A client sends `initialize` (and keeps the `Mcp-Session-Id` it returns), then `notifications/initialized`, then `tools/list`. A `tools/list` without `initialize` gets no tools (see [MCP Gateway, explained](guides/MCP_Gateway_Explained.md))

### Docker access

The gateway only lists containers and networks. It reads them through `docker-socket-proxy` (`tecnativa/docker-socket-proxy:v0.5.0`):

- `CONTAINERS=1`, `NETWORKS=1`, `POST=0`: the gateway can read containers and networks, and every write is refused. Nothing can be created, started, stopped or executed
- The proxy mounts `/var/run/docker.sock` read-only. It is the only service that mounts the socket, and the CI policy check enforces that
- If the proxy is down, the gateway fails. Start both: `docker compose up -d docker-socket-proxy mcp-gateway`

At start the gateway prints about 90 `audit event dropped due to backpressure` lines. They are harmless: outside Docker Desktop its policy audit does nothing, and its 100-event queue is smaller than the 190 tools it lists. No gateway option turns them off.

### Tool scoping (128 tools at most)

Model providers accept at most 128 tools per request, and every tool costs input tokens. Each gateway agent therefore binds only the tools it needs:

| Agent | Tools bound |
|---|---|
| Each "<Product> Agent (MCP Gateway)" | Exactly its own server's tools (the same set as its "(Direct)" twin) |
| Check Point MCP Gateway Agent | A read-first core of 48 tools across all 11 servers |
| Fleet Commander Estate Agent, Guarded Agent (Lakera Guard) | A curated core of 117 tools |
| SOC Response Chain Agent | 62 tools |

Where the scope is set:

- **n8n.** The MCP Client Tool node, Tools to Include: Selected (`include: selected`). In the per-server gateway agents, Check Point MCP Gateway Agent and Guarded Agent (Lakera Guard), the node is named "MCP Gateway", so tool names carry the prefix `MCP_Gateway_`. Fleet Commander Estate Agent names it "MCP Gateway (core toolset)". SOC Response Chain Agent has three: "Gateway · logs", "Gateway · reputation" and "Gateway · management"
- **Flowise.** The Custom MCP node's Actions
- **Langflow.** The MCP Tools component's tool list (`tools_metadata`)

When the gateway's tool list changes (for example after you add a server), refresh the tool list in the builder and switch on only the new tools you need. In Langflow, a refresh can switch every tool on: check that no more than 128 stay on. [`scripts/flows/langflow_fix.py`](../scripts/flows/langflow_fix.py) holds the scopes (`SERVER_TOOLS`, `UMBRELLA_CORE`, `FLEET_CORE`, `SOC_CORE`) that all three builders use.

### Adding a server to the gateway

1. Add a `registry` entry to `mcp-gateway/catalog.yaml` with `type: "remote"`, the service URL and `transport_type: "streamable"`
2. Add its name to the `--servers` list of the `mcp-gateway` command
3. Give the server a health check and add it to the gateway's `depends_on` with `condition: service_healthy`, so the gateway lists its tools at start
4. Recreate the gateway: `docker compose up -d mcp-gateway`
5. Add the new tools to the agents that should use them, within 128 tools

The [Build Your Own MCP Server](guides/Build_Your_Own_MCP_Exercise.md) guide walks through these steps. The PolicyPilot sidecar is not registered with the gateway, and registering it is not part of the lab ([PolicyPilot guide](guides/PolicyPilot_Gateway_Sidecar_Guide.md)). Never add `vuln-mcp` to the gateway.

---

## Seeding and upgrades

The lab's agents live in the repository. Two one-shot importers seed them into the builders on every `docker compose up -d`. Committed files hold placeholders only, never secrets.

| Builder | Seeded by | What it gets |
|---|---|---|
| n8n | `n8n-import` | 35 workflows (33 agents, the "Documentation RAG Retriever" sub-workflow and the "Nightly Agent Self-Check") and 8 credentials |
| Flowise | `builders-import` | 33 agents, the credentials "Lab Model (LiteLLM)", "Lab Tracing (Langfuse)" (when the Langfuse keys are set) and "Lab Qdrant" (when `QDRANT_API_KEY` is set), the variables `MCP_GATEWAY_TOKEN`, `LAKERA_API_KEY`, `IDP_SCIM_TOKEN`, `DEVHUB_MCP_TOKEN`, `PILOT_MCP_TOKEN`, and the API key "Lab Agents API" |
| Langflow | `builders-import` | 33 agents and the Credential variables `LITELLM_MASTER_KEY`, `IDP_SCIM_TOKEN`, `LAKERA_API_KEY`, `QDRANT_API_KEY` |
| Open WebUI | `openwebui-provision` | The admin account only |

### The agents

[`integrations/builders_agents.json`](../integrations/builders_agents.json) is the source of truth for agent names, in all three builders and the evals.

| Kind | Count | Agents | Endpoint |
|---|---|---|---|
| Gateway | 15 | 11 "<Product> Agent (MCP Gateway)", Check Point MCP Gateway Agent, Fleet Commander Estate Agent, Guarded Agent (Lakera Guard), SOC Response Chain Agent | `http://mcp-gateway:8080/mcp` |
| Direct | 12 | 11 "<Product> Agent (Direct)", MCP Security Lab Agent (Intentionally Vulnerable) | A server URL from [MCP servers](#mcp-servers), or `http://vuln-mcp:3099` |
| Native | 2 | Lakera Guard Screening Agent, Documentation RAG Agent | Lakera Guard, or the Qdrant collection `cp_docs` |
| External | 4 | DevHub Operations Agent, PolicyPilot Access Automation Agent (Pro), PolicyPilot Dynamic Layers Agent, Identity Provisioning Agent (SCIM) | `https://hub.<DOMAIN>/api/mcp`, `https://policypilot.<DOMAIN>/mcp/`, `https://idp.<DOMAIN>/scim/v2/Users` |

The products of the per-server agents are Management, Management Logs, Policy Insights, Threat Prevention, HTTPS Inspection, CPInfo Analysis, Gaia, Gateway CLI, Documentation, Threat Emulation and Reputation Service. For example: "Management Agent (MCP Gateway)" and "Management Agent (Direct)".

Agents with prerequisites:

| Agent | Needs | Without it |
|---|---|---|
| Guarded Agent (Lakera Guard), Lakera Guard Screening Agent | `LAKERA_API_KEY` | Published, but block every message and explain how to set the key. Nothing is sent to Lakera |
| DevHub Operations Agent | `DOMAIN`, `DEVHUB_MCP_TOKEN` | n8n: imported, not published |
| PolicyPilot Access Automation Agent (Pro), PolicyPilot Dynamic Layers Agent | `DOMAIN`, `PILOT_MCP_TOKEN` | n8n: imported, not published |
| Identity Provisioning Agent (SCIM) | `DOMAIN`, `IDP_SCIM_TOKEN` | n8n: imported, not published |
| MCP Security Lab Agent (Intentionally Vulnerable) | Profile `security-lab` (`vuln-mcp` running) | n8n: imported, not published |
| Nightly Agent Self-Check | `NIGHTLY_SELF_QA=1` | Imported, not published |

Flowise and Langflow import every agent and name the ones that cannot work yet, with what they need, in the `builders-import` log.

### n8n-import

`n8n-import` runs [`scripts/n8n-provision.sh`](../scripts/n8n-provision.sh) in mode `import` on the n8n image. It needs `LITELLM_MASTER_KEY`, `MCP_GATEWAY_TOKEN`, `N8N_ADMIN_EMAIL` and `N8N_ADMIN_PASSWORD`.

1. Waits until n8n answers `/healthz/readiness`
2. **Credentials.** Copies [`n8n/backup/credentials_public/`](../n8n/backup/credentials_public/) to a private memory-backed folder and fills the placeholders from `.env`. Every run re-syncs them: edits made in the n8n UI are replaced
3. **Workflows.** Fills `{{DOMAIN}}` and the Lakera Guard settings, then imports the workflows by id. A seeded workflow changed in n8n since the last import is kept (see [Edited agents](#edited-agents))
4. **Publishing.** Publishes the workflows through the n8n REST API, so their chat URLs work at once, with no n8n restart. A workflow whose prerequisites are missing is imported but not published, and the log says what it needs

| n8n credential | File | Filled from |
|---|---|---|
| Lab Model (LiteLLM) | `openai.json` | `LITELLM_MASTER_KEY` (URL `http://litellm:4000/v1`) |
| MCP Gateway Bearer | `gateway-bearer.json` | `MCP_GATEWAY_TOKEN` |
| Lab Agents Chat | `lab-agents-chat.json` | `N8N_ADMIN_EMAIL`, `N8N_ADMIN_PASSWORD` (HTTP Basic for the chat pages) |
| Lab Qdrant | `quadrant.json` | `QDRANT_API_KEY` (URL `http://qdrant:6333`; blank = no key) |
| Lakera Guard | `Lakera.json` | `LAKERA_API_KEY` |
| SCIM IdP Token | `scim-idp-token.json` | `IDP_SCIM_TOKEN` |
| DevHub Bearer Auth | `devhub-bearer.json` | `DEVHUB_MCP_TOKEN` |
| PolicyPilot Bearer Auth | `policypilot-bearer.json` | `PILOT_MCP_TOKEN` |

A typical log on a lab with no optional keys and no `DOMAIN`:

```text
Credentials: 8 prepared from .env (they replace any edits made in the n8n UI).
  LAKERA_API_KEY is not set: the Lakera Guard agents block every prompt with setup steps.
  ...
Workflows: 35 to import, 0 kept.
...
Published 29 workflows; 6 not published (prerequisites); 0 kept as changed in n8n; 0 failed.
n8n-import completed.
```

Every n8n agent has a chat trigger with HTTP Basic auth, an OpenAI Chat Model node on `lab-chat`, Simple Memory ("Conversation Memory"), and one MCP Client Tool node (or its own tools). SOC Response Chain Agent is the exception: it chains three agents, each with its own model node and MCP Client Tool node, and has no memory. The chat pages ask for the lab admin sign-in. "Open chat" in the n8n editor needs no extra sign-in. When a tool server or the model fails, the agent's error branch answers with a plain explanation ("Friendly error") instead of HTTP 500.

Re-run after a change in `.env`: `docker compose run --rm n8n-import`.

### builders-import

`builders-import` runs [`integrations/seed_builders.py`](../integrations/seed_builders.py), a standard-library script, on `python:3.12.14-alpine`.

1. Signs in to Flowise (`http://flowise:3020`) and Langflow (`http://langflow:7860`) with the lab admin. On a fresh Flowise database it registers the admin first. `FLOWISE_API_KEY` and `LANGFLOW_API_KEY` replace the sign-in when set
2. Creates or updates the credential "Lab Model (LiteLLM)" in Flowise and the variable `LITELLM_MASTER_KEY` in Langflow. `LITELLM_MASTER_KEY` is required: without it the job exits 1
3. Creates or updates the integration variables listed above, whenever the value is set in `.env`
4. Creates every agent in both builders, or updates it in place when the repository version or a value from `.env` changed. Each seeded flow carries a marker (`labSeed`: the agent slug and a checksum). A seeded flow is never duplicated
5. Flowise only: turns on Langfuse analytics (credential "Lab Tracing (Langfuse)") for seeded flows that have no analytics setting of their own, and creates the API key "Lab Agents API", which `/api/v1/prediction` requires. The canvas chat does not need it
6. Names the agents whose prerequisites are missing

When Langflow is not running, the job logs `Langflow is not running (add langflow to COMPOSE_PROFILES); skipped.` and succeeds.

Placeholders filled in memory (never written back, never printed): `__MCP_GATEWAY_TOKEN__`, `__DEVHUB_MCP_TOKEN__`, `__PILOT_MCP_TOKEN__` (Langflow MCP headers) and `{{DOMAIN}}` (both builders). Flowise flows reference tokens as `{{$vars.NAME}}`.

The Guarded agent and the Lakera Guard Screening Agent are Flowise Agentflow V2 flows: Start, Lakera Guard (input), Allowed?, Agent, Lakera Guard (output), with a direct reply for blocked prompts.

Re-run after a change in `.env`: `docker compose run --rm builders-import`.

### The DOMAIN rule

Both importers use one rule. `DOMAIN` when it is set. Otherwise `N8N_HOST` without its `n8n.` prefix, when `N8N_HOST` starts with `n8n.`. Otherwise no domain: the `{{DOMAIN}}` placeholder stays, and the agents that need it are flagged (n8n: not published). `N8N_HOST=localhost` never becomes a domain.

### Edited agents

`.env` is the source of truth for credentials and variables. Agents you edit are kept.

| Builder | What counts as edited | Kept agent | Replace it once with the repository version |
|---|---|---|---|
| n8n | The workflow changed in n8n since the last import | Log: `kept (changed in n8n since the last import): <name>` | `docker compose run --rm -e N8N_SEED_OVERWRITE=1 n8n-import` |
| Flowise, Langflow | The stored flow differs from the checksum in its marker, or it has no marker | Log: `- <name>: kept, changed in the builder after it was seeded.` or `... no seed marker (seeded by an older version, or created by hand).` | `docker compose run --rm -e SEED_OVERWRITE=1 builders-import` |

- To keep your variant and get the new version too, duplicate the agent under a new name first
- Instead of the one-off command, you can set `N8N_SEED_OVERWRITE=1` or `SEED_OVERWRITE=1` in `.env` for one run, then set it back to `0`
- An edited gateway agent keeps the gateway token it had. After you change `MCP_GATEWAY_TOKEN`, re-seed it, or update its token by hand
- n8n tracks edits from the first import with this check on. The first import after an upgrade from an older lab replaces seeded workflows once. Duplicate the workflows you changed before you upgrade

### Former names

The agents were renamed to the scheme "<Product> Agent (MCP Gateway)" and "<Product> Agent (Direct)", with current product names. `builders_agents.json` lists every former name. The migration needs no manual step:

- **n8n** updates workflows by id, so a renamed workflow keeps its id and history
- **Flowise and Langflow.** `builders_agents.json` lists each agent's `former_names` (and `former_slugs`). A flow found under a former name is the same agent: it is renamed and updated in place. An edited flow keeps its former name until you run once with `SEED_OVERWRITE=1`
- A flow that carries a former name but is not the seeded copy (for example a duplicate you made) is left alone, and the log names it. Delete it in the builder if you no longer need it
- Copies of agents that refer to old node names (for example the former gateway node name) need those references updated

### Upgrades

`./update.sh` (Windows: `update.ps1`) updates a lab to the latest repository version:

1. `git pull --ff-only`: stops and changes nothing when local edits would conflict
2. `./setup.sh --non-interactive`: adds new settings to `.env` and fills new blank secrets. Your values are kept, and nothing the running lab uses is regenerated
3. `docker compose pull --ignore-buildable`: newer images
4. `docker compose up -d`, through `op run` when `.env` holds `op://` references

Edited agents are kept (see [Edited agents](#edited-agents)). Check the lab 5 to 10 minutes later with `./scripts/doctor.sh --post-start`.

What changes when an older lab upgrades:

| Change | What to know |
|---|---|
| n8n 2.41.5 | The first start runs database migrations. The `n8n` health check and `n8n-provision` wait for `/healthz/readiness`, up to about 5 minutes |
| Network `demo` became `lab` | Containers move to `<project>_lab`. The old `<project>_demo` network stays behind. Remove it after the upgrade with `docker network rm <project>_demo`. Change `demo` to `lab` in any local override file |
| Langfuse organization | Langfuse creates its organization only on the first start and never renames it. Setup writes `LANGFUSE_ORG_ID=cp-playground` (the former id) for a lab that started before. A lab that skips setup gets a second, empty organization; the project and its keys stay where they are, so tracing still works |
| Agent names | Migrated in place (see [Former names](#former-names)) |
| Flowise on Postgres | Flowise always uses the lab Postgres now. Flows saved in the old SQLite database (`flowise_data/database.sqlite`) are not moved: the lab agents are seeded again, and your own flows need an export from the old Flowise and an import into the new one. Setup prints a note when this applies |
| Langflow data in a volume | Langflow now keeps its database and flows in the `langflow` volume. Flows saved inside an older Langflow container are lost when it is recreated: export them first. `builders-import` seeds the lab agents again |
| Qdrant 1.19 | Starts on the new `qdrant_data` volume; `rag-ingest` fills it. The old `<project>_qdrant_storage` volume is no longer used |
| Retired n8n credentials | Credentials from older versions (for example the former Azure OpenAI, Anthropic, Gemini, Ollama and Postgres credentials) stay in n8n until you delete them. No seeded workflow uses them |
| Profile `full` | Renamed to `complete` |

#### Retired settings

Setup removes these settings from `.env` because nothing reads them any more: `N8N_BASIC_AUTH_ACTIVE`, `N8N_BASIC_AUTH_USER`, `N8N_BASIC_AUTH_PASSWORD`, `N8N_USER_MANAGEMENT_EMAIL`, `N8N_USER_MANAGEMENT_FIRST_NAME`, `N8N_USER_MANAGEMENT_LAST_NAME`, `N8N_USER_MANAGEMENT_PASSWORD`, `N8N_USER_MANAGEMENT_DISABLED`, `N8N_PORT`, `POSTGRES_PORT`, `OLLAMA_PORT`, `OLLAMA_HOST`, `LANGFLOW_PORT`, `OPEN_WEBUI_PORT`, `AIG_PORT`, `DEPLOY_FLOWISE`, `DEPLOY_LANGFLOW`, `FLOWISE_DATABASE_TYPE` and `HARMONY_SASE_REGION` (SASE now takes `HARMONY_SASE_ORIGIN`). Settings that are not in `.env-example` and not retired are kept under `# ---- Kept from your previous .env (not in .env-example) ----`.

---

## Observability

Langfuse v2 (`2.95.11`) runs as one container on the lab Postgres, in the `langfuse` database that `langfuse-db-init` creates.

| Item | Value |
|---|---|
| Address | `https://trace.<DOMAIN>` on a lab host, or `LANGFUSE_URL` (`http://localhost:3100` with the local override) |
| Sign-in | The lab admin. Sign-up is off (`AUTH_DISABLE_SIGNUP=true`) |
| First start | Creates the organization `LANGFUSE_ORG_ID` ("Check Point Agentic Lab"), the project `agents` ("Agents") with the key pair from `.env`, and the lab admin user |
| Telemetry | Off (`LANGFUSE_TELEMETRY_ENABLED=false`) |

How each part is traced:

| Source | Trace path |
|---|---|
| n8n, Flowise and Langflow agents, the code-first agent, the evals, AI-Infra-Guard | LiteLLM sends every `lab-chat` call to Langfuse (success and failure callbacks) |
| Flowise seeded flows | Also Flowise's own analytics, with the credential "Lab Tracing (Langfuse)". A flow you create needs analytics turned on in its settings |
| Langflow | Through LiteLLM only. Langflow 1.10 bundles the Langfuse v3 SDK, which does not work with Langfuse v2, so compose passes Langflow no Langfuse settings |
| Open WebUI | Not traced: it calls Ollama directly |

- LiteLLM records each model call as its own trace (`litellm-acompletion`). One upstream failure shows one ERROR generation, because LiteLLM does not retry
- Requests refused for a wrong key are not traced
- Tracing turns on when `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` are set. The `[lab-litellm]` log line says `Langfuse tracing on` or `off`
- Prove that Langfuse accepts traces: `docker compose exec -T litellm python - < integrations/observability/langfuse_smoke_trace.py` (exit 0 = the trace landed)
- Test result: in the Complete lab with Azure OpenAI, Langfuse received 18 traces during one acceptance run, 8 of them from builder runs

Guide: [Observability with Langfuse](guides/Observability_Langfuse.md).

---

## RAG, evals, and exercises

### Visible RAG

- `rag-ingest` embeds the eight Markdown files in [`integrations/rag-cp-docs/corpus/`](../integrations/rag-cp-docs/corpus/) with `nomic-embed-text` (768 dimensions) into the Qdrant collection `cp_docs`. The corpus is training content, not official Check Point documentation
- The job is safe to re-run. Nothing changes until every embedding succeeded, an existing collection is updated in place, and it skips the work when nothing changed (`FORCE=1` re-embeds). Re-run it with `docker compose run --rm rag-ingest`
- The retrievers drop snippets that score below 0.5 (0 to 1): n8n (Qdrant search `score_threshold` 0.5 in "Documentation RAG Retriever"), Flowise (Similarity Score Threshold Retriever, 50 %, Max K 4) and Langflow (Check Point Docs Retriever, Minimum Score 0.5). In n8n, the retriever explains an empty result
- The threshold is not calibrated for every corpus. Check how a question scores:

```sh
docker compose run --rm rag-ingest python3 ingest.py --search "How do I enable Identity Awareness?"
```

`--search` lists the top hits with their scores and marks the ones that pass `RAG_MIN_SCORE`. To change the agents' threshold, edit `RAG_MIN_SCORE` in [`scripts/flows/langflow_fix.py`](../scripts/flows/langflow_fix.py), then regenerate the flows (`python3 scripts/flows/langflow_fix.py apply`, `python3 scripts/flows/flowise_fix.py apply`, `python3 scripts/flows/n8n_fix.py apply`) and re-seed.

Guide: [Visible RAG](guides/Visible_RAG.md).

### Evals

`evals-run` (profile `evals`) posts the 10 cases in [`integrations/evals/evals_cases.json`](../integrations/evals/evals_cases.json) to the n8n chat endpoints, scores each answer, and writes `evals_report.md` and `evals_report.json` to `./n8n/shared`.

```sh
docker compose --profile evals run --rm evals-run
```

- It signs in to the chat endpoints with the lab admin (HTTP Basic). Each case gets its own session id per run
- `FAIL` means the agent answered, but not as the case expects (model, prompt or tools). `ERROR` means the agent could not answer at all (sign-in, a workflow not published, a missing service or key). Fix the errors first
- Exit 0 only when every case passes. Most cases need Check Point keys, the DevHub and PolicyPilot cases need those services, and the Guarded agent cases need `LAKERA_API_KEY`
- `EVALS_BASE_URL` points it at another n8n. Plain `http://` is refused for anything but a lab service name or `localhost`

Guide: [Evals Harness](guides/Evals_Harness.md).

### Nightly Agent Self-Check

The n8n workflow "Nightly Agent Self-Check" probes every chat agent at 02:00 (`GENERIC_TIMEZONE`, default UTC) and fails the execution when an agent fails. It is opt-in: set `NIGHTLY_SELF_QA=1`, then run `docker compose run --rm n8n-import`.

### Code-first agent

[`integrations/code-agent/`](../integrations/code-agent/README.md) holds two standard-library scripts:

- `mcp_gateway_client.py` does the MCP handshake with the gateway and lists the tools
- `agent_loop.py` is a tool-use loop on `lab-chat` (LiteLLM) with gateway tools. `MCP_TOOLS` (default `reputation_*`) scopes the tools, at most 128

Run it on the lab network and pass only the two keys it needs:

```sh
cd integrations/code-agent
docker run --rm --network <project>_lab \
  --env-file <(grep -E '^(MCP_GATEWAY_TOKEN|LITELLM_MASTER_KEY)=' ../../.env) \
  -v "$PWD":/app:ro python:3.12-alpine python /app/agent_loop.py
```

The `<(...)` form needs bash or zsh. With 1Password references in `.env`:

```sh
op run --env-file=../../.env -- docker run --rm --network <project>_lab \
  -e MCP_GATEWAY_TOKEN -e LITELLM_MASTER_KEY -v "$PWD":/app:ro \
  python:3.12-alpine python /app/agent_loop.py
```

### Build Your Own MCP Server (profile exercises)

- `ips-cve-mcp` is a standard-library Streamable HTTP server with two tools, `ips_latest_protections` and `ips_protections_by_cve`. It listens at `http://ips-cve-mcp:3013/mcp`, read-only, as uid 65534, with no capabilities
- It needs `IPS_CLIENT_ID` and `IPS_ACCESS_KEY`. `IPS_REGION` is `EU` (default) or `US`. `IPS_MCP_BEARER_TOKEN` optionally requires a Bearer token
- The published image runs the solution. Build your own work with `IPS_CVE_VARIANT=scaffold` and `docker compose --profile exercises up -d --build ips-cve-mcp`
- Self-test without a key or network, from `exercises/build-your-own-mcp/solution` (or `scaffold`): `python3 ../test_byo_mcp.py ips_cve_mcp.py`. Against the running server, from `exercises/build-your-own-mcp`: `docker run --rm --network <project>_lab -v "$PWD":/x:ro python:3.12-alpine python3 /x/test_byo_mcp.py --url http://ips-cve-mcp:3013/mcp` (`--gateway URL` checks the gateway after you register it)
- `ips-cve-mcp` is not in the gateway by default. Register it as in [Adding a server to the gateway](#adding-a-server-to-the-gateway)

Guide: [Build Your Own MCP Server](guides/Build_Your_Own_MCP_Exercise.md).

### Lakera Guard agents

"Guarded Agent (Lakera Guard)" (gateway tools) and "Lakera Guard Screening Agent" screen each prompt before the model runs and each answer after.

- Input screening blocks a flagged prompt, and also blocks when screening cannot complete (no key, Lakera unreachable, key rejected)
- Output screening withholds a flagged answer. When output screening cannot complete, the answer is delivered with a note
- Without `LAKERA_API_KEY`, nothing is sent to Lakera and every message is blocked with setup steps. `LAKERA_PROJECT_ID` applies a project's policy in n8n
- Nothing screens tool output yet

Guide: [Lakera Guard Screening Agent](guides/Lakera_Guard_Screening_Agent_Guide.md).

### Identity provisioning, DevHub, and PolicyPilot

| Agent | Endpoint | Token | Notes |
|---|---|---|---|
| Identity Provisioning Agent (SCIM) | `https://idp.<DOMAIN>/scim/v2/Users` | `IDP_SCIM_TOKEN` | An HTTP tool, not MCP. Creates and lists users (no groups). Pairs with [SAML_IDP_Simulator](https://github.com/alshawwaf/SAML_IDP_Simulator) |
| DevHub Operations Agent | `https://hub.<DOMAIN>/api/mcp` | `DEVHUB_MCP_TOKEN` | The [Dev Hub](https://github.com/alshawwaf/dev-hub) portal's MCP endpoint |
| PolicyPilot Access Automation Agent (Pro), PolicyPilot Dynamic Layers Agent | `https://policypilot.<DOMAIN>/mcp/` | `PILOT_MCP_TOKEN` | The external PolicyPilot portal, not the sidecar |

**PolicyPilot sidecar (profile policypilot).** `policypilot-mcp` runs `${POLICYPILOT_IMAGE}` (default `policypilot:custom`), which is not published: build it from [alshawwaf/PolicyPilot](https://github.com/alshawwaf/PolicyPilot). It needs `PILOT_MCP_TOKEN` and the key the PolicyPilot portal used to encrypt its saved credentials (`PILOT_ENCRYPTION_KEY`, or `PILOT_SESSION_SECRET` when the portal has none). It listens on port 3020 and is not in the gateway by default. Guide: [PolicyPilot agents and MCP sidecar](guides/PolicyPilot_Gateway_Sidecar_Guide.md).

---

## Security lab and AI-Infra-Guard

Both are off by default. Turn them on only for these exercises.

### Containment

Nothing from the security lab or a scan may reach the Docker host or the internet.

| Service | Networks | Hardening |
|---|---|---|
| `vuln-mcp` | `security-lab` (internal) only | No ports, read-only file system, uid 65534, `cap_drop: ALL`, `no-new-privileges`, code mounted read-only, no host data mounts |
| `aig-webserver` | `ai-red-team` (internal) only | No ports, no route, `no-new-privileges` |
| `aig-agent` | `ai-red-team` and `security-lab` (both internal) | No ports, no `SYS_ADMIN`, no `seccomp=unconfined`, `no-new-privileges` |
| `aig-provision` | `ai-red-team` (internal) only | Read-only, `cap_drop: ALL`, `no-new-privileges` |
| `aig-ui` | `aig-ui-access` and `ai-red-team` | No port by default. Read-only, `cap_drop: ALL`, `no-new-privileges`, uid 101, IP forwarding off. Forwards only to `http://aig-webserver:8088` |

- A live test in October 2026, run from inside all three running containers, proved the containment: 1.1.1.1 on port 443, `host.docker.internal` (does not resolve), the Docker host gateway (192.168.65.254 on Docker Desktop) and github.com were all unreachable
- On `ai-red-team`, AI-Infra-Guard reaches LiteLLM, which joins that network only to serve `lab-chat`. Scan prompts therefore reach the model provider you configured. On `security-lab`, `aig-agent` shares the network with `vuln-mcp` and its clients (`n8n`, `flowise`, `langflow` and the two importers)
- `aig-ui` does not open a way out. It joins `aig-ui-access`, a network of its own, only because a port cannot be published from an internal network; no other lab container is on it, so the UI (which has no sign-in) is reachable only through the local port override. It accepts inbound requests and forwards them to one fixed upstream, `http://aig-webserver:8088` ([`aig/ui-nginx.conf`](../aig/ui-nginx.conf)). It is not a forward proxy: `CONNECT` is refused, and neither the `Host` header nor an absolute request URI can pick another target. IP forwarding is off in its network namespace (`net.ipv4.ip_forward=0`), so it does not route packets between its networks, and it holds no capabilities. `aig-webserver` and `aig-agent` still have no default route
- The Security Lab agents in n8n, Flowise and Langflow hold only the four `vuln-mcp` tools: no HTTP request, code or other tools that a poisoned description could misuse
- The CI policy check fails when `vuln-mcp`, `aig-webserver` or `aig-agent` lose their hardening, leave their internal networks, or join the default stack, and when `aig-ui` joins any network other than `lab` and `ai-red-team`, loses its hardening, or turns IP forwarding back on. The nightly live-stack run never starts these profiles

### MCP Security Lab (profile security-lab)

`vuln-mcp` runs [`integrations/mcp-security-lab/vuln_mcp_server.py`](../integrations/mcp-security-lab/vuln_mcp_server.py) on `python:3.12.14-alpine`, at `http://vuln-mcp:3099` with no authentication (on purpose: compare the gateway's token).

| Tool | Attack class |
|---|---|
| `weather_lookup` | Tool poisoning: the description hides an instruction |
| `fetch_ticket` | Indirect prompt injection: the result carries instructions |
| `read_local_file` | Over-permissioned tool: claims to read any file, returns simulated fake secrets |
| `currency_convert` | Rug pull: the description turns malicious after the first call |

- Everything is simulated and labelled `[SIMULATED — MCP SECURITY LAB / training only]`. The server reads no files, starts no processes and makes no outbound connections; its unit tests enforce this
- The rug pull applies server-wide. It turns clean again `RUG_PULL_RESET_SECONDS` (default 600) after the last `currency_convert` call, on `POST /reset`, or on restart. `GET /health` shows `"rug_pull": "clean"` or `"armed"`
- The n8n agent "MCP Security Lab Agent (Intentionally Vulnerable)" is published only when `vuln-mcp` answers at import time. After you turn the profile on, run `docker compose run --rm n8n-import`

Guide: [MCP Security Lab](guides/MCP_Security_Lab.md).

### AI-Infra-Guard (profile ai-red-team)

[AI-Infra-Guard](https://github.com/Tencent/AI-Infra-Guard) is an AI red-teaming platform: AI infrastructure scans, MCP server scans and jailbreak evaluation. The lab runs the upstream multi-arch images `zhuquelab/aig-server:v4.6.3` and `zhuquelab/aig-agent:v4.6.3`.

- `aig-webserver` serves the web UI and task API on port 8088 inside `ai-red-team`. The UI has no sign-in, so it has no route and no published port. A port published on `aig-webserver` would not work anyway: it is only on an internal network
- `aig-ui` is the way into the web UI: an unprivileged nginx reverse proxy to `http://aig-webserver:8088` only. It publishes no port by default. To open the UI, publish one on `127.0.0.1` in your local `docker-compose.override.yml`, run `docker compose up -d aig-ui`, then browse to `http://localhost:8088` (on a remote lab host, through an SSH tunnel to that port). Never bind it to all interfaces or give it a public route: anyone who reaches it can start scans

  ```yaml
  services:
    aig-ui: { ports: ["127.0.0.1:8088:8088"] }   # profile ai-red-team: http://localhost:8088
  ```

- `aig-agent` runs the scans. It reaches `vuln-mcp` through `security-lab` and the model through LiteLLM. Its model settings default to `lab-chat` at `http://litellm:4000/v1` with `LITELLM_MASTER_KEY`; never point it at an abliterated model
- `aig-provision` registers `lab-chat` as a model in AI-Infra-Guard (`aig-provision: registered model lab-chat (lab-chat through LiteLLM). Select it when you start a scan.`). It leaves an existing `lab-chat` model as it is. Select `lab-chat` when you start a scan
- Scan target for the lab: `http://vuln-mcp:3099`
- Tested scan: an MCP scan of `vuln-mcp` through the AI-Infra-Guard task API, from inside `aig-agent`, with `lab-chat` on Azure OpenAI. It found `read_local_file` (high risk, file read) and `weather_lookup` (prompt injection in the description)
- **Report language.** The scan report came back in Chinese although the task asked for English (language `en`). Translate the findings if needed
- `SYS_ADMIN` and `seccomp=unconfined` are upstream settings for Chromium screenshots of scanned web services. The MCP scan does not need them, so the lab leaves them out

### Turning them on and off

```sh
# on: add the profiles to COMPOSE_PROFILES in .env, for example
#   COMPOSE_PROFILES=complete,security-lab,ai-red-team
docker compose up -d
docker compose run --rm n8n-import            # publishes the n8n Security Lab agent
tests/acceptance/run.sh --only SECLAB,AIG     # vuln-mcp on security-lab only; AI-Infra-Guard UI and agent up

# off: remove the profiles from COMPOSE_PROFILES, then stop the containers
docker compose --profile security-lab --profile ai-red-team stop vuln-mcp aig-webserver aig-agent aig-ui
```

---

## Doctor and acceptance tests

| Tool | Use it for |
|---|---|
| `./scripts/doctor.sh --preflight` | Before the first start: settings and Docker |
| `./scripts/doctor.sh --post-start` | 5 to 10 minutes after the start: services, gateway, agents, `lab-chat`, Langfuse |
| `tests/acceptance/run.sh` | Every promise of the lab, end to end |
| `tests/integration-test.sh` | CI: validate, optionally start, wait, then run the acceptance tests |

All of them need only `sh` and `docker` on the host. Network checks run in one throwaway, capped `python:3.12-alpine` container on the lab network. None of them prints a secret, and none stops the lab or removes anything. Run them the way you start the lab: with `op://` references in `.env`, prefix them with `op run --env-file=.env --`.

### doctor.sh

| Option | Meaning |
|---|---|
| `--preflight` (default) | `.env` present, private (mode 600) and complete; 1Password references; the admin password rule; key formats; the model provider settings and `docker compose run --rm --no-deps litellm --check`; Check Point settings that are only partly set; Docker running, Compose v2, external networks, and the CPUs and memory Docker can use against the component set |
| `--preflight --online` | Also calls each supplied model provider (a harmless model list) and tests TCP reachability of the Check Point hosts |
| `--post-start` | Every service of the active profiles healthy and every one-shot exited 0; the gateway answers 401 without and with a wrong token and lists its tools with `MCP_GATEWAY_TOKEN`; each server's tools directly; the agents seeded in n8n, Flowise and Langflow; LiteLLM refuses a wrong key and serves `lab-chat`; one short `lab-chat` prompt; Langfuse received its trace; the RAG collection; `vuln-mcp` on its own network (profile `security-lab`) |
| `--skip-chat` | With `--post-start`: no test prompt |
| `--env-file FILE`, `--project-dir DIR` | Another settings file or lab directory |
| `--verbose`, `-v` | Also list every healthy service |

Output: one line per check (`ok`, `warn`, `FAIL`, `info`, `skip`), then the capability matrix "What works with these settings", then `Result: N blocker(s), M warning(s)` or `Result: no blockers, M warning(s).` After `--post-start` it points to `tests/acceptance/run.sh`. Exit status: 0 = no blockers, 1 = blockers, 2 = the check could not run.

The capability matrix has one row per path. `ready` works with these settings. `needs` names what is missing. `off` means the component set or profile leaves it out, never a failure. `error` means a post-start check failed. `check` means a reachability test needs a look.

```text
What works with these settings
  ready   lab-chat model                       local Ollama qwen3.5:4b (no cloud key set; slow on a CPU)
  ready   MCP gateway path                     190 tools with MCP_GATEWAY_TOKEN
  ready   Direct MCP path (no gateway)         190 tools from the running servers
  ready   Agents in n8n                        35 of 35 lab workflows in n8n, 29 published
  ready   Agents in Flowise                    33 of 33 lab agents in Flowise (33 flows in total)
  off     Agents in Langflow                   not in this setup: add complete (or langflow) to COMPOSE_PROFILES in .env, then docker compose up -d
  needs   Check Point: Management (7 servers)  MANAGEMENT_HOST (or S1C_URL) and MANAGEMENT_API_KEY
  ...
```

Wrappers: `./scripts/health-check.sh` runs `doctor.sh --health` (services, gateway auth and tools, and each of the 11 gateway servers directly; no matrix). `./scripts/validate-env.sh` runs `doctor.sh --preflight` (`ENV_FILE=FILE` also works).

### Acceptance tests

[`tests/acceptance/run.sh`](../tests/acceptance/run.sh) runs 20 checks against a running lab. [`tests/README.md`](../tests/README.md) describes each one.

| ID | Promise | Runs when |
|---|---|---|
| `STACK` | Every service of the active profiles healthy, one-shots exited 0, no crash loops | Always |
| `GW-AUTH` | Gateway: 401 without a token and with a wrong token, 200 with `MCP_GATEWAY_TOKEN` | Always |
| `GW-TOOLS` | The gateway lists every server's tools (190) | Always |
| `DIRECT` | The 11 fronted servers answer `tools/list` directly; Spark Management and SASE pass as not configured | Always |
| `LITELLM` | `lab-chat` listed, 401 without and with a wrong key, a completion and a tool-call round trip | Always (completion with model calls) |
| `KEYS` | No builder container holds a provider key; with `--mock-provider`, the key from `.env` reaches the provider and the master key does not | Always |
| `N8N-SEED` | 35 workflows, 8 credentials, the published set matches `.env`, chat triggers 401 without sign-in and 200 with the lab admin | Always |
| `N8N-RUN` | The Documentation agents (MCP Gateway and Direct) answer with an MCP tool call | Model calls |
| `FLOWISE-SEED` | 33 agents, credential "Lab Model (LiteLLM)", tool counts match the catalog | Always |
| `FLOWISE-RUN` | The prediction API refuses calls without "Lab Agents API"; the Documentation agents use a tool | Model calls |
| `LANGFLOW-SEED` | 33 flows, variable `LITELLM_MASTER_KEY`, tool counts match the catalog | Profile `langflow` or `complete` |
| `LANGFLOW-RUN` | The Documentation flows answer with a tool call | Profile `langflow` or `complete`, model calls |
| `RAG` | `cp_docs` holds 768-dimension points, retrieval returns hits with sources, the RAG agent cites one | Always (agent with model calls) |
| `CODE-AGENT` | `mcp_gateway_client.py` handshake and tools; `agent_loop.py` makes a tool call | Always (loop with model calls) |
| `LANGFUSE` | Langfuse accepts the keys and received this run's traces | Model calls |
| `OPENWEBUI` | Admin sign-in, sign-up closed, the default model listed | Profile `local-chat` or `complete` |
| `SECLAB` | `vuln-mcp` healthy on `security-lab` only, 4 tools, the simulated payload, the n8n Security Lab agent published. No tool is called | Profile `security-lab` |
| `AIG` | The AI-Infra-Guard web UI answers and its agent runs | Profile `ai-red-team` |
| `EXERCISES` | `ips-cve-mcp` answers `tools/list` | Profile `exercises` |
| `EVALS` | The evals scorecard exists and has no wiring errors | Profile `evals` |

| Option | Meaning |
|---|---|
| `--only IDS`, `--skip IDS` | Comma-separated check IDs |
| `--profile-aware` (default), `--no-profile-aware` | Report checks of profiles that are off as `SKIP`, or run them anyway |
| `--with-model`, `--no-model` | Force model calls on or off. Default: on when `.env` has a cloud key or a local chat model |
| `--mock-provider` | `KEYS`: starts a mock provider and a throwaway LiteLLM with no network, and proves the key plumbing (two extra containers, removed afterwards) |
| `--with-scan` | Accepted for `AIG`; the scan test is supervised and not automated |
| `--json FILE` | Also write the results as JSON |
| `--env-file FILE`, `--project-dir DIR`, `--project-name NAME` | Another settings file, lab directory or Compose project |
| `--list` | List the checks |

`LAB_MODEL_TIMEOUT` (seconds per model call, default 180), `LAB_TRACE_WAIT` (default 90) and `LAB_DETAIL_WIDTH` (default 200) tune the run. `DOCKER_COMPOSE=/path/to/wrapper` runs every Compose command through a wrapper.

**Reading the result.** One line per check: `PASS`, `FAIL` or `SKIP` with a one-line reason. Each `FAIL` has a `fix:` line under it. The last lines repeat the failed IDs, for example `Fix the FAIL lines above, then re-run: tests/acceptance/run.sh --only STACK`. Exit status: 0 = no check failed, 1 = a check failed, 2 = the suite could not run.

Side effects are what a trainee does: chat sessions and executions in the builders, and traces in Langfuse. `LANGFLOW-RUN` creates a temporary Langflow API key and deletes it.

### Test results

The code was tested end to end on a fresh install in October 2026: macOS on Apple Silicon, Docker with 6 CPUs and 15.1 GB, and the lab image built from the tested commit.

| Run | Result |
|---|---|
| Standard lab, fresh volumes, no cloud key (`lab-chat` on local `qwen3.5:4b`) | Setup: no blockers. All services healthy, one-shots exited 0, Spark Management and SASE stopped as not configured. `n8n-import`: 35 workflows and 8 credentials, 29 published, 6 held for prerequisites. `doctor.sh --post-start`: no blockers (190 gateway tools, 190 direct, Flowise 33 of 33, RAG 8 points). Acceptance `--no-model --mock-provider`: 10 passed, 0 failed, 10 skipped. With the local model: `LITELLM`, `N8N-RUN`, `FLOWISE-RUN`, `RAG`, `CODE-AGENT` and `LANGFUSE` passed |
| Switch to the Complete lab (`COMPOSE_PROFILES=complete ./setup.sh --non-interactive`) | Langflow and Open WebUI healthy, `builders-import` re-ran by itself and seeded Langflow 33 of 33. `LANGFLOW-RUN` and `LANGFUSE` passed on the local model |
| Complete lab, Azure OpenAI behind an API Management gateway, keys as `op://` references | Acceptance `--with-model --mock-provider`: 16 passed, 0 failed, 4 skipped (the opt-in profiles). `STACK` 34 of 34, `GW-TOOLS` 190, `LITELLM` 1 to 3 seconds with a tool round trip, `LANGFUSE` 18 traces |
| Security lab and AI-Infra-Guard | `SECLAB` and `AIG` passed. Containment proven from inside the containers. MCP scan of `vuln-mcp` found the poisoned and over-permissioned tools |

### integration-test.sh

```sh
tests/integration-test.sh                          # check the running lab
tests/integration-test.sh --up --no-model          # start or update the lab (CI), then check it
tests/integration-test.sh --up -- --json result.json --mock-provider
```

It runs `docker compose config --quiet`, with `--up` also `docker compose up -d`, waits until no service is still starting (`--wait SECONDS`, default 900), then runs the acceptance tests with the other options. It never stops the lab.

---

## Troubleshooting

Start with `./scripts/doctor.sh --preflight` (settings) or `./scripts/doctor.sh --post-start` (running lab). Every `FAIL` line names the fix. Never remove volumes (`docker compose down -v`) to make a check pass: that deletes the lab's workflows, credentials, traces and models.

### Setup and start

| Symptom | Cause | Fix |
|---|---|---|
| `docker compose up -d` stops with `MCP_GATEWAY_TOKEN is not set. Run ./setup.sh` (or the same for `POSTGRES_PASSWORD`, `N8N_ENCRYPTION_KEY`, `N8N_USER_MANAGEMENT_JWT_SECRET`, `NEXTAUTH_SECRET`, `SALT`, `LANGFUSE_ENCRYPTION_KEY`) | `.env` is missing or a lab secret is blank | `./setup.sh` (or `./setup.sh --non-interactive`) |
| `network dokploy-network declared as external, but could not be found` | The external network does not exist on this host | `./setup.sh` creates it, or run `docker network create dokploy-network` |
| Setup: `N8N_ENCRYPTION_KEY is blank but this lab has started before.` | This project's `n8n_storage` or `postgres_storage` volume exists, so n8n already has a key | Copy the key from the volume into `.env`: `docker compose exec n8n cat /home/node/.n8n/config` shows it |
| doctor: `Docker can use ...: less than the Standard lab needs (4 CPUs and 8 GB)` | Docker has too few resources | Docker Desktop > Settings > Resources, then apply and restart |
| doctor: `the Complete lab requires Docker with 6 CPUs and 16 GB of memory` | The Complete lab on a smaller Docker | Raise the resources, or remove `complete` from `COMPOSE_PROFILES` |
| doctor: `COMPOSE_PROFILES in .env names full, the former name of complete` | An old `.env` | Run `./setup.sh` (it renames the profile), or change `full` to `complete` |
| doctor: `the environment variable COMPOSE_PROFILES names full` | The shell or Dokploy environment still says `full` | Change it to `complete` there: Compose reads the environment before `.env` |
| doctor: `DOMAIN is set (a lab host), but COMPOSE_PROFILES does not include complete` | Langflow and Open WebUI do not start on the lab host | Set `COMPOSE_PROFILES=complete` (on Dokploy: in the project's environment settings) |
| doctor: `unresolved 1Password references inside: ...` | The lab was started without `op run` | `op run --env-file=.env -- docker compose up -d` |
| doctor: `the 1Password CLI (op) is not installed, so these references cannot be resolved` | `.env` holds `op://` references | Install and sign in to `op`, or replace the references with values |
| `n8n-provision`: `ERROR: n8n rejected N8N_ADMIN_PASSWORD (...)` | The password breaks the rule | Fix it as in [Admin password](#admin-password), then `docker compose up -d n8n-provision` |
| `n8n-provision`: `ERROR: the owner cannot sign in with N8N_ADMIN_EMAIL / N8N_ADMIN_PASSWORD` | The password in `.env` changed after the first start; n8n keeps the original | Put the original back, or change it in n8n first (see [Admin password](#admin-password)) |
| `n8n-provision`: `ERROR: n8n did not become ready within 5 minutes (docker compose logs n8n).` | n8n is still migrating its database, or cannot reach Postgres | `docker compose logs n8n`, then `docker compose up -d` |
| `ollama-pull-models-cpu`: `ERROR: could not pull <model> after 5 tries (network?)` | No outbound access to the Ollama registry | Fix the network, then `docker compose up -d ollama-pull-models-cpu` |
| Langfuse sign-in redirects to `https://trace.` on your own computer | `LANGFUSE_URL` is blank and `DOMAIN` is blank | Set `LANGFUSE_URL=http://localhost:3100` (setup does this) and publish `127.0.0.1:3100:3000` (see [Local access](#local-access-and-lab-hosts)) |
| `no matching manifest for linux/arm64/v8` | An older lab image built for amd64 only | `./update.sh` pulls the current multi-arch build. Or build the image locally (see [Images and versions](#images-and-versions)) |
| Compose complains about an undefined network `demo` | A local override file still names the old network | Rename it to `lab` |
| Changing `OPEN_WEBUI_DEFAULT_MODELS` has no effect | Open WebUI stores the setting on its first start | Change the default model in Open WebUI's admin settings |

### Model (lab-chat) issues

| Symptom | Cause | Fix |
|---|---|---|
| `[lab-litellm] ERROR: LITELLM_MASTER_KEY is not set. Run ./setup.sh ...` | No master key | `./setup.sh`, then `docker compose up -d litellm` |
| `[lab-litellm] ERROR: LAB_MODEL_PROVIDER=azure but AZURE_OPENAI_DEPLOYMENT is not set ...` | A forced provider is not fully set | Set the named variable, or `LAB_MODEL_PROVIDER=auto` |
| `[lab-litellm] ERROR: ... holds an unresolved 1Password reference (op://...)` | LiteLLM started without `op run` | `op run --env-file=.env -- docker compose up -d` |
| HTTP 401 `Invalid or missing LiteLLM key. Send the LITELLM_MASTER_KEY value from the lab .env as 'Authorization: Bearer <key>'.` | The client sends another key | Seeded agents: `docker compose run --rm n8n-import` and `docker compose run --rm builders-import`. Your own client: send `LITELLM_MASTER_KEY` |
| `AzureException AuthenticationError - Access denied due to missing subscription key` | `AZURE_OPENAI_API_VERSION=v1` makes LiteLLM send a Bearer token, and the API Management gateway expects its key header | Remove `AZURE_OPENAI_API_VERSION` (default `2024-10-21`), then `docker compose up -d litellm` |
| HTTP 404 for `/openai/responses`, then HTTP 429 `No deployments available for selected model ... All deployments for selected model are in cooldown` | Reasoning is on with tools, so LiteLLM uses the Responses API, which the gateway does not serve | `LAB_REASONING_EFFORT=auto` (the default) or, when the deployment name does not contain `gpt-5`, `none`. Then `docker compose up -d litellm` |
| HTTP 404 from an API Management gateway | `AZURE_OPENAI_ENDPOINT` lacks the gateway path | Paste the gateway URL with its path. `docker compose run --rm --no-deps litellm --check` shows the endpoint in use |
| `lab-chat answered, but no tool call came back` | The model does not call tools | Use a model with tool calling; check `LAB_MODEL_PROVIDER` and the model name |
| doctor: `... Ollama cannot load a chat model. Set OLLAMA_MEM_LIMIT=6g in .env, then docker compose up -d` | `lab-chat` or Open WebUI runs a local model with the 1 GB cap | Set `OLLAMA_MEM_LIMIT=6g` |
| `WARNING: OLLAMA_NUM_CTX=... is below 16384` | The agents with many tools lose their instructions | Raise `OLLAMA_NUM_CTX` |
| Local answers take a long time or time out | CPU inference | Expected. Keep `OLLAMA_THINK=false`, or add one cloud key |

### MCP servers and gateway issues

| Symptom | Cause | Fix |
|---|---|---|
| `GW-AUTH` and `GW-TOOLS` fail: `mcp-gateway is not running (crash loop, N restarts)` | `docker-socket-proxy` is down | `docker compose up -d docker-socket-proxy mcp-gateway` |
| `GW-TOOLS` shows a server with 0 tools | The server was not ready when the gateway listed tools | `docker compose restart mcp-gateway` |
| About 90 `audit event dropped due to backpressure` lines at gateway start | The gateway's audit queue | Harmless |
| `TLS certificate verification failed` | Self-signed Management Server or gateway | Follow [Certificates](#certificates-for-self-signed-servers). Never turn verification off |
| `WARNING: MANAGEMENT_HOST is set without MANAGEMENT_API_KEY ...` | A host without credentials | Set `MANAGEMENT_API_KEY` (or the user name and password), then `docker compose up -d` |
| `WARNING: S1C_URL is set without MANAGEMENT_API_KEY ...` | Smart-1 Cloud needs an API key | Set `MANAGEMENT_API_KEY` |
| doctor: `Smart-1 Cloud signs in with an API key only` | `S1C_URL` with a user name and password | Use `MANAGEMENT_API_KEY` |
| Management tools fail or time out | `MANAGEMENT_HOST` is not reachable from the Docker host | Use an address the Docker host can reach (CloudShare: the public IP, with a return route). `./scripts/doctor.sh --preflight --online` tests it |
| `info spark-management-mcp: stopped because it is not configured` (or `harmony-sase-mcp`) | The keys are blank | Expected. Set the keys, then `docker compose up -d spark-management-mcp` (or `harmony-sase-mcp`) |
| Gaia tools answer that Gaia is not configured, or refuse a gateway | `GAIA_*` is blank, or the gateway is not `GAIA_GATEWAY_IP` or in `GAIA_ALLOWED_GATEWAYS` | Set them, then `docker compose up -d quantum-gaia-mcp` |
| CPInfo Analysis or Threat Emulation answers `Access denied` | The file is outside `./n8n/shared` | Put the file in `./n8n/shared` and use its file name |
| HTTP 400 `Invalid session configuration: ...` | A client sent a partial set of credential headers | Send no credential headers (the servers use `.env`), or a complete set |
| An agent cannot reach its MCP server | Wrong URL, or the server is down | Use the service URL from [MCP servers](#mcp-servers) (Gaia ends in `/mcp`). `./scripts/health-check.sh --verbose` checks the gateway and the 11 servers it fronts |

### Seeding and agents issues

| Symptom | Cause | Fix |
|---|---|---|
| doctor: `builders-import ran while Langflow was not up, so the Langflow agents were not seeded`; acceptance `STACK`: `builders-import ran while Langflow was down (Langflow agents not seeded)` | Langflow started after the importer | `docker compose run --rm builders-import` |
| `builders-import`: `Langflow is not running (add langflow to COMPOSE_PROFILES); skipped.` | The Standard lab has no Langflow | Expected |
| `builders-import`: `ERROR: LITELLM_MASTER_KEY is not set.` | No master key | `./setup.sh`, then `docker compose up -d` |
| `n8n-import`: `not published: <agent> (needs DOMAIN, DEVHUB_MCP_TOKEN)` | Prerequisites are missing | Set them in `.env`, then `docker compose run --rm n8n-import` |
| `n8n-import`: `kept (changed in n8n since the last import): <name>` | You edited the workflow | By design. To replace it: `docker compose run --rm -e N8N_SEED_OVERWRITE=1 n8n-import` |
| `builders-import`: `- <name>: kept, ...` | An edited flow, or one seeded by an older version | By design. To replace it: `docker compose run --rm -e SEED_OVERWRITE=1 builders-import` |
| An n8n chat page asks for a sign-in, or a chat URL returns 401 | The chat triggers use HTTP Basic auth | Sign in with the lab admin |
| The Flowise prediction API refuses calls (Flowise may answer HTTP 500 `Unauthorized`) | `/api/v1/prediction` needs the API key "Lab Agents API" | Send that key (Flowise > API Keys). The canvas chat needs no key |
| The Lakera Guard agents block every message with setup steps | `LAKERA_API_KEY` is blank | Set it, then `docker compose up -d` |
| The RAG agent finds nothing for a covered question | The snippet scores below 0.5 | Check with `docker compose run --rm rag-ingest python3 ingest.py --search "<question>"` (see [Visible RAG](#visible-rag)) |
| `RAG`: `collection cp_docs is missing or empty` | `rag-ingest` has not run or failed | `docker compose run --rm rag-ingest` |
| `RAG`: `Qdrant refused the request (HTTP ...): QDRANT_API_KEY differs from the running Qdrant` | The key changed but Qdrant was not recreated | `docker compose up -d` |
| `evals-run` reports `ERROR` cases | Wiring, not answer quality | Fix the named service, key or unpublished workflow first, then re-run |

### Security lab and AI-Infra-Guard issues

| Symptom | Cause | Fix |
|---|---|---|
| `SECLAB`: the n8n Security Lab agent is not published | `vuln-mcp` was not running at import time | `docker compose run --rm n8n-import` |
| The AI-Infra-Guard web UI does not open in a browser | By design: no route and no published port by default | Publish `127.0.0.1:8088:8088` on `aig-ui` in a local override, then `docker compose up -d aig-ui` (see [AI-Infra-Guard](#ai-infra-guard-profile-ai-red-team)) |
| A scan cannot select a model | `aig-provision` did not run | `docker compose logs aig-provision`, then `docker compose up -d aig-provision` |
| The scan report is in Chinese | Upstream report language | Translate the findings |

### Host and tools

| Symptom | Cause | Fix |
|---|---|---|
| Host Python fails with a TLS certificate error while `curl` and Docker work | On a corporate Mac, the host Python may not trust the corporate CA | Never turn off certificate verification. The lab's scripts run their checks inside containers; use them, or `curl` |
| `tests/acceptance/run.sh` reports `unresolved 1Password references` | The suite was started without `op run` | `op run --env-file=.env -- tests/acceptance/run.sh` |
| Many checks fail with `... is not running` | The lab is not up yet | `docker compose up -d`, wait until `doctor.sh --post-start` shows no blockers |
| A check is `SKIP` with a profile name | That part of the lab is off | Add the profile to `COMPOSE_PROFILES`, then `docker compose up -d` |

---

## Backup and restore

[`scripts/backup-volumes.sh`](../scripts/backup-volumes.sh) writes one encrypted archive of the lab. [`scripts/restore-volumes.sh`](../scripts/restore-volumes.sh) restores it. The [Backup and Recovery Guide](operations/BACKUP_RECOVERY.md) covers schedules and recovery planning.

### Backup

```sh
docker compose stop                    # optional: a fully consistent copy
./scripts/backup-volumes.sh            # asks for a passphrase twice (hidden)
docker compose start
```

| Option | Meaning |
|---|---|
| `--output-dir DIR` | Where to write the archive (default `./backups`, mode 700) |
| `--volumes NAME,...` | Only these volumes |
| `--include-models` | Also the Ollama model volume (left out by default: models can be downloaded again) |
| `--passphrase-file FILE` | Read the passphrase from a file. `BACKUP_PASSPHRASE` in the environment also works |
| `--no-encrypt` | No encryption. Avoid it: the archive holds secrets |
| `--retention-days N` | Delete this project's archives older than N days in the output folder (default 30) |
| `--project-dir DIR` | Another lab directory |

What the archive holds:

- Every Docker volume of the project, found by its Compose label, whatever the project name. The Ollama model volume only with `--include-models`
- `./flowise_data` and `./n8n/shared`
- `.env`: n8n can read its stored credentials only with the `N8N_ENCRYPTION_KEY` in it
- A consistent SQL dump of Postgres (`pg_dumpall`) when `postgres` is running

The archive is `lab-backup-<project>-<time>.tar.gz.enc`, encrypted with AES-256 (`openssl`, PBKDF2). It holds every secret of the lab: treat it like a credential. Keep the passphrase in a separate place; without it the backup cannot be restored. The script needs `docker`, `sh` and `openssl`, and uses the helper image `busybox:1.37.0` (`LAB_BACKUP_IMAGE` overrides it).

### Restore

```sh
docker compose stop
./scripts/restore-volumes.sh backups/lab-backup-<project>-<time>.tar.gz.enc
docker compose up -d
```

| Option | Meaning |
|---|---|
| `--volumes NAME,...` | Restore only these volumes |
| `--with-env` | Make the backup's `.env` the active one (the current one is kept as `.env.before-restore`) |
| `--passphrase-file FILE` | Read the passphrase from a file (`BACKUP_PASSPHRASE` also works) |
| `--yes`, `-y` | Skip the confirmation |
| `--project-dir DIR` | Another lab directory |

The restore is safe by design:

1. It decrypts and checks the whole archive before it changes anything
2. It refuses to run while containers of the lab are running
3. It shows what it will replace and asks you to type the project name
4. It restores the volumes for this directory's Compose project, so a backup from another project name restores fine. Each volume is emptied completely, then filled from the backup
5. It never deletes `./flowise_data` or `./n8n/shared`: the current folders are renamed to `<name>.before-restore-<time>` first
6. It never replaces `.env` silently. Without `--with-env`, the backup's copy is saved as `.env.from-backup` (mode 600). n8n needs the `N8N_ENCRYPTION_KEY` from the backup's `.env` to read its restored credentials

---

## Scripts

| Script | Purpose |
|---|---|
| [`setup.sh`](../setup.sh) | Writes `.env` (see [setup.sh](#setupsh)) |
| [`update.sh`](../update.sh), [`update.ps1`](../update.ps1) | Update the lab (see [Upgrades](#upgrades)) |
| [`scripts/doctor.sh`](../scripts/doctor.sh) | Settings and health checks with the capability matrix |
| [`scripts/health-check.sh`](../scripts/health-check.sh) | `doctor.sh --health` |
| [`scripts/validate-env.sh`](../scripts/validate-env.sh) | `doctor.sh --preflight` |
| [`scripts/n8n-provision.sh`](../scripts/n8n-provision.sh) | Modes `owner` (`n8n-provision`), `import` (`n8n-import`) and `publish` (publishing only) |
| [`scripts/openwebui-provision.sh`](../scripts/openwebui-provision.sh) | Creates the Open WebUI admin |
| [`scripts/aig-provision.sh`](../scripts/aig-provision.sh) | Registers `lab-chat` in AI-Infra-Guard |
| [`scripts/backup-volumes.sh`](../scripts/backup-volumes.sh), [`scripts/restore-volumes.sh`](../scripts/restore-volumes.sh) | Backup and restore |
| [`scripts/lib/labenv.sh`](../scripts/lib/labenv.sh), [`scripts/lib/stack_probe.py`](../scripts/lib/stack_probe.py) | Shared helpers of setup, doctor and the tests |
| [`scripts/flows/langflow_fix.py`](../scripts/flows/langflow_fix.py), [`flowise_fix.py`](../scripts/flows/flowise_fix.py), [`n8n_fix.py`](../scripts/flows/n8n_fix.py) | Generate and check the builder flows (`apply`, `check`; Langflow and Flowise also `snapshot`) |
| [`scripts/check_workflow_uniqueness.py`](../scripts/check_workflow_uniqueness.py) | Fails on repeated names, ids or credential ids, and on dangling references in the n8n workflows |
| [`scripts/convert_guides_to_docx.py`](../scripts/convert_guides_to_docx.py) | Exports the guides to `.docx` (needs `python-docx`) |

---

## CI

| Workflow | Runs on | What it does |
|---|---|---|
| [`ci.yml`](../.github/workflows/ci.yml) (CI) | Push to `main`, every pull request, manual | The merge gate. Offline, no secrets. Five jobs below |
| [`live-stack.yml`](../.github/workflows/live-stack.yml) (Live stack) | Nightly (`23 2 * * *` UTC) and manual | The whole Standard lab from the commit, then the acceptance tests. Not a merge gate |
| [`publish-image.yml`](../.github/workflows/publish-image.yml) (Publish images) | After CI passes on a push to `main`, or manual on `main` | Publishes `cp-agentic-n8n` and `cp-agentic-ips-cve-mcp` to GHCR |
| [`security-scan.yml`](../.github/workflows/security-scan.yml) (Security scan) | Mondays 09:00 UTC, manual | Weekly Trivy report. Report only |

CI jobs:

| Job | Checks |
|---|---|
| Validate | `./setup.sh --non-interactive` (`.env` mode 600, no `change_me`); `docker compose config --quiet` for the default stack, every profile and `--profile '*'`; [`.github/scripts/check_compose_policy.py`](../.github/scripts/check_compose_policy.py) (no host ports, no privileged containers or host namespaces, only the socket proxy mounts the Docker socket, CPU and memory limits everywhere, pinned third-party images, the attack services opt-in and contained, no `env_file`); `langflow_fix.py check`, `flowise_fix.py check`, `n8n_fix.py check`, `check_workflow_uniqueness.py`; shellcheck on every shell script; actionlint; `py_compile` on every Python file; unit tests of the security lab server, Build Your Own MCP, RAG ingest, evals and the code-first agent |
| Setup on macOS | `setup.sh --non-interactive` with `/bin/sh` and a Docker stub (BSD tools), mode 600, a second run leaves `.env` unchanged, `doctor.sh --preflight` |
| MCP server security tests | Builds `docker/n8n/mcp-src` as the image does, then runs [`tests/mcp-src/`](../tests/mcp-src/) with no network: TLS verification, credential separation, sessions, `DELETE`, redaction, the Gaia allow-list, the file folders, Policy Insights parity with npm 0.3.5 |
| RAG threshold (Langflow, n8n) | [`tests/rag-threshold/run.sh`](../tests/rag-threshold/run.sh): the Langflow and n8n retrievers honor the 0.5 threshold |
| Lab images (build, smoke test, scan) | Builds the lab image (`linux/amd64`) and checks its revision label; a smoke test starts every MCP server and lists its tools with no network; builds the exercise image and runs its offline self-test; Trivy gate: no fixable CRITICAL vulnerability (exceptions in [`.github/trivy/trivyignore`](../.github/trivy/trivyignore), each with a reason and an expiry date); an informational HIGH and CRITICAL report |

Details:

- **Live stack.** Builds the lab image from the commit as `:ci` (`LAB_IMAGE_TAG=ci`), runs `./setup.sh --non-interactive`, refuses to run when `vuln-mcp` or AI-Infra-Guard would start, runs `doctor.sh --preflight`, then `tests/integration-test.sh --up --wait 900 -- <--with-model or --no-model> --mock-provider --json acceptance.json`, then `doctor.sh --post-start`. It uploads `acceptance.json` for 14 days. Real model calls happen only when a model key is stored as a repository secret (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, or `AZURE_OPENAI_API_KEY` with the repository variables `AZURE_OPENAI_ENDPOINT` and `AZURE_OPENAI_DEPLOYMENT`)
- **Publish images.** Builds `linux/amd64` and `linux/arm64` (QEMU for the final stage), with provenance and an SBOM. Tags: `:<commit sha>` always; `:latest` only while that commit is still the head of `main`. It checks that the pushed image lists both platforms. AI-Infra-Guard is not built here: compose uses the upstream images
- **Security scan.** Scans the published lab images (both platforms) and every third-party image `docker-compose.yml` pins by digest. Results go to the Security tab (SARIF) and the job log. The merge gate for new code is the Trivy step in CI
- **Pinning.** Every GitHub Action is pinned by commit SHA. Dependabot ([`.github/dependabot.yml`](../.github/dependabot.yml)) updates them weekly. Image pins are bumped by hand
- **Branch protection.** Require the five CI checks on `main`, by their exact names: "Validate", "Setup on macOS", "MCP server security tests", "RAG threshold (Langflow, n8n)" and "Lab images (build, smoke test, scan)"

---

## Related repositories

| Repository | Role |
|---|---|
| [alshawwaf/ubuntu-dokploy-ai](https://github.com/alshawwaf/ubuntu-dokploy-ai) | Installer for a lab host: Ubuntu, Dokploy and Traefik. Deploys this repository |
| [alshawwaf/dev-hub](https://github.com/alshawwaf/dev-hub) | The portal at `hub.<DOMAIN>` that embeds the apps. Hosts the DevHub MCP endpoint |
| [alshawwaf/PolicyPilot](https://github.com/alshawwaf/PolicyPilot) | Access automation over MCP. The source of the `policypilot-mcp` image |
| [alshawwaf/SAML_IDP_Simulator](https://github.com/alshawwaf/SAML_IDP_Simulator) | The identity provider the SCIM agent talks to |
| [alshawwaf/checkpoint-mcp-on-aws-agentcore](https://github.com/alshawwaf/checkpoint-mcp-on-aws-agentcore) | Check Point MCP servers and an agent on Amazon Bedrock AgentCore |
| [alshawwaf/checkpoint-mcp-on-azure-foundry](https://github.com/alshawwaf/checkpoint-mcp-on-azure-foundry) | Check Point MCP servers and an agent on Microsoft Foundry |
| [CheckPointSW/mcp-servers](https://github.com/CheckPointSW/mcp-servers) | The upstream Check Point MCP servers (MIT) |
| [Tencent/AI-Infra-Guard](https://github.com/Tencent/AI-Infra-Guard) | The upstream AI red-teaming platform (Apache-2.0) |

---

## Guides index

The guides live in [`docs/guides/`](guides/). Start with:

- [MCP Gateway, explained](guides/MCP_Gateway_Explained.md) and [MCP Gateway agents](guides/MCP_Gateway_Agent_Guide.md)
- The per-server guides: [Management](guides/Quantum_Management_MCP_Agent_Guide.md), [Management Logs](guides/Management_Logs_MCP_Agent_Guide.md), [Policy Insights](guides/Policy_Insights_MCP_Agent_Guide.md), [Threat Prevention](guides/Threat_Prevention_MCP_Agent_Guide.md) (and the [node-by-node deep dive](guides/CheckPoint_Threat_Prevention_Guide.md)), [HTTPS Inspection](guides/HTTPS_Inspection_MCP_Agent_Guide.md), [Gaia](guides/Quantum_Gaia_MCP_Agent_Guide.md), [Gateway CLI](guides/Quantum_Gateway_CLI_MCP_Agent_Guide.md), [CPInfo Analysis](guides/CPInfo_Analysis_MCP_Agent_Guide.md), [Documentation](guides/Documentation_MCP_Agent_Guide.md), [Threat Emulation](guides/Threat_Emulation_MCP_Agent_Guide.md) and [Reputation Service](guides/Reputation_Service_MCP_Agent_Guide.md)
- [Observability with Langfuse](guides/Observability_Langfuse.md), [Visible RAG](guides/Visible_RAG.md), [Evals Harness](guides/Evals_Harness.md)
- [Lakera Guard Screening Agent](guides/Lakera_Guard_Screening_Agent_Guide.md), [Build Your Own MCP Server](guides/Build_Your_Own_MCP_Exercise.md), [MCP Security Lab](guides/MCP_Security_Lab.md)
- [Identity provisioning (SCIM)](guides/Identity_Provisioning_SCIM_Agent_Guide.md), [PolicyPilot agents and MCP sidecar](guides/PolicyPilot_Gateway_Sidecar_Guide.md), [Capstone: Zero Trust onboarding](guides/Capstone_Zero_Trust_Onboarding.md)

Operations and development: [Production Deployment](operations/PRODUCTION_DEPLOYMENT.md), [Backup and Recovery](operations/BACKUP_RECOVERY.md), [Developer Guide](development/DEVELOPER_GUIDE.md), [Directory Structure](development/DIRECTORY_STRUCTURE.md), [Tests README](../tests/README.md), [MCP server patches](../docker/n8n/mcp-src/PATCHES.md), [certificates](../certs/README.md).

---

## License

- This repository is [MIT](../LICENSE), © 2025 Check Point Software Technologies Ltd.
- The vendored MCP sources in `docker/n8n/mcp-src/` are [MIT](../docker/n8n/mcp-src/LICENSE), with the same copyright holder. Keep the notice when you redistribute the tarballs from `/opt/artifacts`
- AI-Infra-Guard (Tencent/AI-Infra-Guard) is licensed Apache-2.0
