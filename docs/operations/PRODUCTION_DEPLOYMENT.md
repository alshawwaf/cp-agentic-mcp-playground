# Lab host deployment

This guide covers a shared lab host: one Linux server that a class reaches over HTTPS, with Dokploy,
a domain and the Complete lab. The lab is a training environment, not a platform for production
workloads. Connect it only to lab Check Point environments and lab data. Never connect it to a
production Management server, and never load customer configurations or customer telemetry into it.

## What you need

| Item | Requirement |
|------|-------------|
| Server | Linux with Docker Engine and Docker Compose v2 |
| Resources | The Complete lab: Docker with 6 CPUs and 16 GB of memory. Disk: about 12 GB for the Standard lab's images and volumes, more for the Complete lab's local models, plus room for backups |
| Domain | DNS for `n8n.<DOMAIN>`, `flowise.<DOMAIN>`, `langflow.<DOMAIN>`, `trace.<DOMAIN>` (Langfuse) and `chat.<DOMAIN>` (Open WebUI) |
| Inbound traffic | Ports 80 and 443 to the reverse proxy only, or a Cloudflare tunnel |
| Lab model | One provider key: Azure OpenAI, OpenAI, Anthropic or Gemini. The local model works without a key but is slow on a CPU for a whole class |
| Check Point | Lab credentials for the products the class uses (Management, Gaia, the Check Point portal API keys) |

## Deploy with Dokploy

The lab host installer, [alshawwaf/ubuntu-dokploy-ai](https://github.com/alshawwaf/ubuntu-dokploy-ai),
installs Dokploy with Traefik and deploys this repository as a Docker Compose project. Dokploy owns
the routes of n8n, Flowise, Langflow and Open WebUI. Langfuse brings its own Traefik labels in
`docker-compose.yml`.

### 1. Settings

On Dokploy, the project's environment settings take the place of `.env`: Docker Compose reads the
same variables from them. Set at least:

| Variable | Value on a lab host |
|----------|---------------------|
| `COMPOSE_PROFILES` | `complete` (add optional profiles comma-separated, for example `complete,exercises`) |
| `DOMAIN` | Your domain, for example `lab.example.com` |
| `N8N_HOST` | `n8n.<DOMAIN>` |
| `WEBHOOK_URL`, `N8N_EDITOR_BASE_URL` | `https://n8n.<DOMAIN>/` |
| `LANGFUSE_URL` | Blank: Langfuse is then at `https://trace.<DOMAIN>` |
| `LAB_IMAGE_TAG` | The full commit SHA of a published build (CI tags every build on `main` with it), so every redeploy runs the same lab image |
| `LANGFLOW_AUTO_LOGIN` | `false` |
| The model provider | `LAB_MODEL_PROVIDER` and the provider's variables (see `.env-example`, section 2) |
| The lab secrets | Generated, see below |

Without `COMPOSE_PROFILES=complete` a lab host runs the Standard lab: Langflow and Open WebUI do
not start. `./setup.sh` and `./scripts/doctor.sh` warn about it when `DOMAIN` is set.

To generate a complete settings file for a lab host, run setup in a private checkout:

```sh
DOMAIN=lab.example.com COMPOSE_PROFILES=complete ./setup.sh --non-interactive
```

With `DOMAIN` set, setup derives `N8N_HOST`, `WEBHOOK_URL` and `N8N_EDITOR_BASE_URL` and leaves
`LANGFUSE_URL` blank. Put the provider and Check Point keys in the environment of that command (same
names as in `.env-example`), or add them afterwards. Copy the values into the project environment,
then delete the copy.

### 2. Domains

| App | Service | Port inside the lab network | Route |
|-----|---------|-----------------------------|-------|
| n8n | `n8n` | 5678 | Dokploy domain `n8n.<DOMAIN>` |
| Flowise | `flowise` | 3020 (`FLOWISE_PORT`) | Dokploy domain `flowise.<DOMAIN>` |
| Langflow | `langflow` | 7860 | Dokploy domain `langflow.<DOMAIN>` |
| Open WebUI | `open-webui` | 8080 | Dokploy domain `chat.<DOMAIN>` |
| Langfuse | `langfuse` | 3000 | Traefik labels in `docker-compose.yml`: `trace.<DOMAIN>` |

Flowise, Langflow and Open WebUI carry only `traefik.enable=true` and
`traefik.docker.network=dokploy-network` in compose. Do not add router labels for them: a second
router set for the same host gives intermittent 404 errors.

### 3. Deploy and check

Deploy the project. The first start pulls several GB of images and takes 5 to 10 minutes. Then, on the
host, in the project directory:

```sh
./scripts/doctor.sh --post-start
tests/acceptance/run.sh
```

Expected result: doctor reports no blockers, and every acceptance check passes or is `SKIP` for a
profile that is off. A redeploy recreates the services; agents you changed in the builders are kept
(see [integrations/README.md](../../integrations/README.md)).

### On a plain Docker host

The lab host setup is built for Dokploy. Without it, run `./setup.sh` on the host: it writes `.env`
with mode 600 and creates the external `dokploy-network` when it is missing. A reverse proxy of your
own then joins `dokploy-network`, routes the hosts in the table above to the service ports and
terminates TLS. Flowise, Langflow, Open WebUI and Langfuse are already on `dokploy-network`. n8n is
not: on a lab host the installer's Dokploy domain settings route it. Without Dokploy, add it in a
git-ignored `docker-compose.override.yml`:

```yaml
services:
  n8n:
    networks: [ "lab", "security-lab", "dokploy-network" ]
```

Then start the lab with `docker compose up -d`.

## TLS

- **Browsers to the lab.** The reverse proxy terminates TLS. The Langfuse routes use the Traefik
  certificate resolver `letsencrypt`. Behind a Cloudflare tunnel, traffic arrives on the plain HTTP
  entrypoint `web`, which is why Langfuse has a `web` and a `websecure` router. Give every Dokploy
  domain HTTPS.
- **Inside the lab.** Services talk plain HTTP on the lab network, which has no published port. n8n
  runs with `N8N_SECURE_COOKIE=false` for this reason.
- **The lab to Check Point.** The Check Point MCP servers always verify certificates. For a
  self-signed Management server or gateway, add its certificate as described in
  [certs/README.md](../../certs/README.md). Never turn verification off.
- **The lab to the model provider.** LiteLLM calls the provider over HTTPS. `AZURE_OPENAI_ENDPOINT`
  must start with `https://` (doctor checks it). Behind TLS inspection, the `litellm` container
  itself must trust the inspecting CA; the host's trust store does not reach into containers, and
  `docker-compose.yml` has no setting for it. Ask your network team to exempt the provider endpoints
  from inspection. Do not turn verification off.

## Secrets

`./setup.sh` generates every lab secret: the lab admin password, `POSTGRES_PASSWORD`,
`N8N_ENCRYPTION_KEY`, `N8N_USER_MANAGEMENT_JWT_SECRET`, `MCP_GATEWAY_TOKEN`, `LITELLM_MASTER_KEY`,
`NEXTAUTH_SECRET`, `SALT`, `LANGFUSE_ENCRYPTION_KEY`, the Langfuse key pair, `WEBUI_SECRET_KEY` and
`PILOT_SESSION_SECRET`. No secret has a public default.

The lab fails closed:

- Docker Compose refuses to start without `POSTGRES_PASSWORD`, `N8N_ENCRYPTION_KEY`,
  `N8N_USER_MANAGEMENT_JWT_SECRET`, `NEXTAUTH_SECRET`, `SALT`, `LANGFUSE_ENCRYPTION_KEY` and
  `MCP_GATEWAY_TOKEN`.
- LiteLLM refuses to start without `LITELLM_MASTER_KEY`, and answers any other key with HTTP 401.
- `./scripts/doctor.sh --preflight` fails on blank secrets, placeholders and the published training
  values of older lab versions. The importers warn about them too.

Each secret reaches only the containers that need it. The model provider key goes only to `litellm`:
the acceptance check `KEYS` proves that no builder container holds a provider key. The Check Point
keys go only to the MCP servers of their product.

### 1Password

`./setup.sh --1password` writes references (`op://<vault>/<item>/<field>`, default vault `Private`,
item `checkpoint-ai-lab`) instead of key values, and can store the generated secrets in a second
item, `<item>-secrets`. Start and check the lab through 1Password:

```sh
op run --env-file=.env -- docker compose up -d
op run --env-file=.env -- ./scripts/doctor.sh --post-start
op run --env-file=.env -- tests/acceptance/run.sh
```

No service passes `.env` through to a container (`env_file` is refused by the CI policy check), so an
unresolved `op://` reference never reaches a container as a value. `doctor.sh --post-start` reports a
container that holds one.

### Changing a secret

| Secret | Change it after the first start? |
|--------|----------------------------------|
| Provider keys, Check Point keys, `LAKERA_API_KEY`, `IDP_SCIM_TOKEN`, `DEVHUB_MCP_TOKEN`, `PILOT_MCP_TOKEN`, `QDRANT_API_KEY` | Yes |
| `MCP_GATEWAY_TOKEN`, `LITELLM_MASTER_KEY` | Yes. The importers re-sync them into n8n, Flowise and Langflow |
| `POSTGRES_PASSWORD`, `N8N_ENCRYPTION_KEY`, `SALT`, `LANGFUSE_ENCRYPTION_KEY` | No: the databases keep the values they started with |

After a change:

```sh
docker compose up -d
docker compose run --rm n8n-import
docker compose run --rm builders-import
```

With the `ai-red-team` profile on, `aig-provision` leaves an existing `lab-chat` model in
AI-Infra-Guard as it is. After a `LITELLM_MASTER_KEY` change, update that model's key in
AI-Infra-Guard.

## Hardening already in place

| Area | What the lab does |
|------|-------------------|
| Ports | No service publishes a host port. The CI policy check (`.github/scripts/check_compose_policy.py`) fails any pull request that adds one |
| Docker API | Only `docker-socket-proxy` mounts the Docker socket, read-only. It forwards the gateway's container and network reads and refuses every write (`POST=0`). The gateway uses `DOCKER_HOST=tcp://docker-socket-proxy:2375` |
| Containers | The MCP servers and the gateway drop every capability. They, the socket proxy and the attack-lab services run with `no-new-privileges`. Every service has a CPU and a memory limit and rotated logs (3 files of 10 MB) |
| Images | Third-party images are pinned by tag and digest. The lab's own images are built by CI and scanned by Trivy (fixable CRITICAL fails the build). `LAB_IMAGE_TAG` selects the build (default `latest`; pin a commit on a lab host) |
| Sign-in | n8n: the owner account; its chat URLs ask for the lab admin (HTTP Basic). Flowise: the lab admin; its prediction API needs the API key "Lab Agents API". Langflow: the superuser, auto-login off. Langfuse: sign-up off. Open WebUI: the admin is created before anyone can sign up |
| Flowise | Flows may reach the lab network and the internet, but loopback, link-local (cloud metadata) and multicast addresses stay denied (`HTTP_DENY_LIST`). Custom MCP runs over HTTP only (`CUSTOM_MCP_PROTOCOL=sse`): no local commands |
| Check Point MCP servers | Certificate verification always on. Credentials go only to the configured hosts. Secrets are redacted in logs. Idle sessions close after 30 minutes, at most 32 per server. CPInfo Analysis and Threat Emulation read files only from `./n8n/shared` |
| Attack labs | `vuln-mcp` and AI-Infra-Guard run only in their profiles and only on internal networks (`security-lab`, `ai-red-team`) with no internet access and no route to the host |
| Settings file | `.env` is written with mode 600 and is git-ignored |

## Your checklist

- Pin `LAB_IMAGE_TAG` to a commit and change it on purpose.
- Keep `LANGFLOW_AUTO_LOGIN=false`.
- Open only 80 and 443 (or use the tunnel), plus SSH for administrators.
- Never publish a lab port. For a local check on the host, publish on `127.0.0.1` only, in a
  git-ignored `docker-compose.override.yml`.
- Start the `security-lab` and `ai-red-team` profiles only while you teach them.
- Give the lab a cloud model key, so a class does not queue on a local CPU model.
- Schedule encrypted backups and test a restore (see [BACKUP_RECOVERY.md](BACKUP_RECOVERY.md)).
- Keep the host patched, and update the lab with `./update.sh` (or a Dokploy redeploy).
- After every update run `./scripts/doctor.sh --post-start` and `tests/acceptance/run.sh`.

## What never gets a route

Route only n8n, Flowise, Langflow, Open WebUI and Langfuse.

| Service | Why it stays internal |
|---------|-----------------------|
| `mcp-gateway` and every Check Point MCP server | They act on Check Point products with the lab's credentials |
| `litellm` | It holds the provider key; its master key gives model access |
| `postgres`, `qdrant`, `ollama-cpu` | Lab data and models. `ollama-cpu` joins `dokploy-network` only so other apps on the host can reach it by name |
| `docker-socket-proxy` | The Docker API |
| `aig-webserver`, `aig-ui` | AI-Infra-Guard has no sign-in. Reach its UI only through an SSH tunnel to the `127.0.0.1` port of `aig-ui` |
| `vuln-mcp` | Intentionally vulnerable |
| Langflow with `LANGFLOW_AUTO_LOGIN=true` | No sign-in at all |

## Operate the lab host

| Task | Command |
|------|---------|
| Post-start check | `./scripts/doctor.sh --post-start` |
| End-to-end tests | `tests/acceptance/run.sh` |
| Scheduled health check | `./scripts/health-check.sh` (see [scripts/README.md](../../scripts/README.md)) |
| Update | `./update.sh` |
| Re-seed after a settings change | `docker compose run --rm n8n-import` and `docker compose run --rm builders-import` |
| Logs | `docker compose logs <service>` |
| Backup | `./scripts/backup-volumes.sh --passphrase-file <file>` |

## Known limits

- Langfuse runs 2.95.11, the last v2 release. v3 needs ClickHouse, Redis and object storage.
- Postgres runs 16. A major upgrade needs a dump and a restore.
- Flowise stores its variables (the gateway token among them) as plain values in its database, and
  Langflow keeps the gateway token in the seeded gateway flows. Protect the databases and the
  backups accordingly.
- The backup archive is encrypted (AES-256-CBC) but carries no authentication code. Store it where
  only administrators can write.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `network dokploy-network declared as external, but could not be found` | Plain Docker host: run `./setup.sh`, or `docker network create dokploy-network` |
| `MCP_GATEWAY_TOKEN is not set. Run ./setup.sh` (or another required secret) | Add the generated secrets to the project environment |
| Langflow and Open WebUI are missing | Set `COMPOSE_PROFILES=complete` and redeploy |
| Langfuse sign-in redirects to `localhost` | Leave `LANGFUSE_URL` blank on a lab host, with `DOMAIN` set |
| The DevHub, PolicyPilot or SCIM agents are not published | Set `DOMAIN` and the agent's token, then re-run the importers |
| `TLS certificate verification failed` | Add the server's certificate: [certs/README.md](../../certs/README.md) |
| Agents answer slowly or time out | `lab-chat` runs on the local model. Set a cloud provider key and run `docker compose up -d litellm` |
