# Developer guide

For maintainers who change the lab: agents, MCP servers, compose, scripts and tests. Every change
lands through a pull request, and the CI gate must pass.

## Set up a development lab

```sh
git clone https://github.com/alshawwaf/cp-agentic-mcp-playground.git
cd cp-agentic-mcp-playground
./setup.sh                       # or: COMPOSE_PROFILES=complete ./setup.sh --non-interactive
docker compose up -d
./scripts/doctor.sh --post-start # 5 to 10 minutes later
tests/acceptance/run.sh
```

Work on Langflow needs the Complete lab (`COMPOSE_PROFILES=complete`) or the `langflow` profile.
`./setup.sh` generates every secret; there are no default passwords to fall back on.

### Open the apps on your computer

The lab publishes no port. For local work, publish the apps on the loopback address in a
`docker-compose.override.yml` next to `docker-compose.yml` (git-ignored, merged by Docker Compose):

```yaml
services:
  n8n:
    ports: [ "127.0.0.1:5678:5678" ]
  flowise:
    ports: [ "127.0.0.1:3020:3020" ]
  langfuse:
    ports: [ "127.0.0.1:3100:3000" ]
```

These match the workstation defaults: `WEBHOOK_URL=http://localhost:5678/`, `FLOWISE_PORT=3020` and
`LANGFUSE_URL=http://localhost:3100` (set by `./setup.sh` when `DOMAIN` is blank). Langflow listens
on 7860 and Open WebUI on 8080 inside the lab network. Never publish on `0.0.0.0`.

### Build the lab image locally

`n8n`, `n8n-import` and every Check Point MCP server run the lab image
`ghcr.io/alshawwaf/cp-agentic-n8n:${LAB_IMAGE_TAG}`, built from `docker/n8n/Dockerfile`:

```sh
docker build -t ghcr.io/alshawwaf/cp-agentic-n8n:local docker/n8n
```

Set `LAB_IMAGE_TAG=local` in `.env` and run `docker compose up -d`. The Build Your Own MCP image builds
with `docker compose --profile exercises up -d --build ips-cve-mcp`.

## Sources of truth

Change a fact in one place; the generators and checks carry it everywhere else.

| Fact | Where |
|------|-------|
| Agent names, slugs, kinds, endpoints, tool counts, prerequisites, former names | `integrations/builders_agents.json` |
| Tools of each MCP server (`SERVER_TOOLS`), curated tool lists, sidecar URLs, product names, prompts, `RAG_MIN_SCORE` | `scripts/flows/langflow_fix.py` |
| n8n credentials, workflow file map, n8n-only texts, publishing prerequisites (`REQUIRES`) | `scripts/flows/n8n_fix.py` |
| Flowise node versions and Lakera Guard agentflow layout | `scripts/flows/flowise_fix.py` |
| Services, profiles, limits, networks, image pins | `docker-compose.yml` |
| Servers behind the MCP Gateway | `mcp-gateway/catalog.yaml` and the gateway's `--servers` list in `docker-compose.yml` |
| Settings and their documentation | `.env-example` |
| Vendored Check Point MCP servers and the lab's patches | `docker/n8n/mcp-src/`, `docker/n8n/mcp-src/PATCHES.md` |

Naming: per-server agents are "<Product> Agent (MCP Gateway)" and "<Product> Agent (Direct)", with
current product names. Names, prompts, greetings and sticky notes never abbreviate Check Point and
use no informal wording: the `check` commands fail on the words in the `UNPROFESSIONAL` pattern of
`scripts/flows/langflow_fix.py`.

## The flow generators

The 33 Flowise flows, the 33 Langflow flows and the 35 n8n workflows are generated files. Change them
through the generators in `scripts/flows/` (standard library only), never by hand alone.

| Command | What it does |
|---------|--------------|
| `python3 scripts/flows/langflow_fix.py apply` | Rewrites `integrations/langflow/*.flow.json`: model wiring to `lab-chat`, MCP wiring and tool scopes, prompts, names. Idempotent |
| `python3 scripts/flows/flowise_fix.py apply` | The same for `integrations/flowise/*.flowdata.json`, including the Lakera Guard agentflows |
| `python3 scripts/flows/n8n_fix.py apply` | The same for `n8n/backup/workflows/*.json` and `n8n/backup/credentials_public/*.json` |
| `... check` | Validates every file; exit 1 on any problem. CI runs all three |
| `python3 scripts/check_workflow_uniqueness.py` | Duplicate ids, webhook paths, node names and dangling credential references in the n8n set |

The usual loop after a change to a shared table or a flow:

```sh
python3 scripts/flows/langflow_fix.py apply
python3 scripts/flows/flowise_fix.py apply
python3 scripts/flows/n8n_fix.py apply
python3 scripts/flows/langflow_fix.py check
python3 scripts/flows/flowise_fix.py check
python3 scripts/flows/n8n_fix.py check
python3 scripts/check_workflow_uniqueness.py
git diff --stat
```

`flowise_fix.py` and `n8n_fix.py` import their tool lists and prompts from `langflow_fix.py`, so run
Langflow first. To run them as CI does, prefix each command with `.github/scripts/py-isolated.sh --`.

### When you upgrade Langflow or Flowise, or a server's tools change

`apply` alone keeps the node templates in the files. `snapshot` saves what the installed builder
version renders, and `apply --snapshot DIR` rebuilds the templates and edge handles from it. Run
`snapshot` inside the lab network, against a lab that runs the new version. In bash or zsh, from the
repository root (the lab network is `<project>_lab`):

```bash
mkdir -p "$HOME/lab-snapshots"
docker run --rm --network <project>_lab \
  -v "$PWD":/repo:ro -v "$HOME/lab-snapshots":/snap -w /repo -e PYTHONDONTWRITEBYTECODE=1 \
  --env-file <(grep -E '^(N8N_ADMIN_EMAIL|N8N_ADMIN_PASSWORD|MCP_GATEWAY_TOKEN)=' .env | sed 's/^N8N_ADMIN_/ADMIN_/') \
  python:3.12-alpine python3 scripts/flows/langflow_fix.py snapshot --out /snap/langflow
python3 scripts/flows/langflow_fix.py apply --snapshot "$HOME/lab-snapshots/langflow"
```

For Flowise, run `flowise_fix.py snapshot --out /snap/flowise` the same way and add
`-e FLOWISE_URL=http://flowise:3020` (its built-in default port differs from the lab's). The snapshots
contain no secrets. When a server's tool list changed, update `SERVER_TOOLS` first; the acceptance
checks `GW-TOOLS` and `DIRECT` compare the live tool lists with it.

### Push your changes into a running lab

The importers keep agents that were changed in the builders. On your development lab, replace them:

```sh
docker compose run --rm -e N8N_SEED_OVERWRITE=1 n8n-import
docker compose run --rm -e SEED_OVERWRITE=1 builders-import
```

Do not run these on a lab host during a class: they replace the trainees' edits to seeded agents.

## Add an agent

1. **Catalog entry.** Add the agent to `integrations/builders_agents.json`: `slug`, `name`, `kind`
   (`gateway`, `direct`, `external` or `native`), `endpoint`, `auth` (the placeholder of its token, if
   any), `tools`, the two file paths, and `requires` (variables it needs) or `profile` when it has
   prerequisites.
2. **Flow files.** Copy the closest existing agent into `integrations/flowise/<slug>.flowdata.json`,
   `integrations/langflow/<slug>.flow.json` and a workflow file in `n8n/backup/workflows/`. Give the n8n
   copy a new workflow id, chat-trigger `webhookId` and node ids.
3. **Generator tables.** Register the slug in `scripts/flows/langflow_fix.py` (`GATEWAY_TWINS`,
   `DIRECT_TWINS`, `CURATED`, `EXTERNAL` or `NO_MCP`) and the n8n file in `scripts/flows/n8n_fix.py`
   (`GATEWAY_FILES`, `DIRECT_FILES` or `OTHER_FILES`; `REQUIRES` for publishing prerequisites).
   Agents that span servers bind a curated list; keep it at 128 tools or fewer.
4. **Generate and check** with the loop above. `flowise_fix.py check` fails for a Flowise file that
   has no catalog entry.
5. **Seed and test.** Push it into your lab (see above), chat with it in every builder, then run
   `tests/acceptance/run.sh --only N8N-SEED,FLOWISE-SEED,LANGFLOW-SEED`.
6. **Document it.** Add or update its guide in `docs/guides/`, the agent table in
   [integrations/README.md](../../integrations/README.md), and an evals case if the agent has a
   checkable answer (see [integrations/evals/INTEGRATION.md](../../integrations/evals/INTEGRATION.md)).

To rename an agent, change `name` and add the old name to `former_names`. The seeders rename the
deployed copy in place; n8n keeps the workflow id.

## Add a Check Point MCP server

1. **Source.** Add the package under `docker/n8n/mcp-src/packages/`. Apply the lab's patches to it
   (TLS verification, credential separation, session limits, redaction) and list it in
   `docker/n8n/mcp-src/PATCHES.md`. No `@chkp` package comes from the npm registry: the Dockerfile
   installs the server CLIs only from the tarballs it packs. Check the license of every new dependency
   first, and keep `package-lock.json` in step (`npm ci` refuses a lock that is out of date).
2. **Wrapper.** Add `wrap <service-name> <cli-name>` to `docker/n8n/Dockerfile`, which creates
   `/usr/local/bin/<service-name>`.
3. **Service.** Add a service to `docker-compose.yml` with `<<: *mcp-sidecar`, a `container_name`,
   and `MCP_SERVICE`, `MCP_PORT` (the next free port) and `MCP_PRODUCT`. For a server that cannot
   start without its settings, add `MCP_REQUIRED` and `MCP_SETTINGS`: it then prints one "not
   configured" line and stops cleanly. Pass only the variables it needs. Add the variables to
   `.env-example` with a comment.
4. **Gateway.** Add a `registry` entry to `mcp-gateway/catalog.yaml` (`type: "remote"`, its URL,
   `transport_type: "streamable"`), add its name to the gateway's `--servers` list and its service to
   the gateway's `depends_on` with `condition: service_healthy`. The gateway lists tools once at start.
5. **Tables.** Add the server to `SERVER_TOOLS`, `PRODUCT` and `SIDECAR_URL` in
   `scripts/flows/langflow_fix.py`, and to the per-server texts in both `langflow_fix.py` and
   `n8n_fix.py`. Search the generators for an existing server id, for example `threat-emulation`, to
   find every table. Update `EXPECTED_GATEWAY_TOOLS` in `tests/acceptance/acceptance.py` (190 today)
   and the server lists in `.github/scripts/mcp-image-smoke.mjs` and `tests/mcp-src/static.test.mjs`.
6. **Agents.** Add its "(MCP Gateway)" and "(Direct)" agents as described above, and add their tools
   to the curated lists where they belong (keep each list at 128 tools or fewer).
7. **Test.** Build the image, start the lab, run `./scripts/doctor.sh --post-start` (it reads the
   direct servers from `catalog.yaml`) and `tests/acceptance/run.sh --only GW-TOOLS,DIRECT`.

A server that must not be behind the gateway, such as the MCP Security Lab's `vuln-mcp`, stays out
of `catalog.yaml`.

## CI

| Workflow | Runs | What must pass |
|----------|------|----------------|
| `ci.yml`, job Validate | Every pull request and push to `main` | `./setup.sh --non-interactive` (`.env` mode 600, no placeholders), `docker compose config` for the default stack and every profile, the compose policy check, the three flow checks and the uniqueness check, shellcheck on every `*.sh`, actionlint, `py_compile` on every `*.py`, and the unit tests |
| `ci.yml`, job Setup on macOS | same | `setup.sh` and `doctor.sh --preflight` with `/bin/sh` and BSD tools (Docker is a stub), and a second setup run that leaves `.env` unchanged |
| `ci.yml`, job MCP server security tests | same | `tests/mcp-src/run.sh` |
| `ci.yml`, job RAG threshold | same | `tests/rag-threshold/run.sh` |
| `ci.yml`, job Lab images | same | Build of `docker/n8n` with the commit as OCI revision label, a smoke test that starts every MCP wrapper with no network and lists its tools, the exercise image and its self-test, and a Trivy gate: no fixable CRITICAL vulnerability |
| `live-stack.yml` | Nightly and on demand | The Standard lab from the commit and the acceptance tests. Not a merge gate |
| `publish-image.yml` | After CI passes on `main` | Publishes `cp-agentic-n8n` and `cp-agentic-ips-cve-mcp` to GHCR for `linux/amd64` and `linux/arm64`, tagged with the commit (and `latest` while it is the head of `main`), with provenance and SBOM |
| `security-scan.yml` | Weekly and on demand | Trivy report of the published images and every pinned third-party image, to the Security tab |

The compose policy check (`.github/scripts/check_compose_policy.py`) fails a change that publishes a
host port, runs a container privileged or on the host network, mounts the Docker socket anywhere but
`docker-socket-proxy`, leaves a service without CPU and memory limits, uses an image without tag and
digest (the lab's own images excepted), weakens the containment of the attack labs, or passes `.env`
through with `env_file`. To run it locally, use a throwaway checkout with a throwaway `.env`
(`./setup.sh --non-interactive`), as CI does: the resolved configuration contains the secrets.

An accepted vulnerability goes into `.github/trivy/trivyignore` with a reason, an owner decision and
an expiry date. Dependabot keeps the pinned GitHub Actions current; image pins are bumped by hand.

## Vendored MCP servers

The servers in `docker/n8n/mcp-src/` come from Check Point's MCP servers repository. Every lab change
is marked `LAB PATCH` in the code and explained in `PATCHES.md`. After a change, run
`tests/mcp-src/run.sh` and add a check for the new behavior. Never weaken certificate verification or
let credentials go to a host the operator did not configure.

## Debugging

| Task | Command |
|------|---------|
| Logs of one service | `docker compose logs -f <service>` |
| What `lab-chat` runs on | `docker compose logs litellm \| grep lab-litellm` |
| Validate the model settings | `docker compose run --rm --no-deps litellm --check` |
| The LiteLLM config in use (holds no secrets) | `docker compose exec litellm cat /tmp/lab-litellm/config.yaml` |
| Shell in a container | `docker compose exec n8n sh` |
| Gateway and direct servers | `./scripts/health-check.sh --verbose` |
| One acceptance check | `tests/acceptance/run.sh --only <ID>` |

The n8n image has no `curl`. Call lab services from a throwaway container on the lab network
instead, as the scripts do.

Never fix a failing check with `docker compose down -v` or an image prune: that deletes the lab's
workflows, credentials and models.
