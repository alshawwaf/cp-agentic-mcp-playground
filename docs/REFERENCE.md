# Reference

This is the detailed reference for the Check Point Agentic MCP Playground. The [README](../README.md) covers what the lab is and the short path to a running stack. Everything else lives here.

This page was checked against [`docker-compose.yml`](../docker-compose.yml) and the files it mounts. If the two ever disagree, the compose file is right.

## Contents

1. [Stack components](#stack-components)
2. [One-shot helpers](#one-shot-helpers)
3. [Compose profiles](#compose-profiles)
4. [Deployment and routing](#deployment-and-routing)
5. [Running on a plain Docker host](#running-on-a-plain-docker-host)
6. [MCP servers](#mcp-servers)
7. [MCP Gateway](#mcp-gateway)
8. [Agents and seeding](#agents-and-seeding)
9. [Chat models and provider keys](#chat-models-and-provider-keys)
10. [Observability](#observability)
11. [RAG, evals, and labs](#rag-evals-and-labs)
12. [Ollama models](#ollama-models)
13. [Configuration](#configuration)
14. [Data volumes](#data-volumes)
15. [Development](#development)
16. [Troubleshooting](#troubleshooting)
17. [Security notes](#security-notes)
18. [Guides index](#guides-index)
19. [Related repositories](#related-repositories)
20. [License](#license)

## Stack components

`docker-compose.yml` defines 40 services. 35 have no profile and start on every `docker compose up`. The other 5 sit behind [opt-in profiles](#compose-profiles).

| Component | Service | Image | Role |
|---|---|---|---|
| n8n | `n8n` | `ghcr.io/alshawwaf/cp-agentic-n8n:latest`, built `FROM n8nio/n8n:2.40.7` | Workflow automation and agent builder |
| n8n CLI | `n8n-import` | `ghcr.io/alshawwaf/cp-agentic-n8n:latest`, the same image as `n8n` | Imports the n8n agents. Uses the n8n image, so the CLI and server versions always match |
| PostgreSQL | `postgres` | `postgres:16-alpine` | Databases for n8n, Langfuse and, optionally, Flowise |
| Ollama | `ollama-cpu` | `ollama/ollama:0.31.1` | Local LLM server. Always runs |
| Open WebUI | `open-webui` | `ghcr.io/open-webui/open-webui:v0.11.4`, pinned by digest | Chat UI for the local Ollama models. No n8n pipe ships ([details](#upgrading-n8n-and-open-webui)) |
| Flowise | `flowise` | `flowiseai/flowise@sha256:85038c3b…` | Low-code agent builder with native Langfuse tracing |
| Langflow | `langflow` | `langflowai/langflow:1.10.1` | Visual flow builder |
| Langfuse | `langfuse` | `langfuse/langfuse:2` | Tracing UI (v2, one Postgres-backed container) |
| LiteLLM | `litellm` | `ghcr.io/berriai/litellm:main-v1.66.0-stable` | Internal OpenAI-compatible proxy. Traces n8n and Langflow model calls |
| Qdrant | `qdrant` | `qdrant/qdrant:v1.12.4` | Vector database for the Visible RAG demo |
| AI-Infra-Guard | `aig-webserver`, `aig-agent` | `ghcr.io/alshawwaf/cp-agentic-aig-webserver:latest`, `ghcr.io/alshawwaf/cp-agentic-aig-agent:latest` | AI red teaming: MCP security scans and jailbreak evaluation |
| MCP servers | 14 sidecars | `ghcr.io/alshawwaf/cp-agentic-n8n:latest` | Check Point MCP servers over Streamable HTTP. See [MCP servers](#mcp-servers) |
| MCP Gateway | `mcp-gateway` | `docker/mcp-gateway@sha256:97ec61bc…` | One Bearer-authenticated endpoint for 11 of the MCP servers |

Long-running services restart `unless-stopped`. There are 26: the 12 application services above (both AI-Infra-Guard containers count) and the 14 MCP sidecars. Short-lived services are in [One-shot helpers](#one-shot-helpers).

### Version pins

- **n8n.** The only real pin is the `FROM` line in [`docker/n8n/Dockerfile`](../docker/n8n/Dockerfile), now `n8nio/n8n:2.40.7`. `n8n`, `n8n-import` and the 14 sidecars all run `ghcr.io/alshawwaf/cp-agentic-n8n:latest`, so the CLI and the server cannot drift apart. The `x-n8n` anchor also names `n8nio/n8n:2.40.7`, but that image never runs, because `n8n` overrides it.
- **The pin and the running version can differ.** Compose runs whatever the GHCR `:latest` tag holds. [`publish-image.yml`](../.github/workflows/publish-image.yml) rebuilds that tag only on a push to `main` or a manual run. Until it does, a Dockerfile bump changes nothing on a host. Check the running version with `docker compose exec n8n n8n --version`.
- **Open WebUI.** Pinned as `v0.11.4@sha256:9591b13f…`. The tag is for readability. The digest decides what runs.
- **Flowise.** The digest matches no current Docker Hub tag. The image was built on 2026-06-25, the day 3.1.3 shipped. The committed flows target Flowise 3.1.2. Custom MCP header auth needs 3.0.2 or later.
- **Langfuse.** `langfuse/langfuse:2` floats within the v2 line. Pin a patch tag for full reproducibility.
- **MCP Gateway.** Pinned by digest. The image was created on 2026-06-30.
- **Helpers.** `curlimages/curl:8.9.1`, `python:3.12-alpine` and `postgres:16-alpine`.

### Prebuilt images

[`publish-image.yml`](../.github/workflows/publish-image.yml) builds four public images on every push to `main` and on manual dispatch. Each image gets a `latest` tag and a commit-SHA tag.

| Image | Built from | Used by |
|---|---|---|
| `ghcr.io/alshawwaf/cp-agentic-n8n` | [`docker/n8n/Dockerfile`](../docker/n8n/Dockerfile) | `n8n`, `n8n-import` and all 14 MCP sidecars |
| `ghcr.io/alshawwaf/cp-agentic-aig-webserver` | `Dockerfile` in [Tencent/AI-Infra-Guard](https://github.com/Tencent/AI-Infra-Guard) | `aig-webserver` |
| `ghcr.io/alshawwaf/cp-agentic-aig-agent` | `Dockerfile_Agent` in Tencent/AI-Infra-Guard | `aig-agent` |
| `ghcr.io/alshawwaf/cp-agentic-ips-cve-mcp` | [`exercises/build-your-own-mcp/`](../exercises/build-your-own-mcp/) | `ips-cve-mcp` (`exercises` profile) |

`n8n`, `aig-webserver`, `aig-agent` and `ips-cve-mcp` use `pull_policy: always`. Every `docker compose up` re-pulls them and replaces any local image with the same tag. See [Build the custom image locally](#build-the-custom-image-locally).

All four images are built for `linux/amd64` only, because the workflow sets no `platforms:`. On an arm64 host, such as an Apple Silicon Mac, the pull fails with `no matching manifest for linux/arm64/v8`. See [Running on a plain Docker host](#running-on-a-plain-docker-host) for the workaround.

The custom n8n image adds 14 MCP command-line servers under `/usr/local/bin`:

- 13 are built from sources vendored from [CheckPointSW/mcp-servers](https://github.com/CheckPointSW/mcp-servers) (MIT, package version 1.0.1), with local patches.
- Policy Insights comes from npm, pinned to `@chkp/policy-insights-mcp@0.3.5`.
- The built npm tarballs stay inside the image at `/opt/artifacts`. The repo has no GitHub Releases. Copy them out with `docker compose cp n8n:/opt/artifacts ./artifacts`.

## One-shot helpers

| Service | Image | Starts after | What it does |
|---|---|---|---|
| `n8n-provision` | `curlimages/curl:8.9.1` | `n8n` healthy | Creates the n8n owner account ([`n8n-provision.sh`](../scripts/n8n-provision.sh)) |
| `n8n-import` | `ghcr.io/alshawwaf/cp-agentic-n8n:latest` | `n8n-provision` succeeded | Injects secrets, imports 12 credentials and 34 workflows, and activates them. See [What n8n-import does](#what-n8n-import-does) |
| `flowise-db-init` | `postgres:16-alpine` | `postgres` healthy | Creates the `flowise` database. Flowise uses it only when `FLOWISE_DATABASE_TYPE=postgres` |
| `langfuse-db-init` | `postgres:16-alpine` | `postgres` healthy | Creates the `langfuse` database |
| `openwebui-provision` | `curlimages/curl:8.9.1` | `open-webui` started | Creates the Open WebUI admin from `OPEN_WEBUI_ADMIN_EMAIL` and `OPEN_WEBUI_ADMIN_PASSWORD`. Skips with a warning when they are unset |
| `builders-import` | `python:3.12-alpine` | `flowise` and `langflow` started | Seeds 33 flows into each builder and creates credentials. See [What builders-import does](#what-builders-import-does) |
| `rag-ingest` | `python:3.12-alpine` | `qdrant` and `ollama-cpu` healthy | Embeds the RAG corpus into Qdrant |
| `evals-run` | `python:3.12-alpine` | `n8n` started | Runs 8 scored chat cases against n8n and writes a report. See [Evals](#evals) |

All eight use `restart: "no"` and are idempotent. On re-runs, HTTP 400 or 403 answers such as "already registered" are expected.

`evals-run` has no profile, so it runs on every `docker compose up -d`, even though its compose comment calls it opt-in. On a first deploy it can start before `n8n-import` finishes. Re-run it later with `docker compose up evals-run`.

The model pullers are not one-shots. `ollama-pull-models-cpu`, and `ollama-pull-models-gpu` under the `gpu-nvidia` profile, pull `OLLAMA_MODELS` and then run `tail -f /dev/null` so their logs stay available. They never exit, so `docker compose ps` shows them as running.

## Compose profiles

The core stack has no profile. Four profiles add opt-in services.

| Profile | Adds | Needs | Guide |
|---|---|---|---|
| `gpu-nvidia` | `ollama-gpu`, `ollama-pull-models-gpu` | NVIDIA GPU and the NVIDIA Container Toolkit | None |
| `exercises` | `ips-cve-mcp` on port 3013 | `IPS_CLIENT_ID`, `IPS_ACCESS_KEY` | [Build Your Own MCP Server](guides/Build_Your_Own_MCP_Exercise.md) |
| `security-lab` | `vuln-mcp` on port 3099, intentionally vulnerable, no auth | Nothing | [MCP Security Lab](guides/MCP_Security_Lab.md) |
| `policypilot` | `policypilot-mcp` on port 3020 | A locally built image, `PILOT_MCP_TOKEN`, `PILOT_ENCRYPTION_KEY` | [PolicyPilot behind the Gateway](guides/PolicyPilot_Gateway_Sidecar_Guide.md) |

Enable a profile per command or in `.env`:

```bash
docker compose --profile exercises up -d
# or set it once in .env:
# COMPOSE_PROFILES=exercises,security-lab
```

- **There is no `cpu` profile.** `.env-example` sets `COMPOSE_PROFILES=cpu`, and `setup.sh`, `ci.yml` and the tests pass `--profile cpu`. That value matches no service and changes nothing.
- **`gpu-nvidia` adds a GPU Ollama next to the CPU one.** It does not replace it. `ollama-cpu` always runs, and both share the `ollama_storage` volume. Open WebUI, `rag-ingest`, the n8n Ollama credential and AI-Infra-Guard all call `http://ollama-cpu:11434`. Change those URLs to `http://ollama-gpu:11434` to use the GPU.
- **`evals-run` is not behind a profile.** See [One-shot helpers](#one-shot-helpers).

## Deployment and routing

The lab host runs [alshawwaf/ubuntu-dokploy-ai](https://github.com/alshawwaf/ubuntu-dokploy-ai). It installs [Dokploy](https://dokploy.com) on bare-metal Ubuntu, with Traefik for ingress and Let's Encrypt for TLS. It fetches this repo to `/opt/cp-agentic-mcp-playground`, pulls the GHCR images and publishes the web apps under `DOMAIN`.

Before you run the installer:

- **Use an amd64 server.** The [prebuilt images](#prebuilt-images) have no arm64 build.
- **Default ingress (`letsencrypt`).** The host needs public inbound ports 80 and 443, and a wildcard DNS `A` record (`*.<domain>`) that points at it. You create that record yourself. The installer only checks it, and stops if it is missing.
- **NAT or no inbound ports (`--ingress tunnel`).** The domain must be on Cloudflare. Put `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` in `answers.env`. The installer then creates the tunnel and the DNS record.
- **Set the admin password.** Put an `N8N_ADMIN_PASSWORD` that meets the [admin password rule](#admin-password) in `answers.env`.

| App | URL | Route defined by |
|---|---|---|
| n8n | `https://n8n.<domain>` | The installer's Dokploy domain config. n8n has no Traefik labels |
| Open WebUI | `https://chat.<domain>` | The installer's Dokploy domain config |
| Flowise | `https://flowise.<domain>` | The installer's Dokploy domain config |
| Langflow | `https://langflow.<domain>` | The installer's Dokploy domain config |
| Langfuse | `https://trace.<domain>` | Traefik labels in compose |
| AI-Infra-Guard | `https://aig.<domain>` | Traefik labels in compose |

- Flowise, Open WebUI and Langflow carry only `traefik.enable=true` and `traefik.docker.network=dokploy-network`. When compose also declared their routers, Traefik saw two router sets for one host and returned intermittent 404s.
- Langfuse and AI-Infra-Guard each declare a `web` router and a `websecure` router. The `websecure` router uses the `letsencrypt` resolver. The plain `web` router exists because the lab's Cloudflare tunnel delivers traffic on port 80.
- Dev Hub embedding: Langfuse has its own `langfuse-hubframe` middleware. Langflow relies on the installer's global `hubframe@file` middleware. Flowise allows framing by default. AI-Infra-Guard has no framing middleware.

### Networks

| Network | Created by | Members |
|---|---|---|
| `demo` | Compose, as `<project>_demo` | Every service |
| `dokploy-network` | Must already exist (`external: true`) | `ollama-cpu`, `ollama-gpu`, `flowise`, `open-webui`, `langflow`, `langfuse`, `aig-webserver` |

Ollama joins `dokploy-network` on purpose, so standalone Dokploy apps can reach `http://ollama-cpu:11434`. It still has no route and no host port.

The Compose project name defaults to the directory name. In a clone named `cp-agentic-mcp-playground`, the private network is `cp-agentic-mcp-playground_demo`. Find yours with `docker network ls | grep demo`.

### Internal endpoints

No service publishes a host port. `docker-compose.yml` has no `ports:` keys. Inside the `demo` network, use these URLs:

| Service | URL |
|---|---|
| n8n | `http://n8n:5678` |
| Open WebUI | `http://open-webui:8080` |
| Flowise | `http://flowise:<FLOWISE_PORT>` (3001 in `.env-example`) |
| Langflow | `http://langflow:7860` |
| Langfuse | `http://langfuse:3000` |
| LiteLLM | `http://litellm:4000` |
| Qdrant | `http://qdrant:6333` |
| Ollama | `http://ollama-cpu:11434` |
| MCP Gateway | `http://mcp-gateway:8080/mcp` |
| AI-Infra-Guard | `http://aig-webserver:8088` |
| PostgreSQL | `postgres:5432` |

MCP sidecar URLs are in [MCP servers](#mcp-servers).

## Running on a plain Docker host

The stack targets the Dokploy host. On any other Docker host, you need Docker Engine with Compose v2 and Python 3. `docker compose up` then needs these extra steps:

1. **Allow outbound HTTPS** from the host and the containers. The first start pulls images from ghcr.io and Docker Hub, and models from the Ollama registry (`OLLAMA_MODELS`). At runtime, the agents call your model provider and the Check Point cloud APIs. The GHCR images are public, so no login is needed.
2. **On arm64, force amd64 images.** The [prebuilt images](#prebuilt-images) are amd64 only. On an Apple Silicon Mac, turn on Rosetta emulation in Docker Desktop. Then run `export DOCKER_DEFAULT_PLATFORM=linux/amd64` in the shell where you run Compose. Emulated containers start more slowly.
3. **Create the external network.** Compose never creates an external network. Without it, startup stops with `network dokploy-network declared as external, but could not be found`.
4. **Set a valid admin password.** `N8N_ADMIN_PASSWORD` must meet the [admin password rule](#admin-password). `setup.sh -y` on Linux generates one that does. The `.env-example` value, `change_me`, does not.
5. **Generate the Langfuse secrets.** `NEXTAUTH_SECRET` and `SALT` are blank in `.env-example`, and `setup.sh` does not fill them. Langfuse v2 will not start without them.
6. **Add a cloud model key.** Every shipped agent defaults to a cloud model. See [Chat models and provider keys](#chat-models-and-provider-keys).
7. **Publish ports if you want to browse locally.** Use an override file, shown below.

```bash
./setup.sh -y                                # creates .env with random secrets (Linux)
docker network create dokploy-network
python3 integrations/observability/gen_secrets.py --with-keys
# Replace the blank NEXTAUTH_SECRET, SALT, LANGFUSE_ENCRYPTION_KEY,
# LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY lines in .env with the output.
docker compose up -d
docker compose ps -a n8n-import              # wait until it shows Exited (0)
docker compose restart n8n                   # first run only
```

The last step matters on a first run. `n8n-import` activates the workflows after n8n has already started, and n8n registers active chat webhooks only at startup. Restart n8n only after `n8n-import` has exited. A workflow activated after the restart gets no webhook, and its chat URL returns 404 until the next restart. On Dokploy, each redeploy recreates n8n, so this happens by itself.

`docker compose ps -a` lists every one-shot. `n8n-provision`, `n8n-import` and `builders-import` should each show `Exited (0)`. If `n8n-provision` shows `Exited (1)`, `n8n-import` never starts. `evals-run` exits 1 whenever a case fails, which is normal before you add live credentials. See [Troubleshooting](#troubleshooting).

For local browsing, create `docker-compose.override.yml` next to the compose file. Compose merges it automatically. Keep it local.

```yaml
services:
  n8n:
    ports: ["127.0.0.1:5678:5678"]
  open-webui:
    ports: ["127.0.0.1:3000:8080"]
  flowise:
    ports: ["127.0.0.1:3001:3001"]   # container side must equal FLOWISE_PORT
  langflow:
    ports: ["127.0.0.1:7860:7860"]
  langfuse:
    ports: ["127.0.0.1:3100:3000"]
  aig-webserver:
    ports: ["127.0.0.1:8088:8088"]
```

- Binding to `127.0.0.1` keeps the apps off your network. Several are open by design. See [Security notes](#security-notes).
- `.env-example` already sets `WEBHOOK_URL` and `N8N_EDITOR_BASE_URL` to `http://localhost:5678/`. Compose also passes `WEBHOOK_URL` to n8n as `N8N_WEBHOOK_URL`.
- Langfuse sets `NEXTAUTH_URL=https://trace.${DOMAIN}`, so expect its sign-in redirects to use that host.
- With `DOMAIN` blank, the Traefik host rules become `trace.` and `aig.`, which is harmless. `n8n-import` derives the domain from `N8N_HOST` instead (`localhost` in `.env-example`). The external-endpoint agents then point at `*.localhost` and cannot connect.

## MCP servers

The 14 Check Point MCP servers run as sidecars from the custom n8n image. Each one runs `<cli> --transport http --transport-port <port>` and listens only on the `demo` network.

In the table, "Management credentials" means `MANAGEMENT_HOST` plus `MANAGEMENT_API_KEY`. Compose passes the key to each server as `API_KEY`.

| Server | Service URL | Gateway name | Credentials | Guide |
|---|---|---|---|---|
| Documentation | `http://mcp-documentation:3000` | `documentation` | `DOC_CLIENT_ID`, `DOC_SECRET_KEY`, `DOC_REGION` | [Guide](guides/Documentation_MCP_Agent_Guide.md) |
| HTTPS Inspection | `http://mcp-https-inspection:3001` | `https-inspection` | Management credentials | [Guide](guides/HTTPS_Inspection_MCP_Agent_Guide.md) |
| Management | `http://mcp-quantum-management:3002` | `quantum-management` | Management credentials | [Guide](guides/Quantum_Management_MCP_Agent_Guide.md) |
| Management Logs | `http://mcp-management-logs:3003` | `management-logs` | Management credentials | [Guide](guides/Management_Logs_MCP_Agent_Guide.md) |
| Threat Emulation | `http://threat-emulation-mcp:3004` | `threat-emulation` | `TE_API_KEY` | [Guide](guides/Threat_Emulation_MCP_Agent_Guide.md) |
| Threat Prevention | `http://threat-prevention-mcp:3005` | `threat-prevention` | Management credentials | [Guide](guides/Threat_Prevention_MCP_Agent_Guide.md) |
| Spark Management | `http://spark-management-mcp:3006` | Not fronted | `SPARK_MGMT_*` | None |
| Reputation Service | `http://reputation-service-mcp:3007` | `reputation-service` | `REPUTATION_API_KEY` | [Guide](guides/Reputation_Service_MCP_Agent_Guide.md) |
| SASE | `http://harmony-sase-mcp:3008` | Not fronted | `HARMONY_SASE_*` | None |
| Gateway CLI | `http://quantum-gw-cli-mcp:3009` | `gw-cli` | Management credentials | [Guide](guides/Quantum_Gateway_CLI_MCP_Agent_Guide.md) |
| Gateway Connection Analysis | `http://quantum-gw-connection-analysis-mcp:3010` | Not fronted | Management credentials | None |
| Gaia | `http://quantum-gaia-mcp:3011/mcp` | `gaia` | Management credentials and `GAIA_*` | [Guide](guides/Quantum_Gaia_MCP_Agent_Guide.md) |
| CPInfo Analysis | `http://cpinfo-analysis-mcp:3012` | `cpinfo-analysis` | Management credentials | [Guide](guides/CPInfo_Analysis_MCP_Agent_Guide.md) |
| Policy Insights | `http://policy-insights-mcp:3013` | `policy-insights` | Management credentials | None |

- The host part of each URL is both the Compose service name and the container name.
- **Gaia is the only server reached at `/mcp`.** The others answer at their root URL.
- **Gaia needs a gateway.** It talks to a gateway's Gaia REST API, not to the Management Server. Its tools return a clear error until `GAIA_GATEWAY_IP`, `GAIA_USERNAME` and `GAIA_PASSWORD` are set. `GAIA_GATEWAY_PORT` defaults to 443.
- **Policy Insights** needs Management API v2.1 (R82.10 or later), with Policy Insights enabled on the Management Server.
- **`MANAGEMENT_HOST` must be reachable from the Docker host.** In a CloudShare lab, use the Management Server's public IP and make sure replies route back out. The [MCP Gateway Agent Guide](guides/MCP_Gateway_Agent_Guide.md) covers lab connectivity.
- **Three servers are idle.** Spark Management, SASE and Gateway Connection Analysis run, but no shipped agent uses them and the gateway does not front them.
- **File drop.** `./n8n/shared` is mounted into `n8n` at `/data/shared`, into `threat-emulation-mcp` at `/data/shared` and into `cpinfo-analysis-mcp` at `/data/cpinfo`. Put files for Threat Emulation or CPInfo Analysis there.

### Connecting a client

Every shipped n8n workflow that calls MCP uses n8n's native **MCP Client Tool** node (`@n8n/n8n-nodes-langchain.mcpClientTool`). The old community MCP node, with its package mode and Connection Type fields, is no longer used.

| Target | Endpoint URL | Authentication |
|---|---|---|
| One sidecar | The service URL from the table above | None |
| The gateway | `http://mcp-gateway:8080/mcp` | Bearer, with the `CP MCP Gateway Bearer` credential |

Flowise and Langflow flows use the same URLs.

## MCP Gateway

The [Docker MCP Gateway](guides/MCP_Gateway_Explained.md) puts 11 of the sidecars behind one endpoint.

| Setting | Value |
|---|---|
| Image | `docker/mcp-gateway@sha256:97ec61bc…` |
| Endpoint | `http://mcp-gateway:8080/mcp`, on the `demo` network only |
| Transport | Streamable HTTP (`--transport=streaming`) |
| Auth | `Authorization: Bearer <MCP_GATEWAY_TOKEN>`. Compose passes the token to the gateway as `MCP_GATEWAY_AUTH_TOKEN` |
| Default token | `cp-mcp-gateway-training-token`. It is public, so override it |
| Catalog | [`mcp-gateway/catalog.yaml`](../mcp-gateway/catalog.yaml), mounted as `checkpoint-mcp.yaml` (catalog name `checkpoint-mcp`) |
| Enabled servers | `documentation`, `quantum-management`, `policy-insights`, `cpinfo-analysis`, `https-inspection`, `management-logs`, `gaia`, `gw-cli`, `reputation-service`, `threat-emulation`, `threat-prevention` (the `--servers` flag) |
| Start gate | Waits for healthy `mcp-documentation`, `mcp-quantum-management` and `policy-insights-mcp` |
| Health check | `nc -z 127.0.0.1 8080` |
| Host access | Mounts `/var/run/docker.sock` |
| Other settings | `DOCKER_MCP_IN_CONTAINER=1`. `DOCKER_MCP_ALLOW_INSECURE_REMOTE_URLS=1`, because the sidecars speak plain HTTP on the private network |

Things to know:

- **Pin the token.** Without `MCP_GATEWAY_AUTH_TOKEN`, the gateway mints a new token on every restart. That breaks every stored client credential.
- **Tools are listed once, at startup.** The gateway drops a sidecar that is not listening yet, until the gateway restarts. Only 3 of the 11 servers have health checks, so the start gate covers only those 3.
- **The handshake matters.** A client must send `initialize`, then `notifications/initialized`, then `tools/list`. A bare `tools/list` returns no tools. Replies arrive as Server-Sent Events. A wrong or missing token returns 401.
- **Check the tool count** with `./scripts/health-check.sh --verbose`. It runs a real handshake from inside the network.

**Per-session servers.** Stock server packages connect a single MCP server object to every HTTP session. A gateway opens concurrent sessions, so the second one fails. The vendored sources carry a patch that creates one server per session. Every vendored package now exports that per-session `createServer` factory, so every fronted server is gateway-safe. [`PATCHES.md`](../docker/n8n/mcp-src/PATCHES.md) explains the patch. Its capability matrix still lists only Management and Documentation as patched, which is out of date.

### Adding a server to the gateway

1. Add a `registry` entry to `mcp-gateway/catalog.yaml` with `type: "remote"`, the service URL and `transport_type: "streamable"`.
2. Add the entry's name to the `--servers` list in the `mcp-gateway` command.
3. Optional: give the sidecar a health check, and add it to the gateway's `depends_on` with `condition: service_healthy`.
4. Recreate the gateway with `docker compose up -d mcp-gateway`.

The [PolicyPilot guide](guides/PolicyPilot_Gateway_Sidecar_Guide.md) walks through these steps for `policypilot-mcp`.

## Agents and seeding

The example agents live in the repo. One-shot importers seed them into the running builders on every deploy. Committed files carry placeholders only, never real secrets.

### n8n workflows

[`n8n/backup/workflows/`](../n8n/backup/workflows/) holds 34 workflows. 32 of them are chat agents.

| Group | Count | Files |
|---|---|---|
| Direct-sidecar agents | 11 | One per fronted server, for example `quantum-management-mcp.json` |
| Gateway twins | 11 | `*-via-gateway.json`: the same 11 servers, reached through the gateway |
| Other chat agents | 10 | `Lakera-Playground.json`, `guarded-chat.json`, `fleet-commander.json`, `soc-response-chain.json`, `devhub-agent.json`, `policypilot-management-agent.json`, `policypilot-dynamic-layer-agent.json`, `identity-provisioning-scim-agent.json`, `mcp-security-lab-agent.json`, `rag-cp-docs-agent.json` |
| RAG retriever | 1 | `rag-cp-docs-retriever.json`, a sub-workflow with an Execute Workflow trigger |
| Nightly Self-QA | 1 | `nightly-self-qa.json`. Runs daily at 02:00, pings the agents and scores the replies |

A standard agent has this shape:

- A chat trigger, then a **Normalize input** node, then an **AI Agent** node.
- The agent has **Conversation Memory** (window buffer) and one **MCP Client Tool** node.
- The wired model is **Azure OpenAI (default)**, `gpt-5.4-2026-03-05`. The exception is `Lakera-Playground.json`, which runs on Gemini 2.5 Flash Lite (`models/gemini-2.5-flash-lite`).
- Four alternates sit unwired on the canvas: Ollama `qwen3.5:4b`, Gemini 2.5 Pro, OpenAI `gpt-5.1` and Claude Opus 4.8. To switch, connect one of them to the agent's Chat Model input instead of Azure.
- Gateway twins use the node's `include: selected` filter. It scopes the gateway's full tool list down to one server's tools.

### What n8n-import does

1. Waits for Postgres and n8n to accept connections.
2. Copies `./n8n/backup` to a temporary directory, so the committed files stay untouched.
3. Replaces placeholders in the credential copies:

| Placeholder | Source variable | Credential files | If the variable is empty |
|---|---|---|---|
| `__POSTGRES_PASSWORD__` | `POSTGRES_PASSWORD` | `postgres.json` | Not applicable (required) |
| `__MCP_GATEWAY_TOKEN__` | `MCP_GATEWAY_TOKEN` | `gateway-bearer.json` | Training default |
| `__LITELLM_MASTER_KEY__` | `LITELLM_MASTER_KEY` | `openai.json`, `azure-OpenAI.json`, `anthropic.json`, `Google Gemini(PaLM) Api account.json` | Training default |
| `__LAKERA_API_KEY__` | `LAKERA_API_KEY` | `Lakera.json` | Imported with the placeholder, plus a warning |
| `__IDP_SCIM_TOKEN__` | `IDP_SCIM_TOKEN` | `CP-SCIM-IdP-Token.json` | Imported with the placeholder, plus a warning |
| `__QDRANT_API_KEY__` | `QDRANT_API_KEY` | `quadrant.json` | Imported with the placeholder, plus a warning |
| `__PILOT_MCP_TOKEN__` | `PILOT_MCP_TOKEN` | `policypilot-bearer.json` | File dropped, plus a warning |
| `__DEVHUB_MCP_TOKEN__` | `DEVHUB_MCP_TOKEN` | `devhub-bearer.json` | File dropped, plus a warning |

4. Replaces `{{DOMAIN}}` in every workflow with `DOMAIN`. If `DOMAIN` is blank, it uses `N8N_HOST` minus a leading `n8n.`.
5. Imports the 12 credentials in [`credentials_public/`](../n8n/backup/credentials_public/) and all 34 workflows.
6. Publishes (activates) every workflow. n8n registers their webhooks on its next start.

Every committed workflow has a fixed `id`, and `n8n import:workflow` upserts by ID. Each deploy therefore replaces edits to a seeded workflow with the repo version. The old version stays in the workflow's history. To keep a change, duplicate the workflow under a new name, or commit the change to `n8n/backup/workflows/`. This differs from `builders-import`, which skips flows that already exist.

The Ollama credential (`ollama.json`) carries no secret. It points at `http://ollama-cpu:11434`.

`setup.sh` also copies `credentials_public/` to `n8n/backup/credentials/`. Nothing reads that copy.

### Flowise and Langflow flows

[`integrations/flowise/`](../integrations/flowise/) and [`integrations/langflow/`](../integrations/langflow/) each hold 33 flows. [`builders_agents.json`](../integrations/builders_agents.json) catalogues 32 of them. `seed_builders.py` adds the umbrella gateway agent (`cp-mcp-gateway-agent.*`).

| Kind | Count | Endpoint | Agents |
|---|---|---|---|
| `gateway` | 14 | `http://mcp-gateway:8080/mcp` | 11 per-server agents, Fleet Commander, Guarded Agent, SOC Response Chain |
| `direct` | 13 | A sidecar URL | 11 per-server agents, Lakera Playground, MCP Security Lab |
| `external` | 4 | Services outside this stack | DevHub, two PolicyPilot agents, SCIM provisioning |
| `native` | 1 | Qdrant collection `cp_docs` | Visible RAG |
| Umbrella | 1 | `http://mcp-gateway:8080/mcp` | Added by `seed_builders.py` |

The MCP Security Lab flows target `http://vuln-mcp:3099`, which runs only under the `security-lab` profile.

### What builders-import does

`builders-import` runs [`seed_builders.py`](../integrations/seed_builders.py), a stdlib-only script, on `python:3.12-alpine`.

1. Waits for Flowise at `http://flowise:${FLOWISE_PORT:-3020}` and Langflow at `http://langflow:7860`.
2. Signs in with the stack admin (`N8N_ADMIN_EMAIL`, `N8N_ADMIN_PASSWORD`). On a fresh Flowise database, it first registers that admin. `FLOWISE_API_KEY` or `LANGFLOW_API_KEY` override the sign-in.
3. Imports flows by name and skips any flow that already exists. If the existing flow list cannot be parsed, it imports nothing rather than risk duplicates.
4. In Flowise, creates a credential for each provider key present: `CP OpenAI (auto)`, `CP Azure OpenAI (auto)` (needs the key and the endpoint), `CP Anthropic (auto)` and `CP Gemini (auto)`. It attaches the OpenAI credential to the flows' OpenAI model nodes.
5. In Flowise, creates `CP Langfuse (auto)` when both Langfuse keys are set, and turns on Langfuse analytics for every chatflow.
6. In Langflow, creates global variables of type Credential for `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` and `AZURE_OPENAI_API_KEY`.

Existing credentials and variables are never updated. To change a key later, edit it in the builder's UI, or delete it there and run `docker compose up builders-import`. [`integrations/README.md`](../integrations/README.md) lists the flow placeholders and shows how to run the seeder by hand.

### External-endpoint agents

| Agent | Endpoint | Token variable | Notes |
|---|---|---|---|
| DevHub Operations | `https://hub.{{DOMAIN}}/api/mcp` | `DEVHUB_MCP_TOKEN` | The [Dev Hub](https://github.com/alshawwaf/dev-hub) portal's MCP endpoint |
| PolicyPilot Access Automation (Pro) | `https://policypilot.{{DOMAIN}}/mcp/` | `PILOT_MCP_TOKEN` | Targets the external PolicyPilot portal, not the opt-in sidecar |
| PolicyPilot Dynamic Layers | `https://policypilot.{{DOMAIN}}/mcp/` | `PILOT_MCP_TOKEN` | Same as above |
| SCIM Provisioning | `https://idp.{{DOMAIN}}/scim/v2/Users` | `IDP_SCIM_TOKEN` | An HTTP tool, not MCP. Pairs with [SAML_IDP_Simulator](https://github.com/alshawwaf/SAML_IDP_Simulator) |

### Admin accounts

One identity, `N8N_ADMIN_EMAIL` and `N8N_ADMIN_PASSWORD`, signs in to four apps.

| App | Account | Created by |
|---|---|---|
| n8n | Owner | `n8n-provision` |
| Flowise | Admin | `builders-import`, on a fresh database |
| Langflow | Superuser | The `LANGFLOW_SUPERUSER` settings on `langflow` |
| Langfuse | Init user | The `LANGFUSE_INIT_USER_*` settings on `langfuse` |
| Open WebUI | Admin, from `OPEN_WEBUI_ADMIN_EMAIL` and `OPEN_WEBUI_ADMIN_PASSWORD` | `openwebui-provision` |

The Open WebUI variables are not in `.env-example`. When they are unset, `openwebui-provision` skips, and the first person to sign up becomes the Open WebUI admin. Open WebUI then turns public sign-up off.

Compose also sets `N8N_BASIC_AUTH_USER` and `N8N_BASIC_AUTH_PASSWORD` on n8n. The provisioner sends the same pair.

### Admin password

`N8N_ADMIN_PASSWORD` must pass the password checks of both n8n and Flowise:

- 8 to 64 characters.
- At least one uppercase letter, one lowercase letter and one digit. n8n requires the digit and the uppercase letter.
- At least one symbol. Flowise requires it, along with the lowercase letter. Use `-`, `_`, `.`, `!`, `@` or `%`.
- No `"`, `\`, `$`, `#`, `|`, `&` or spaces. `n8n-provision.sh` builds its JSON by plain string interpolation, Compose interpolates `$`, and `setup.sh` writes `.env` with `sed`.

A weaker password breaks the first deploy:

| Service | What happens |
|---|---|
| `n8n-provision` | n8n rejects the owner setup. The script retries 60 times, about 3 minutes, then exits 1 |
| `n8n-import` | Never starts, because it waits for `n8n-provision` to succeed. No workflows are imported, and `docker compose up -d` exits with an error |
| `builders-import` | Flowise rejects the admin registration, so no Flowise flows or credentials are seeded |
| n8n and Flowise | Both stay in first-visitor setup. Whoever opens them first creates the owner account |

Where the password comes from:

- **`setup.sh -y`** adds a fixed `Aa1-` prefix to a random hex string, so its password passes.
- **`.env-example`** ships `change_me`, which fails both checks. Replace it before the first start.
- **The lab installer** generates 24 random letters and digits. There is never a symbol, so Flowise always rejects it. About 1 in 70 also has no digit, which n8n rejects. Set your own `N8N_ADMIN_PASSWORD` in `answers.env`. The installer uses an `answers.env` value in place of the generated one, even though its template lists the key as generated.

Set the password before the first deploy. Each app keeps the password it was first created with. If you change it in `.env` later, the `n8n-provision` sign-in fails, and that blocks `n8n-import`. Change it in each app first, then in `.env`.

To confirm the Flowise admin exists, run `docker compose logs builders-import`. Look for `registered the stack admin` on the first run, or `admin login OK` on later runs.

### Code-first agent

[`integrations/code-agent/`](../integrations/code-agent/README.md) holds two stdlib-only scripts that use the same gateway:

- `mcp_gateway_client.py` does the raw MCP handshake, lists the tools and makes one Reputation Service call.
- `agent_loop.py` is an Anthropic Messages tool loop. It needs `ANTHROPIC_API_KEY`. `ANTHROPIC_MODEL` defaults to `claude-opus-4-8`.

There is no compose service. Run the scripts on the `demo` network:

```bash
cd integrations/code-agent
docker run --rm --network <project>_demo -v "$PWD":/app \
  -e MCP_GATEWAY_TOKEN -e ANTHROPIC_API_KEY \
  python:3.12-alpine python /app/agent_loop.py
```

`-e NAME` without a value passes the variable from your shell. Both scripts default to `http://mcp-gateway:8080/mcp` and the training token.

## Chat models and provider keys

Out of the box, every shipped agent needs a cloud model key. Only Open WebUI and AI-Infra-Guard default to local Ollama.

| Client | Default model | Path | Keys to set |
|---|---|---|---|
| n8n agents | Azure OpenAI `gpt-5.4-2026-03-05` | n8n, then LiteLLM, then Azure | `AZURE_OPENAI_API_KEY` and `AZURE_OPENAI_ENDPOINT` |
| Flowise flows | OpenAI `gpt-5.4` | Flowise, direct to OpenAI | `OPENAI_API_KEY` |
| Langflow flows | OpenAI `gpt-5.4` | Langflow, then LiteLLM, then OpenAI | `OPENAI_API_KEY` |
| Code-first agent | `claude-opus-4-8` | Direct to Anthropic | `ANTHROPIC_API_KEY` |
| Open WebUI | `gemma4:e2b` | Ollama | None |
| AI-Infra-Guard | `AIG_LLM_MODEL` | Ollama, OpenAI-compatible `/v1` | None, but the model must be pulled |

- **`AZURE_OPENAI_ENDPOINT` is missing from `.env-example`.** Add it, for example `https://<resource>.openai.azure.com`.
- **The Azure deployment name is fixed.** LiteLLM sends the n8n default to the Azure deployment named `gpt-5.4-2026-03-05`. Use that name, or edit [`litellm_config.yaml`](../integrations/observability/litellm_config.yaml).
- **Lakera Playground is the exception in n8n.** It runs on Gemini 2.5 Flash Lite and needs `GEMINI_API_KEY`.
- **Key-free n8n demo.** Wire each agent's unwired Ollama node instead. Those calls bypass LiteLLM, so they are not traced.

All four n8n model credentials point at LiteLLM and carry `LITELLM_MASTER_KEY`. The real provider keys go only to the `litellm` container, plus `builders-import` for Flowise and Langflow.

| n8n credential | Base URL | LiteLLM sends it to |
|---|---|---|
| Azure Open AI account | `http://litellm:4000` | Azure OpenAI, model `gpt-5.4-2026-03-05` |
| OpenAi account | `http://litellm:4000/v1` | OpenAI, for any other model name |
| Anthropic account | `http://litellm:4000` | Anthropic, for `claude-opus-4-8` and `anthropic/*` |
| Google Gemini(PaLM) Api account | `http://litellm:4000/gemini` | Google AI Studio, through LiteLLM's Gemini passthrough |
| Ollama account | `http://ollama-cpu:11434` | Local Ollama. Does not use LiteLLM |

After you change a provider key in `.env`, recreate the proxy with `docker compose up -d litellm`. For Flowise and Langflow, see the update rule in [What builders-import does](#what-builders-import-does).

## Observability

Langfuse v2 runs as one container on the stack Postgres, in a dedicated `langfuse` database created by `langfuse-db-init`. Open it at `https://trace.<domain>`.

- **Required secrets.** Langfuse will not start without `NEXTAUTH_SECRET` and `SALT`. Set `LANGFUSE_ENCRYPTION_KEY` too. All three are blank in `.env-example`.
- **Project keys.** `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` are blank too. Without them, nothing is traced.
- **Generate all five** with `python3 integrations/observability/gen_secrets.py --with-keys`. It prints ready-to-paste `.env` lines and writes nothing to disk.
- **Headless init.** On first boot, Langfuse creates the org `cp-playground` ("Check Point Playground") and the project `agents` ("Agents") with your key pair. It creates the init user from `N8N_ADMIN_EMAIL` and `N8N_ADMIN_PASSWORD`.
- **Telemetry** is off (`LANGFUSE_TELEMETRY_ENABLED=false`).
- **Database URL.** Leave `LANGFUSE_DATABASE_URL` blank to use the stack Postgres. Set a full URL only to use a separate server.

| Source | Trace path | Works when |
|---|---|---|
| Flowise | Native per-chatflow analytics, through the `CP Langfuse (auto)` credential | Both keys were set when `builders-import` ran |
| n8n | Model calls go through LiteLLM, which logs each call to Langfuse | The keys are set on `litellm` |
| Langflow | The same LiteLLM path | The keys are set on `litellm` |
| n8n Ollama node, Open WebUI, code-first agent, AI-Infra-Guard | Not traced | Not applicable |

Why LiteLLM: n8n and Langflow have no callback that works with the lean v2 server. Langflow 1.10 bundles the Langfuse v3 SDK, which needs a v3 server.

LiteLLM routing, from [`litellm_config.yaml`](../integrations/observability/litellm_config.yaml):

| Model name | Routed to | Upstream key |
|---|---|---|
| `gpt-5.4-2026-03-05` | `azure/gpt-5.4-2026-03-05`, API version `2025-04-01-preview` | `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT` |
| `claude-opus-4-8`, `anthropic/*` | Anthropic | `ANTHROPIC_API_KEY` |
| `*` (any other name) | `openai/*` | `OPENAI_API_KEY` |
| Gemini requests | The built-in `/gemini` passthrough | `GEMINI_API_KEY` |

Success and failure callbacks both go to Langfuse. `drop_params: true` drops parameters that a provider rejects. Clients authenticate with `LITELLM_MASTER_KEY`.

Guide: [Observability with Langfuse](guides/Observability_Langfuse.md).

## RAG, evals, and labs

### Visible RAG

- `qdrant` runs `v1.12.4` with telemetry off and no API key. It is reachable only on the `demo` network.
- `rag-ingest` embeds the 8 Markdown snippets in [`integrations/rag-cp-docs/corpus/`](../integrations/rag-cp-docs/corpus/) into the `cp_docs` collection, using `nomic-embed-text`. It pulls the model if it is missing. Re-run it with `docker compose up rag-ingest`.
- In n8n, `rag-cp-docs-agent.json` calls the `rag-cp-docs-retriever.json` sub-workflow. The sub-workflow must be active, and `n8n-import` activates it.
- The corpus is paraphrased demo text, not official Check Point documentation.
- To require a Qdrant key, set `QDRANT_API_KEY` and uncomment the `QDRANT__SERVICE__API_KEY` line in compose. Never pass an empty value: Qdrant reads that as "auth on, empty key".

Guide: [Visible RAG](guides/Visible_RAG.md).

### Evals

- [`run_evals.py`](../integrations/evals/run_evals.py) posts the 8 cases in [`evals_cases.json`](../integrations/evals/evals_cases.json) to n8n chat webhooks. It scores each reply by substring.
- It writes `evals_report.md` and `evals_report.json` to `./n8n/shared`. It exits 0 only if every case passes.
- Most cases need live Check Point credentials. The DevHub and PolicyPilot cases need those external services. The guarded-chat injection case needs `LAKERA_API_KEY`.
- `EVALS_BASE_URL` (default `http://n8n:5678`) points the run at another n8n.
- The Nightly Self-QA workflow in n8n complements the harness with a daily ping of every agent.

Guide: [Evals Harness](guides/Evals_Harness.md).

### Guardrails

- `Lakera-Playground.json` screens the prompt with Lakera Guard before the model runs, and screens the answer after. When Guard flags either one, a second model explains the block. Both of its Gemini nodes use `models/gemini-2.5-flash-lite`, including the one labelled "Gemini 2.5 Flash". It also needs `GEMINI_API_KEY`.
- `guarded-chat.json` applies the same input and output screening to an agent that can call gateway tools. It uses the default Azure model.
- Both need `LAKERA_API_KEY`. The key is project-scoped, so the guard workflows do not send `LAKERA_PROJECT_ID`.

Guide: [Lakera Playground](guides/n8n_Lakera_Playground_Guide.md).

### Build Your Own MCP Server (`exercises`)

- `ips-cve-mcp` is a stdlib-only Streamable HTTP server with two IPS and CVE tools. It listens on port 3013 on the `demo` network.
- It needs `IPS_CLIENT_ID` and `IPS_ACCESS_KEY`. The optional `IPS_AUTH_URL` and `IPS_SERVICE_URL` must point at the same region.
- The published image ships the **solution**. The [Dockerfile](../exercises/build-your-own-mcp/Dockerfile) copies `solution/ips_cve_mcp.py`. To run your own work, point it at `scaffold/`, build the image yourself and start it without a re-pull:

```bash
docker build -t ghcr.io/alshawwaf/cp-agentic-ips-cve-mcp:latest exercises/build-your-own-mcp
docker compose --profile exercises up -d --pull never ips-cve-mcp
```

The `--build` flag in the compose comment does nothing while the `build:` block is commented out.

### MCP Security Lab (`security-lab`)

- `vuln-mcp` runs [`vuln_mcp_server.py`](../integrations/mcp-security-lab/vuln_mcp_server.py) on `python:3.12-alpine`. It listens on port 3099 with no auth.
- It simulates four attack classes: tool poisoning, indirect prompt injection, an over-permissioned tool and a rug pull. Nothing is real: there are no file reads and no exfiltration.
- Detect the attacks with an AI-Infra-Guard MCP scan. Defend with the Lakera-guarded agent, gateway auth and least privilege.
- Keep the profile off outside the exercise.

### PolicyPilot sidecar (`policypilot`)

- It runs `${POLICYPILOT_IMAGE:-policypilot:custom}`. That image is not published. Build it from [alshawwaf/PolicyPilot](https://github.com/alshawwaf/PolicyPilot).
- It needs `PILOT_MCP_TOKEN`, and the server exits without it. It also needs `PILOT_ENCRYPTION_KEY`, matching the key the PolicyPilot portal used. Share the portal's database through `PILOT_DATABASE_URL` to see populated servers.
- It listens on port 3020. It is not added to the gateway automatically. Register it as in [Adding a server to the gateway](#adding-a-server-to-the-gateway).
- The shipped PolicyPilot agents target the external portal at `policypilot.<domain>`, not this sidecar.

Guide: [PolicyPilot behind the Gateway](guides/PolicyPilot_Gateway_Sidecar_Guide.md). The [Capstone](guides/Capstone_Zero_Trust_Onboarding.md) chains SCIM, PolicyPilot and Lakera into one Zero Trust onboarding scenario.

### AI-Infra-Guard

- `aig-webserver` serves the UI on port 8088, routed as `aig.<domain>`. `aig-agent` runs the scans and connects to it.
- The agent's model defaults to local Ollama: `AIG_LLM_BASE_URL=http://ollama-cpu:11434/v1`, with API key `ollama`.
- `.env-example` sets `AIG_LLM_MODEL=huihui_ai/deepseek-r1-abliterated:8b`. The compose fallback is the `:latest` tag. Neither is in the default `OLLAMA_MODELS`, so pull the model first.
- Compose mounts a patched `aig/patches/llm.py` into the agent.
- `aig-agent` runs with `cap_add: SYS_ADMIN`, `seccomp=unconfined` and a 2 GB `/dev/shm`.

## Ollama models

The puller installs the comma-separated `OLLAMA_MODELS` on every start.

| Setting | Value |
|---|---|
| `.env-example` default | `gemma4:e2b,qwen3.5:4b,nomic-embed-text` |
| Compose fallback when unset | `gemma4:e2b,qwen3.5:4b` |
| Retries | Up to 5 per model, 15 s apart |
| `OLLAMA_MAX_LOADED_MODELS` | 2 by default |
| `OLLAMA_KEEP_ALIVE` | `-1` in `.env-example`, so models stay loaded |

| Model | Used by |
|---|---|
| `gemma4:e2b` | The Open WebUI default (`OPEN_WEBUI_DEFAULT_MODELS`) |
| `qwen3.5:4b` | The unwired Ollama node on each n8n agent |
| `nomic-embed-text` | RAG embeddings, in `rag-ingest` and the retriever |
| `huihui_ai/deepseek-r1-abliterated:8b` | The AI-Infra-Guard default. Not pulled by default |

`.env-example` also lists a heavier full-demo set for GPU hosts: `gemma4:e2b,qwen3.5:9b,richardyoung/mythos-9b-unhinged-abliterated:latest,huihui_ai/deepseek-r1-abliterated:8b,qwen3-embedding:0.6b`. That set leaves out `qwen3.5:4b` and `nomic-embed-text`, which the n8n Ollama nodes and the RAG demo use. Add them back if you switch.

Manage models inside the container:

```bash
docker compose exec ollama-cpu ollama list
docker compose exec ollama-cpu ollama pull huihui_ai/deepseek-r1-abliterated:8b
docker compose exec ollama-cpu ollama rm <model>
```

## Configuration

[`.env-example`](../.env-example) is the template. Compose reads `.env` from the project directory. Every non-empty secret in `.env-example` is a placeholder or a training value.

### setup.sh

- Accepts `--non-interactive`, `--ci` or `-y`.
- Checks for `docker` and `docker compose`.
- Copies `.env-example` to `.env`. It asks before overwriting and keeps `.env.bak`. In non-interactive mode it never overwrites.
- Offers to replace 5 values with `openssl rand -hex 16`: `POSTGRES_PASSWORD`, `N8N_ENCRYPTION_KEY`, `N8N_USER_MANAGEMENT_JWT_SECRET`, `N8N_ADMIN_PASSWORD` and `N8N_BASIC_AUTH_PASSWORD`. The admin password gets a fixed `Aa1-` prefix, so it meets the [admin password rule](#admin-password). The interactive default is No, which keeps the `change_me` training values. Non-interactive mode says Yes.
- Can prompt for `MANAGEMENT_HOST` and `MANAGEMENT_API_KEY`.
- Copies `credentials_public/` to the unused `n8n/backup/credentials/` and asks you to edit `CHANGE_ME` values. Those values no longer exist, so skip that step.
- Prints `docker compose --profile cpu up -d`. That is the same as `docker compose up -d`.
- Uses GNU `sed -i` syntax. BSD `sed` on macOS rejects it, so the script stops at the secret step there. On macOS, run `cp .env-example .env` instead. Then replace the same 5 values by hand, with an `N8N_ADMIN_PASSWORD` that meets the [admin password rule](#admin-password).

`setup.sh` leaves these alone: the Langfuse secrets and keys, `LITELLM_MASTER_KEY`, `MCP_GATEWAY_TOKEN`, the provider keys, `AZURE_OPENAI_ENDPOINT`, `OPEN_WEBUI_ADMIN_*` and `DOMAIN`.

### Core and n8n

| Variable | Used by | Notes |
|---|---|---|
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | `postgres`, the n8n services, the DB init jobs, Langfuse, Flowise | `POSTGRES_DB` is the n8n database (`n8n`). The `langfuse` and `flowise` databases sit next to it |
| `N8N_ENCRYPTION_KEY` | `n8n`, `n8n-import` | Encrypts n8n credentials. Never change it after first boot |
| `N8N_USER_MANAGEMENT_JWT_SECRET` | `n8n` | Signs n8n sessions |
| `N8N_ADMIN_EMAIL`, `N8N_ADMIN_PASSWORD`, `N8N_ADMIN_FIRST_NAME`, `N8N_ADMIN_LAST_NAME` | n8n, Flowise, Langflow, Langfuse | The one admin login. See [Admin accounts](#admin-accounts) |
| `N8N_BASIC_AUTH_USER`, `N8N_BASIC_AUTH_PASSWORD` | `n8n`, `n8n-provision`, `n8n-import` | Must match across all three |
| `WEBHOOK_URL`, `N8N_EDITOR_BASE_URL` | `n8n` | Public base URLs. `http://localhost:5678/` in `.env-example`. Compose passes `WEBHOOK_URL` to n8n as both `WEBHOOK_URL` and `N8N_WEBHOOK_URL`, so set only `WEBHOOK_URL`. Setting `N8N_WEBHOOK_URL` in `.env` has no effect. n8n 2.40 logs a harmless deprecation warning for `WEBHOOK_URL` |
| `N8N_PUSH_BACKEND` | `n8n` | `websocket` |
| `N8N_HOST` | `n8n-import`, `builders-import` | Only used to derive the domain when `DOMAIN` is blank |
| `DOMAIN` | Traefik labels, Langfuse `NEXTAUTH_URL`, both importers | Base domain. Leave blank for local runs |
| `COMPOSE_PROFILES` | Compose | `.env-example` sets `cpu`, which matches nothing |

`scripts/validate-env.sh` treats 9 of these as required: `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `N8N_ENCRYPTION_KEY`, `N8N_USER_MANAGEMENT_JWT_SECRET`, `N8N_ADMIN_EMAIL`, `N8N_ADMIN_PASSWORD`, `N8N_BASIC_AUTH_USER` and `N8N_BASIC_AUTH_PASSWORD`.

### Langfuse and LiteLLM

| Variable | Used by | Notes |
|---|---|---|
| `NEXTAUTH_SECRET`, `SALT` | `langfuse` | Required. Blank in `.env-example` |
| `LANGFUSE_ENCRYPTION_KEY` | `langfuse` | 256-bit hex. Blank in `.env-example` |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | `langfuse`, `litellm`, `langflow`, `builders-import` | Blank means nothing is traced |
| `LANGFUSE_DATABASE_URL` | `langfuse` | Leave blank |
| `LANGFUSE_TELEMETRY_ENABLED` | `langfuse` | `false` |
| `LANGFUSE_HOST` | `litellm`, `builders-import` | Defaults to `http://langfuse:3000`. Not in `.env-example` |
| `LITELLM_MASTER_KEY` | `litellm`, `n8n-import`, `builders-import` | Blank falls back to the public `sk-cp-litellm-training-key`. Override it |

### Model providers

| Variable | Used by | Notes |
|---|---|---|
| `AZURE_OPENAI_API_KEY` | `litellm`, `builders-import` | The n8n default model |
| `AZURE_OPENAI_ENDPOINT` | `litellm`, `builders-import` | Required for the n8n default. Missing from `.env-example` |
| `AZURE_OPENAI_DEPLOYMENT` | `builders-import` | The Flowise Azure credential. Defaults to `gpt-5.4-2026-03-05`. Not in `.env-example` |
| `OPENAI_API_KEY` | `litellm`, `builders-import` | The Flowise and Langflow defaults, and the n8n OpenAI node |
| `ANTHROPIC_API_KEY` | `litellm`, `builders-import`, code-first agent | The n8n Claude node and the code-first agent |
| `GEMINI_API_KEY` | `litellm`, `builders-import` | The n8n Gemini node |
| `ANTHROPIC_MODEL` | Code-first agent only | Compose ignores it. Defaults to `claude-opus-4-8` |

### MCP servers and gateway

Set only the variables for servers you use.

| Variable | Used by | Notes |
|---|---|---|
| `MCP_GATEWAY_TOKEN` | `mcp-gateway`, both importers, `health-check.sh`, code-first agent | Defaults to the public `cp-mcp-gateway-training-token`. Override it |
| `MANAGEMENT_HOST`, `MANAGEMENT_API_KEY` | 9 sidecars | HTTPS Inspection, Management, Management Logs, Threat Prevention, Gateway CLI, Gateway Connection Analysis, Gaia, CPInfo Analysis, Policy Insights |
| `DOC_CLIENT_ID`, `DOC_SECRET_KEY`, `DOC_REGION` | Documentation | An API client from the Check Point cloud portal. `DOC_REGION` is `EU`, `US`, `STG` or `Local` |
| `TE_API_KEY` | Threat Emulation | |
| `REPUTATION_API_KEY` | Reputation Service | |
| `GAIA_GATEWAY_IP`, `GAIA_GATEWAY_PORT`, `GAIA_USERNAME`, `GAIA_PASSWORD` | Gaia | A gateway's Gaia API. The port defaults to 443 |
| `SPARK_MGMT_CLIENT_ID`, `SPARK_MGMT_SECRET_KEY`, `SPARK_MGMT_REGION`, `SPARK_MGMT_INFINITY_PORTAL_URL` | Spark Management | |
| `HARMONY_SASE_API_KEY`, `HARMONY_SASE_MANAGEMENT_HOST`, `HARMONY_SASE_REGION` | SASE | |
| `CPINFO_LOG_LEVEL` | CPInfo Analysis | `info` in `.env-example` |

### Integrations

| Variable | Used by | Notes |
|---|---|---|
| `LAKERA_API_KEY` | `n8n-import` | The guardrail agents |
| `LAKERA_PROJECT_ID` | `n8n-import` | Optional. The guard workflows do not send it |
| `IDP_SCIM_TOKEN` | Both importers | The SCIM agent |
| `DEVHUB_MCP_TOKEN` | Both importers | Blank skips the DevHub credential |
| `PILOT_MCP_TOKEN` | Both importers, `policypilot-mcp` | Blank skips the PolicyPilot credential |
| `QDRANT_API_KEY` | `n8n-import`, `rag-ingest` | Leave blank. See [Visible RAG](#visible-rag) |

### Builders, Ollama and labs

| Variable | Used by | Notes |
|---|---|---|
| `FLOWISE_PORT` | `flowise`, `builders-import` | Required. `3001` in `.env-example`. Flowise has no fallback, while `builders-import` falls back to `3020` |
| `FLOWISE_DATABASE_TYPE` | `flowise` | `sqlite` (default) or `postgres`. Commented out in `.env-example`. Switch only on a fresh deploy |
| `FLOWISE_API_KEY`, `LANGFLOW_API_KEY` | `builders-import` | Override the admin sign-in. Not in `.env-example` |
| `LANGFLOW_AUTO_LOGIN` | `langflow` | `false` by default. `true` removes the login |
| `OPEN_WEBUI_DEFAULT_MODELS` | `open-webui` | Seeds the setting on first boot only |
| `OPEN_WEBUI_ADMIN_EMAIL`, `OPEN_WEBUI_ADMIN_PASSWORD` | `openwebui-provision` | Not in `.env-example`. Set them before first boot |
| `OLLAMA_MODELS`, `OLLAMA_MAX_LOADED_MODELS`, `OLLAMA_KEEP_ALIVE` | The Ollama services | See [Ollama models](#ollama-models) |
| `AIG_LLM_API_KEY`, `AIG_LLM_BASE_URL`, `AIG_LLM_MODEL` | `aig-agent` | Default to local Ollama |
| `IPS_CLIENT_ID`, `IPS_ACCESS_KEY`, `IPS_AUTH_URL`, `IPS_SERVICE_URL` | `ips-cve-mcp` | `exercises` profile |
| `POLICYPILOT_IMAGE`, `PILOT_ENCRYPTION_KEY`, `PILOT_DATABASE_URL` | `policypilot-mcp` | `policypilot` profile |
| `EVALS_BASE_URL` | `evals-run` | Defaults to `http://n8n:5678`. Commented out in `.env-example` |

### Not read by compose

`.env-example` also sets `DEPLOY_FLOWISE`, `DEPLOY_LANGFLOW`, `POSTGRES_PORT`, `N8N_PORT`, `OLLAMA_PORT`, `OLLAMA_HOST`, `LANGFLOW_PORT`, `OPEN_WEBUI_PORT` and `AIG_PORT`. `docker-compose.yml` reads none of them. Flowise and Langflow always run, and no ports are published.

## Data volumes

Compose prefixes each named volume with the project name, for example `cp-agentic-mcp-playground_n8n_storage`.

| Volume | Mounted by | Holds |
|---|---|---|
| `n8n_storage` | `n8n`, `n8n-import` | n8n settings and local files |
| `postgres_storage` | `postgres` | The `n8n`, `langfuse` and `flowise` databases |
| `ollama_storage` | The Ollama services and pullers | Downloaded models |
| `open-webui` | `open-webui` | Open WebUI users, chats, settings and Functions |
| `qdrant_storage` | `qdrant` | RAG vectors |
| `aig_data`, `aig_db`, `aig_logs`, `aig_uploads` | `aig-webserver` | AI-Infra-Guard state |
| `policypilot_data` | `policypilot-mcp` | The PolicyPilot database (`policypilot` profile) |
| `flowise`, `langflow`, `vllm_storage` | Nothing | Declared but unused |

Bind mounts, relative to the repo root:

| Host path | Mounted into | Notes |
|---|---|---|
| `./flowise_data` | `flowise` at `/root/.flowise` | The Flowise SQLite database and credential encryption key. Created at runtime. Not in `.gitignore` |
| `./langflow/flows` | `langflow` at `/app/flows` | Only the flows folder. Langflow's own database stays inside the container and is lost when the container is recreated. `builders-import` re-seeds the flows |
| `./n8n/backup` | `n8n`, `n8n-import` at `/backup` | The committed workflows and credential templates |
| `./n8n/shared` | `n8n`, `threat-emulation-mcp`, `cpinfo-analysis-mcp`, `evals-run` | The file drop and eval reports |
| `./n8n/custom-nodes` | `n8n`, `n8n-import` at `/home/node/.n8n/custom` | Custom n8n nodes |
| `./integrations` and sub-paths | `builders-import`, `evals-run`, `rag-ingest`, `vuln-mcp`, `litellm` | Read-only |
| `./mcp-gateway/catalog.yaml` | `mcp-gateway` | Read-only |
| `./scripts` | The provisioners | Read-only |
| `./aig/patches/llm.py` | `aig-agent` | Read-only patch |
| `/var/run/docker.sock` | `mcp-gateway` | Docker API access |

### Full reset

```bash
docker compose down -v      # removes the containers and every named volume
rm -rf ./flowise_data       # down -v keeps bind mounts; may need sudo
docker compose up -d
```

This deletes all workflows, credentials, chats, traces and models. Back up first.

### Backups

[`scripts/backup-volumes.sh`](../scripts/backup-volumes.sh) and `scripts/restore-volumes.sh` handle volume backups. The [Backup and Recovery Guide](operations/BACKUP_RECOVERY.md) covers schedules and recovery.

- The default list is `n8n_storage`, `postgres_storage`, `ollama_storage`, `qdrant_storage`, `open-webui`, `flowise` and `langflow`.
- The script looks those names up without the project prefix. Compose volumes carry the prefix, so the defaults are skipped. Pass the real names instead:

```bash
docker volume ls | grep -E '_(n8n_storage|postgres_storage|open-webui|qdrant_storage)$'
./scripts/backup-volumes.sh --volumes <project>_n8n_storage,<project>_postgres_storage,<project>_open-webui
```

- The defaults include the unused `flowise` and `langflow` volumes. They skip the `aig_*` volumes and `./flowise_data`. Archive `./flowise_data` separately, for example with `tar`.

## Development

### Common commands

```bash
docker compose config --quiet                 # validate the compose file
./scripts/validate-env.sh                     # check the 9 required .env variables
./scripts/health-check.sh --verbose           # in-network probes and gateway tool count
python3 scripts/check_workflow_uniqueness.py  # workflow, webhook and node ids must be unique
docker compose logs -f n8n-import             # follow an importer
docker compose up n8n-import                  # re-import the n8n agents
docker compose up builders-import             # re-seed Flowise and Langflow
docker compose up rag-ingest                  # re-embed the RAG corpus
docker compose up evals-run                   # re-run the evals
```

### Build the custom image locally

The `build:` block for `n8n` is commented out, and the service uses `pull_policy: always`. Build with plain Docker, then stop Compose from re-pulling:

```bash
docker compose pull                           # fetch every other image first
docker build -t ghcr.io/alshawwaf/cp-agentic-n8n:latest -f docker/n8n/Dockerfile docker/n8n
docker compose up -d --pull never             # keep the local :latest
```

A plain `docker compose up` afterwards replaces your build with the GHCR image. `docker compose build n8n`, `update.sh` and `update.ps1` build nothing while the `build:` block stays commented out.

In the Dockerfile, each `wrap` line maps a CLI name in `/usr/local/bin` to its npm binary. A new server needs a `wrap` line, a sidecar service in compose and, optionally, a gateway entry.

### Upgrading n8n and Open WebUI

The current pins are n8n 2.40.7 (previously 2.28.6) and Open WebUI v0.11.4 (previously a `main` build of v0.10.2). These notes apply to hosts that ran the older pins.

n8n:

- Bump the `FROM` line in `docker/n8n/Dockerfile`. To keep things tidy, bump the unused `x-n8n` anchor image too. `n8n-import` and the sidecars run the `n8n` image, so nothing else changes.
- Hosts get the new version only after `publish-image.yml` rebuilds `:latest`. Let that run finish before you redeploy, then check with `docker compose exec n8n n8n --version`.
- A local build must be tagged `ghcr.io/alshawwaf/cp-agentic-n8n:latest`, so `n8n-import` and the sidecars use it too. See [Build the custom image locally](#build-the-custom-image-locally).
- Once the running image is 2.40 or later, you can drop the `WEBHOOK_URL` line from the `x-n8n` anchor. Keep `N8N_WEBHOOK_URL`.
- The runtime moves from Node 24 to Node 26, for n8n and all 14 sidecars. Smoke-test each sidecar with one tool call.
- MCP Client Tool behaviour changed between 2.29 and 2.33, including session reuse and argument shape. Re-test direct and gateway agents, then re-run the evals.
- The AI Assistant module is on by default since 2.35. Set `N8N_DISABLED_MODULES=instance-ai` on n8n to hide it.

Open WebUI:

- The 0.11 database migrations are one-way. Back up the `open-webui` volume first. Rolling back means restoring that backup.
- A new unique index fails if two users' emails differ only by case, and a failed migration stops startup. Check for such users before you upgrade.
- The n8n pipe bind mount is gone. It pointed at `open-webui/pipes/n8n_pipe.py`, a file that was never in this repo. No n8n pipe ships here, so out of the box Open WebUI chats only with the local Ollama models. To reach the n8n agents from it, add your own pipe Function. Open WebUI keeps Functions in its database. Add them in the UI or through `POST /api/v1/functions/create`.
- `WEBUI_SECRET_KEY` is not set, so recreating the container signs out every session.
- Admin settings now live inside the Settings window.
- `OLLAMA_BASE_URL`, `DEFAULT_MODELS` and `ENABLE_SIGNUP` seed the database on first boot only. After that, change them in the UI.

### Scripts

| Script | Purpose |
|---|---|
| `scripts/health-check.sh` | Takes `--profile <name>` and `--verbose`. Probes containers with `docker exec` and counts gateway tools over a real MCP handshake. Does not check `policy-insights-mcp`, Langfuse, LiteLLM, Qdrant or AI-Infra-Guard |
| `scripts/validate-env.sh` | Checks the 9 required variables in `.env` |
| `scripts/backup-volumes.sh`, `scripts/restore-volumes.sh` | Volume backup and restore. See [Backups](#backups) |
| `scripts/n8n-provision.sh`, `scripts/openwebui-provision.sh` | Run by the provisioner one-shots |
| `scripts/check_workflow_uniqueness.py` | Fails if workflow ids, webhook ids or node ids repeat |
| `scripts/convert_guides_to_docx.py` | Exports the guides to `.docx`. Needs `python-docx` |

More usage detail is in [`scripts/README.md`](../scripts/README.md).

### Tests

[`tests/integration-test.sh`](../tests/integration-test.sh) probes `localhost:5678`, `:3000`, `:7860`, `:3001`, `:6333` and the MCP ports on the host. The stack publishes no host ports, so the script fails against a stock stack. It also starts the stack with `--profile cpu`. Use `scripts/health-check.sh` instead.

### CI

| Workflow | Runs on | What it does |
|---|---|---|
| [`ci.yml`](../.github/workflows/ci.yml), job `validate` | Push to `main` or `develop`, pull requests to `main`, manual | `setup.sh --non-interactive`, `docker compose config --quiet`, then `docker build -f docker/n8n/Dockerfile docker/n8n` |
| `ci.yml`, job `live-stack` | Manual only | Starts the stack, waits 90 s, runs `health-check.sh --verbose` |
| [`security-scan.yml`](../.github/workflows/security-scan.yml) | Mondays at 09:00 UTC, pull requests to `main`, manual | Builds the custom image and scans it with Trivy |
| [`publish-image.yml`](../.github/workflows/publish-image.yml) | Push to `main`, manual | Builds and pushes the 4 GHCR images |

CI never runs `tests/integration-test.sh`.

### Leftover files

| Path | Status |
|---|---|
| `implementation_plan.md` | A vLLM CPU plan. Compose has no vLLM service, and `vllm_storage` is unused |
| `quadrant/backup/config.yaml` | A legacy Qdrant directory |
| `update.sh`, `update.ps1` | Run `docker compose build --pull --no-cache n8n`, which builds nothing. Use `git pull`, `docker compose pull` and `docker compose up -d` instead |
| `assets/n8n-tool-workflows/` | Four unrelated Slack, Google Docs and Postgres tool workflows. Nothing imports them |
| `assets/n8n-demo.gif` | A copy of the demo GIF from n8n's self-hosted AI starter kit (Apache-2.0). It does not show this stack |

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `network dokploy-network declared as external, but could not be found` | You are on a plain Docker host. Run `docker network create dokploy-network` |
| `no matching manifest for linux/arm64/v8` | The prebuilt images are amd64 only. Turn on Rosetta in Docker Desktop, run `export DOCKER_DEFAULT_PLATFORM=linux/amd64`, then retry. See [Running on a plain Docker host](#running-on-a-plain-docker-host) |
| `docker compose up -d` stops with `service "n8n-provision" didn't complete successfully: exit 1` | n8n rejected `N8N_ADMIN_PASSWORD`, so `n8n-import` never ran. Fix the password as in [Admin password](#admin-password), then run `docker compose up -d` again. If the owner already exists, the password in `.env` no longer matches it |
| `builders-import` logs `Flowise login failed`, and Flowise has no flows | Flowise rejected `N8N_ADMIN_PASSWORD`, usually for a missing symbol. See [Admin password](#admin-password), then run `docker compose up builders-import` |
| `langfuse` keeps restarting and logs invalid environment variables | `NEXTAUTH_SECRET` or `SALT` is blank. Run `python3 integrations/observability/gen_secrets.py --with-keys`, put the lines in `.env`, then run `docker compose up -d langfuse` |
| Nothing appears in Langfuse | The project keys are blank, or the agent uses Ollama. Set `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` (or create a pair in the Langfuse UI), run `docker compose up -d langfuse litellm`, then `docker compose up builders-import` |
| An agent replies with a model, deployment or authentication error | No cloud key reached the model. n8n needs `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT` and a deployment named `gpt-5.4-2026-03-05`. Flowise and Langflow need `OPENAI_API_KEY`. Recreate `litellm` after you edit `.env` |
| A chat URL returns 404 after the first deploy | The workflows were activated after n8n started. Wait until `docker compose ps -a n8n-import` shows `Exited (0)`, then run `docker compose restart n8n` |
| n8n logs a deprecation warning for `WEBHOOK_URL` | Harmless. Compose sets both `WEBHOOK_URL` and `N8N_WEBHOOK_URL` from the same value. See [Core and n8n](#core-and-n8n) |
| `n8n-import` fails with "Mismatching encryption keys" | `N8N_ENCRYPTION_KEY` differs from the key stored in `n8n_storage`. Restore the original key, or do a [full reset](#full-reset). `n8n-provision` does not use the key |
| The gateway exposes 0 tools, or one server's tools are missing | That server was not listening when the gateway started, and only 3 sidecars gate its start. Look for "Can't start" in `docker compose logs mcp-gateway`, then run `docker compose restart mcp-gateway` |
| An agent cannot reach its MCP server | Use the service name, not `localhost`. Test with `docker compose exec n8n nc -z -w 3 mcp-documentation 3000 && echo open`, or run `./scripts/health-check.sh --verbose` |
| Gaia tools fail | Set `GAIA_GATEWAY_IP`, `GAIA_USERNAME` and `GAIA_PASSWORD`. Clients must use `http://quantum-gaia-mcp:3011/mcp` |
| Management-backed tools fail or time out | `MANAGEMENT_HOST` is not reachable from the Docker host. In CloudShare, use the public IP and fix the return route |
| An old workflow shows `@chkp/... ERR_MODULE_NOT_FOUND` | It uses the retired community MCP node. Replace that node with the native MCP Client Tool node |
| A local n8n build disappears after `docker compose up` | `pull_policy: always` re-pulled the GHCR image. Use `docker compose up -d --pull never` |
| `builders-import` cannot reach Flowise | `FLOWISE_PORT` is unset. Set it to `3001` and recreate `flowise` |
| The Open WebUI admin is whoever signed up first | `OPEN_WEBUI_ADMIN_EMAIL` and `OPEN_WEBUI_ADMIN_PASSWORD` were unset on first boot. Set them before the first start. On an existing volume, manage users in Open WebUI's admin settings |
| Changing `OPEN_WEBUI_DEFAULT_MODELS` has no effect | The value seeds the database on first boot only. Change the default model in Open WebUI's settings |
| AI-Infra-Guard scans fail with a model error | Its model is not pulled. Run `docker compose exec ollama-cpu ollama pull huihui_ai/deepseek-r1-abliterated:8b`, or add it to `OLLAMA_MODELS` |
| The GPU is not used | `gpu-nvidia` runs next to CPU Ollama, and clients still call `ollama-cpu`. Check the host with `docker run --rm --gpus all nvidia/cuda:12.3.2-base-ubuntu22.04 nvidia-smi` |
| `setup.sh` fails at `sed` on macOS | The script uses GNU `sed -i`. Edit `.env` by hand, or run the script on Linux |
| `tests/integration-test.sh` fails every probe | It expects host ports. Use `./scripts/health-check.sh` |
| The backup script skips every volume | Pass the project-prefixed volume names with `--volumes`. See [Backups](#backups) |
| A provisioner logs HTTP 400 or 403 | This is normal on re-runs. The one-shots are idempotent |
| `evals-run` exits 1 | At least one case failed. Read `./n8n/shared/evals_report.md` |

## Security notes

This stack is built for lab and demo use. Before wider exposure, apply the [Production Deployment Guide](operations/PRODUCTION_DEPLOYMENT.md). Parts of that guide are out of date.

- **Chat endpoints are open.** 31 of the 32 n8n chat triggers are public with no authentication, and `n8n-import` activates every workflow. On a routed `n8n.<domain>`, anyone can reach each agent's `/webhook/<id>/chat` URL without signing in. That includes the SCIM provisioning agent, the PolicyPilot write agents and the security-lab agent. Only `Lakera-Playground` is not public. Keep n8n unrouted, add chat-trigger authentication, or deactivate agents you don't need.
- **Training defaults are public.** `LITELLM_MASTER_KEY` falls back to `sk-cp-litellm-training-key` and `MCP_GATEWAY_TOKEN` to `cp-mcp-gateway-training-token`. `setup.sh` changes neither. Set random values, for example with `openssl rand -hex 32`.
- **Keep secrets out of git.** Never commit `.env` or `.env.bak`. `./flowise_data` holds the Flowise database and its credential encryption key, and `.gitignore` does not cover it. Committed credential and workflow files carry placeholders only.
- **Open WebUI sign-up.** Until the first account exists, anyone who reaches `chat.<domain>` can sign up and become admin. Set `OPEN_WEBUI_ADMIN_EMAIL` and `OPEN_WEBUI_ADMIN_PASSWORD` so `openwebui-provision` claims the account during deploy. Open WebUI turns public sign-up off after the first account.
- **The Flowise SSRF guard is off.** `HTTP_SECURITY_CHECK=false` lets Flowise reach the gateway on the private network. It also lets any flow call any private address.
- **Docker socket.** `mcp-gateway` mounts `/var/run/docker.sock`, which gives it root-level control of the Docker host.
- **AI-Infra-Guard privileges.** `aig-agent` runs with `SYS_ADMIN` and `seccomp=unconfined`.
- **Langflow.** It is pinned to `1.10.1` because of CVE-2026-33017, an unauthenticated remote code execution flaw fixed in 1.9.0. Bump it deliberately, and never float on `latest`. Keep `LANGFLOW_AUTO_LOGIN=false` on any routed host.
- **n8n cookies.** `N8N_SECURE_COOKIE=false` lets the provisioner sign in over plain HTTP inside the network. As a result, the session cookie is not marked Secure.
- **Unauthenticated internals.** Qdrant and the MCP sidecars have no auth and rely on the private `demo` network. The `security-lab` server is intentionally vulnerable. Keep that profile off outside the exercise.
- **Local ports.** If you publish ports with an override file, bind them to `127.0.0.1`.

## Guides index

The guides live in [`docs/guides/`](guides/). 13 of them also ship as `.docx` exports. The "Known gaps" column lists content that no longer matches the stack.

| Guide | Covers | Known gaps |
|---|---|---|
| [MCP Gateway, Explained](guides/MCP_Gateway_Explained.md) | Gateway concepts, catalog, token and a curl handshake | Says 10 servers and about 180 tools. The catalog now has 11 servers |
| [MCP Gateway Agent Guide](guides/MCP_Gateway_Agent_Guide.md) | Direct versus gateway lab, CloudShare connectivity | Old credential names and legacy nodes |
| Per-server guides: [Management](guides/Quantum_Management_MCP_Agent_Guide.md), [Gaia](guides/Quantum_Gaia_MCP_Agent_Guide.md), [Gateway CLI](guides/Quantum_Gateway_CLI_MCP_Agent_Guide.md), [Management Logs](guides/Management_Logs_MCP_Agent_Guide.md), [Threat Prevention](guides/Threat_Prevention_MCP_Agent_Guide.md), [Threat Emulation](guides/Threat_Emulation_MCP_Agent_Guide.md), [Reputation Service](guides/Reputation_Service_MCP_Agent_Guide.md), [HTTPS Inspection](guides/HTTPS_Inspection_MCP_Agent_Guide.md), [CPInfo Analysis](guides/CPInfo_Analysis_MCP_Agent_Guide.md), [Documentation](guides/Documentation_MCP_Agent_Guide.md) | Using each direct agent | Several describe the legacy tool nodes and Postgres memory. There are no guides yet for Policy Insights, Spark Management, SASE or Gateway Connection Analysis |
| [Threat Prevention deep dive](guides/CheckPoint_Threat_Prevention_Guide.md) | An older screenshot walkthrough | Duplicates the Threat Prevention guide. 4 of its 5 images are missing |
| [Screenshot checklist](guides/SCREENSHOT_CHECKLIST_THREAT_PREVENTION.md) | An internal authoring checklist | Not user-facing |
| [Lakera Playground](guides/n8n_Lakera_Playground_Guide.md) | Lakera Guard screening before and after the model | The example login and `localhost:5678` URL predate the current setup |
| [Observability with Langfuse](guides/Observability_Langfuse.md) | Tracing for each builder, reading a trace | None known |
| [Visible RAG](guides/Visible_RAG.md) | Ingest, embed, retrieve and cite | None known |
| [Evals Harness](guides/Evals_Harness.md) | Running and extending the evals | None known |
| [MCP Security Lab](guides/MCP_Security_Lab.md) | Attack, detect and defend | None known |
| [Build Your Own MCP Server](guides/Build_Your_Own_MCP_Exercise.md) | The stdlib MCP server exercise | Uses the no-op `--profile cpu` |
| [Identity Provisioning (SCIM)](guides/Identity_Provisioning_SCIM_Agent_Guide.md) | The SCIM user provisioning agent | The URL and token are now filled in automatically, and the workflow is activated on import |
| [PolicyPilot behind the Gateway](guides/PolicyPilot_Gateway_Sidecar_Guide.md) | Enabling and registering the sidecar | None known |
| [Capstone: Zero Trust Onboarding](guides/Capstone_Zero_Trust_Onboarding.md) | SCIM, PolicyPilot and Lakera, end to end | None known |

Other documents:

| Document | Covers | Known gaps |
|---|---|---|
| [Production Deployment](operations/PRODUCTION_DEPLOYMENT.md) | Hardening, TLS, secrets, scaling, monitoring | References `custom-mcp-n8n:custom`, n8n ports that don't exist and a missing monitoring guide |
| [Backup and Recovery](operations/BACKUP_RECOVERY.md) | Backup scripts, disaster recovery, RTO and RPO | Says Qdrant is not in the stack |
| [Developer Guide](development/DEVELOPER_GUIDE.md) | Dev setup, adding MCP tools, debugging | References `custom-mcp-n8n:custom`, `--profile cpu` and `docker compose build n8n` |
| [Directory Structure](development/DIRECTORY_STRUCTURE.md) | An annotated repo tree | Lists only 2 guides and 2 CI workflows |
| [Integrations README](../integrations/README.md) | Flowise and Langflow flow shapes, seeding | Written around the umbrella flow only |
| [Code-first agent](../integrations/code-agent/README.md) | The stdlib gateway client and agent loop | None known |
| Integration notes: [code-agent](../integrations/code-agent/INTEGRATION.md), [evals](../integrations/evals/INTEGRATION.md), [mcp-security-lab](../integrations/mcp-security-lab/INTEGRATION.md), [observability](../integrations/observability/INTEGRATION.md), [rag-cp-docs](../integrations/rag-cp-docs/INTEGRATION.md) | The paste sheets used to wire each feature into compose | The observability sheet still says n8n has no tracing |
| [PATCHES.md](../docker/n8n/mcp-src/PATCHES.md) | The local MCP server patches | The capability matrix is out of date, and it mentions GitHub Releases that don't exist |
| [Scripts README](../scripts/README.md) | Script usage | Its links to the operations guides are broken |
| [Tests README](../tests/README.md) | The test helpers | The tests assume host ports |

## Related repositories

| Repository | Role |
|---|---|
| [alshawwaf/ubuntu-dokploy-ai](https://github.com/alshawwaf/ubuntu-dokploy-ai) | One-command Ubuntu, Dokploy and Traefik installer for the suite. Deploys this repo |
| [alshawwaf/dev-hub](https://github.com/alshawwaf/dev-hub) | Desktop-style portal at `hub.<domain>` that embeds the apps. Hosts the DevHub MCP endpoint |
| [alshawwaf/checkpoint-mcp-on-aws-agentcore](https://github.com/alshawwaf/checkpoint-mcp-on-aws-agentcore) | Production path on AWS: 15 Check Point MCP servers behind one AgentCore Gateway (9 through the gateway by default), with a Claude on Bedrock agent |
| [alshawwaf/checkpoint-mcp-on-azure-foundry](https://github.com/alshawwaf/checkpoint-mcp-on-azure-foundry) | Production path on Azure: 15 Check Point MCP servers run as stdio child processes of a Foundry agent, with Key Vault and Entra ID. There is no gateway tier by default. An opt-in `--remote-mcp` tier is available |
| [alshawwaf/PolicyPilot](https://github.com/alshawwaf/PolicyPilot) | Guarded-write access automation over MCP |
| [alshawwaf/SAML_IDP_Simulator](https://github.com/alshawwaf/SAML_IDP_Simulator) | The identity provider simulator the SCIM agent talks to |
| [CheckPointSW/mcp-servers](https://github.com/CheckPointSW/mcp-servers) | The upstream Check Point MCP servers (MIT) |
| [Tencent/AI-Infra-Guard](https://github.com/Tencent/AI-Infra-Guard) | The upstream AI red-teaming platform (Apache-2.0) |

## License

- This repo is [MIT](../LICENSE), © 2025 Check Point Software Technologies Ltd.
- The vendored MCP sources in `docker/n8n/mcp-src/` are [MIT](../docker/n8n/mcp-src/LICENSE), with the same copyright holder.
- The AI-Infra-Guard images are built from Tencent/AI-Infra-Guard, which is licensed Apache-2.0.
- `assets/n8n-demo.gif` comes from n8n's self-hosted AI starter kit, which is licensed Apache-2.0.
