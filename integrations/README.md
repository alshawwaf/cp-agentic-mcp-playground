# Integrations

This folder holds the agents the lab seeds into Flowise and Langflow, the seeder that imports them,
the lab model endpoint (LiteLLM) and the add-on integrations. The n8n versions of the same agents
live in `n8n/backup/` and are imported by the `n8n-import` service.

| Path | What it is |
|------|------------|
| `builders_agents.json` | The agent catalog: name, slug, kind, MCP endpoint, tool count and prerequisites of every agent. It is the source of truth for agent names in n8n, Flowise, Langflow, the evals and the guides. |
| `seed_builders.py`, `seed_builders.sh` | The Flowise and Langflow seeder. It runs as the one-shot service `builders-import` on every deploy. |
| `flowise/*.flowdata.json` | 33 Flowise agents, one per catalog entry. Generated and checked by `scripts/flows/flowise_fix.py`. |
| `langflow/*.flow.json` | 33 Langflow agents, one per catalog entry. Generated and checked by `scripts/flows/langflow_fix.py`. |
| `litellm/` | `render_config.py` (entrypoint of the `litellm` service) and `lab_auth.py` (key check). Together they serve the one lab model, `lab-chat`. |
| `code-agent/` | The code-first agent: the same gateway and model, in plain Python. See [code-agent/README.md](code-agent/README.md). |
| `evals/` | The evals harness for the n8n agents. See [evals/INTEGRATION.md](evals/INTEGRATION.md). |
| `rag-cp-docs/` | The Visible RAG corpus and its ingester. See [rag-cp-docs/INTEGRATION.md](rag-cp-docs/INTEGRATION.md). |
| `observability/` | Langfuse helpers. See [observability/INTEGRATION.md](observability/INTEGRATION.md). |
| `mcp-security-lab/` | The intentionally vulnerable MCP server for the MCP Security Lab. See [mcp-security-lab/INTEGRATION.md](mcp-security-lab/INTEGRATION.md). |

Change the flow files with the generators in `scripts/flows/`, not by hand. The CI gate runs their
`check` command on every pull request. See [docs/development/DEVELOPER_GUIDE.md](../docs/development/DEVELOPER_GUIDE.md).

## How every agent is wired

**Model.** Every seeded agent uses one model, `lab-chat`, at `http://litellm:4000/v1`, with the key
`LITELLM_MASTER_KEY`. LiteLLM picks the provider from `.env` (`LAB_MODEL_PROVIDER`, default `auto`).
The builders never receive a provider key. To change the provider or a key, edit `.env` (or the
1Password item) and run `docker compose up -d litellm`. No agent changes.

| Builder | Model node | Key |
|---------|------------|-----|
| n8n | OpenAI Chat Model (`lmChatOpenAi`), model `lab-chat` | Credential **Lab Model (LiteLLM)** (`n8n/backup/credentials_public/openai.json`, base URL `http://litellm:4000/v1`) |
| Flowise | ChatOpenAI (`chatOpenAI`), Base Path `http://litellm:4000/v1`, model `lab-chat` | Credential **Lab Model (LiteLLM)**, created by the seeder |
| Langflow | OpenAI component (`OpenAIModel`), `openai_api_base` `http://litellm:4000/v1`, model `lab-chat` | Global variable `LITELLM_MASTER_KEY`, created by the seeder |

**MCP tools.** An agent reaches the Check Point MCP servers one of two ways:

- **MCP Gateway**: `http://mcp-gateway:8080/mcp` with the header `Authorization: Bearer <MCP_GATEWAY_TOKEN>`.
  The gateway fronts 11 servers and lists 190 tools.
- **Direct**: the server's own address on the lab network, with no gateway and no token.

| Server | Direct address |
|--------|----------------|
| Documentation | `http://mcp-documentation:3000` |
| HTTPS Inspection | `http://mcp-https-inspection:3001` |
| Management | `http://mcp-quantum-management:3002` |
| Management Logs | `http://mcp-management-logs:3003` |
| Threat Emulation | `http://threat-emulation-mcp:3004` |
| Threat Prevention | `http://threat-prevention-mcp:3005` |
| Reputation Service | `http://reputation-service-mcp:3007` |
| Gateway CLI | `http://quantum-gw-cli-mcp:3009` |
| Gaia | `http://quantum-gaia-mcp:3011/mcp` |
| CPInfo Analysis | `http://cpinfo-analysis-mcp:3012` |
| Policy Insights | `http://policy-insights-mcp:3013` |

**Tool scope.** Each "(MCP Gateway)" agent binds only its own server's tools, so it has the same
tool set as its "(Direct)" twin. The agents that span servers use curated lists: the Check Point MCP
Gateway Agent binds 48 tools, the Fleet Commander Estate Agent and the Guarded Agent (Lakera Guard)
117, and the SOC Response Chain Agent 62. No agent binds more than 128 tools (the per-request
limit of OpenAI and Azure OpenAI). The tool lists live in `scripts/flows/langflow_fix.py`
(`SERVER_TOOLS`, `UMBRELLA_CORE`, `FLEET_CORE`, `SOC_CORE`).

## The agents

The same 33 agents exist in n8n, Flowise and Langflow, under the same names. An agent whose product
is not configured still starts and answers that the product is not configured.

| Agent | MCP path | Tools | Needs in `.env` (beyond the lab model) |
|-------|----------|-------|-----------------------------------------|
| Check Point MCP Gateway Agent | MCP Gateway | 48 | The products it queries |
| Management Agent (MCP Gateway), Management Agent (Direct) | Gateway, Direct | 50 | Management server (see below) |
| Management Logs Agent (MCP Gateway), Management Logs Agent (Direct) | Gateway, Direct | 7 | Management server |
| Policy Insights Agent (MCP Gateway), Policy Insights Agent (Direct) | Gateway, Direct | 10 | Management server with Management API v2.1 (R82.10 or later) and Policy Insights enabled |
| Threat Prevention Agent (MCP Gateway), Threat Prevention Agent (Direct) | Gateway, Direct | 25 | Management server |
| HTTPS Inspection Agent (MCP Gateway), HTTPS Inspection Agent (Direct) | Gateway, Direct | 9 | Management server |
| Gateway CLI Agent (MCP Gateway), Gateway CLI Agent (Direct) | Gateway, Direct | 26 | Management server |
| Gaia Agent (MCP Gateway), Gaia Agent (Direct) | Gateway, Direct | 42 | `GAIA_GATEWAY_IP`, `GAIA_USERNAME`, `GAIA_PASSWORD` |
| CPInfo Analysis Agent (MCP Gateway), CPInfo Analysis Agent (Direct) | Gateway, Direct | 12 | A CPInfo file in `./n8n/shared` |
| Documentation Agent (MCP Gateway), Documentation Agent (Direct) | Gateway, Direct | 1 | `DOC_CLIENT_ID`, `DOC_SECRET_KEY` |
| Threat Emulation Agent (MCP Gateway), Threat Emulation Agent (Direct) | Gateway, Direct | 5 | `TE_API_KEY`; files to scan in `./n8n/shared` |
| Reputation Service Agent (MCP Gateway), Reputation Service Agent (Direct) | Gateway, Direct | 3 | `REPUTATION_API_KEY` |
| Fleet Commander Estate Agent | MCP Gateway | 117 | The products it queries |
| SOC Response Chain Agent | MCP Gateway | 62 | Management server, `REPUTATION_API_KEY` |
| Guarded Agent (Lakera Guard) | MCP Gateway | 117 | `LAKERA_API_KEY` |
| Lakera Guard Screening Agent | none | 0 | `LAKERA_API_KEY` |
| Documentation RAG Agent | none (Qdrant collection `cp_docs`) | 0 | Nothing: `rag-ingest` fills the collection |
| Identity Provisioning Agent (SCIM) | none (`https://idp.<DOMAIN>/scim/v2/Users`) | 0 | `DOMAIN`, `IDP_SCIM_TOKEN` |
| DevHub Operations Agent | `https://hub.<DOMAIN>/api/mcp` | 8 | `DOMAIN`, `DEVHUB_MCP_TOKEN` |
| PolicyPilot Access Automation Agent (Pro) | `https://policypilot.<DOMAIN>/mcp/` | 31 | `DOMAIN`, `PILOT_MCP_TOKEN` |
| PolicyPilot Dynamic Layers Agent | `https://policypilot.<DOMAIN>/mcp/` | 31 | `DOMAIN`, `PILOT_MCP_TOKEN` |
| MCP Security Lab Agent (Intentionally Vulnerable) | Direct, `http://vuln-mcp:3099` | 4 | The `security-lab` profile |

**Management server** means `MANAGEMENT_HOST` (on-premises) or `S1C_URL` (Smart-1 Cloud), plus
`MANAGEMENT_API_KEY` (or, on-premises only, `MANAGEMENT_USERNAME` and `MANAGEMENT_PASSWORD`). A
self-signed certificate needs `MANAGEMENT_CA_CERT` (see [certs/README.md](../certs/README.md)).

n8n has two more workflows: **Documentation RAG Retriever** (the sub-workflow the RAG agent calls)
and **Nightly Agent Self-Check** (published only with `NIGHTLY_SELF_QA=1`). That makes 35 n8n
workflows.

In Flowise, the Guarded Agent (Lakera Guard) and the Lakera Guard Screening Agent are Agentflow V2
flows: Start, Lakera Guard (input), Allowed?, the agent, Lakera Guard (output), and a direct reply
for blocked prompts. The other Flowise agents are chatflows with a Tool Agent.

## Seeding, per builder

Seeding runs on every `docker compose up -d`. `.env` (or 1Password) is the source of truth for every
secret: each run writes the current values into the builders.

### n8n: `n8n-import`

`n8n-import` runs `scripts/n8n-provision.sh import` in the lab image, after `n8n-provision` has
created the n8n owner from `N8N_ADMIN_EMAIL` and `N8N_ADMIN_PASSWORD`.

1. **Credentials.** It copies the 8 files in `n8n/backup/credentials_public/`, fills the
   placeholders from the environment and imports them. Every run replaces edits made to these
   credentials in the n8n UI.
2. **Workflows.** It fills `{{DOMAIN}}` and the Lakera Guard settings, then imports the 35
   workflows by id. A seeded workflow that you changed in n8n since the last import is kept and
   named in the log.
3. **Publishing.** It publishes the workflows through the n8n REST API, so their chat URLs work at
   once, with no n8n restart. A workflow whose prerequisites are not met (its `meta.labRequires`) is
   imported but not published, and the log says what it needs. On a lab with no optional settings,
   29 workflows are published and 6 wait for their prerequisites.

The hosted chat URL of every n8n agent asks for HTTP Basic sign-in with the lab admin (credential
**Lab Agents Chat**). **Open chat** in the n8n editor needs no extra sign-in.

### Flowise: `builders-import`

`builders-import` runs `seed_builders.py` once Flowise is healthy. It signs in with
`FLOWISE_API_KEY` when set, otherwise with the lab admin (on a fresh Flowise database it registers
the admin first). Then it:

- creates or updates the credential **Lab Model (LiteLLM)** from `LITELLM_MASTER_KEY` and attaches it
  to every seeded model node
- creates or updates the Flowise variables `MCP_GATEWAY_TOKEN`, `LAKERA_API_KEY`, `IDP_SCIM_TOKEN`,
  `DEVHUB_MCP_TOKEN` and `PILOT_MCP_TOKEN` whenever `.env` sets them. The flows read them as
  `{{$vars.NAME}}`, so no token is written into a flow
- creates the credential **Lab Qdrant** when `QDRANT_API_KEY` is set
- creates the credential **Lab Tracing (Langfuse)** when `LANGFUSE_PUBLIC_KEY` and
  `LANGFUSE_SECRET_KEY` are set, and turns on Langfuse analytics for seeded flows that have no
  analytics setting of their own
- binds the seeded flows to the Flowise API key **Lab Agents API**. The prediction API
  (`/api/v1/prediction`) refuses calls without it. The canvas chat is not affected
- creates or updates the 33 agents

### Langflow: `builders-import`

Langflow runs in the Complete lab (`COMPOSE_PROFILES=complete`) or with the `langflow` profile.
When Langflow is not running, the seeder logs "Langflow is not running (add langflow to
COMPOSE_PROFILES); skipped." and still succeeds. Otherwise it signs in with `LANGFLOW_API_KEY` when
set, or as the Langflow superuser (the lab admin), and then:

- creates or updates the global variable `LITELLM_MASTER_KEY` (type Credential)
- creates or updates `IDP_SCIM_TOKEN`, `LAKERA_API_KEY` and `QDRANT_API_KEY` from `.env`. Until
  `.env` sets them, `IDP_SCIM_TOKEN` and `LAKERA_API_KEY` hold their own name as a placeholder: the
  SCIM and Lakera Guard components read that as "not configured" and answer with setup steps
- fills the placeholders below into the flows and creates or updates the 33 agents

When you switch from the Standard lab to the Complete lab, `builders-import` runs again and seeds
Langflow.

## Placeholders

The repository holds no secret. The importers fill these placeholders in memory, never in the files,
and never print the values.

| Placeholder | Filled from | Where |
|-------------|-------------|-------|
| `__LITELLM_MASTER_KEY__` | `LITELLM_MASTER_KEY` (required) | n8n credential Lab Model (LiteLLM) |
| `__MCP_GATEWAY_TOKEN__` | `MCP_GATEWAY_TOKEN` (required) | n8n credential MCP Gateway Bearer; Langflow MCP headers of the gateway agents |
| `__N8N_ADMIN_EMAIL__`, `__N8N_ADMIN_PASSWORD__` | `N8N_ADMIN_EMAIL`, `N8N_ADMIN_PASSWORD` (required) | n8n credential Lab Agents Chat |
| `__LAKERA_API_KEY__` | `LAKERA_API_KEY` | n8n credential Lakera Guard |
| `__IDP_SCIM_TOKEN__` | `IDP_SCIM_TOKEN` | n8n credential SCIM IdP Token |
| `__DEVHUB_MCP_TOKEN__` | `DEVHUB_MCP_TOKEN` | n8n credential DevHub Bearer Auth; Langflow DevHub agent |
| `__PILOT_MCP_TOKEN__` | `PILOT_MCP_TOKEN` | n8n credential PolicyPilot Bearer Auth; Langflow PolicyPilot agents |
| `__QDRANT_API_KEY__` | `QDRANT_API_KEY` (blank = no Qdrant authentication) | n8n credential Lab Qdrant |
| `__LAKERA_CONFIGURED__`, `__LAKERA_PROJECT_ID__` | `LAKERA_API_KEY` set or not, `LAKERA_PROJECT_ID` | n8n Lakera Guard workflows |
| `{{DOMAIN}}` | `DOMAIN` | External endpoints (SCIM, DevHub, PolicyPilot) in all three builders |

Flowise flows use no `__NAME__` placeholders: they read Flowise variables (`{{$vars.NAME}}`) and
the credentials the seeder attaches.

**DOMAIN rule (both importers).** `DOMAIN` when it is set. Otherwise `N8N_HOST` without its `n8n.`
prefix, when `N8N_HOST` starts with `n8n.`. Otherwise the placeholder stays, and the agents that
need it are flagged (n8n imports them unpublished).

**Where the values end up.** n8n stores its credentials encrypted with `N8N_ENCRYPTION_KEY`.
Flowise stores its variables in its database as plain values. Langflow keeps the gateway token in
the MCP header of each seeded gateway flow. Treat the builder databases, and every backup of them, as
secrets.

## Updates keep your edits

Every run compares what is deployed with what the repository holds.

- **n8n.** Workflows are updated by id. A workflow you changed in n8n is kept. Credentials are always
  re-synced from `.env`.
- **Flowise and Langflow.** Each seeded flow carries a marker (`labSeed`: the agent slug and a
  checksum of what was seeded). An untouched flow is updated in place when the repository version
  changes. A flow you changed in the builder is kept and named in the log. Moving nodes on the
  canvas is not a change. A flow is never seeded twice.
- **Renamed agents.** `builders_agents.json` lists the names an agent had before (`former_names`).
  The seeder finds a flow under a former name and renames it in place. An edited flow keeps its
  former name until you replace it.

To replace kept agents with the repository version (your edits are lost), run once:

```sh
docker compose run --rm -e N8N_SEED_OVERWRITE=1 n8n-import
docker compose run --rm -e SEED_OVERWRITE=1 builders-import
```

`SEED_OVERWRITE=1` also replaces flows that carry no marker, for example flows seeded by an older lab
version. To keep your own variant, duplicate the agent under a new name first: the seeders never take
over a flow with another name.

## Run the importers by hand

Run them after you change `.env`. They run inside the lab network; the builder names do not resolve
on the host.

```sh
docker compose run --rm n8n-import
docker compose run --rm builders-import
```

With 1Password references in `.env`:

```sh
op run --env-file=.env -- docker compose run --rm n8n-import
op run --env-file=.env -- docker compose run --rm builders-import
```

Expected result: the n8n log ends with `n8n-import completed.` and the builders log with
`Builders import completed.` Both exit 0.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `ERROR: LITELLM_MASTER_KEY is not set` | Run `./setup.sh`, then `docker compose up -d`. |
| `WARNING: ... is the public training default` (n8n-import) or `... is the published training default` (builders-import) | Run `./setup.sh` to generate a private value, then `docker compose up -d`. |
| `kept, changed in the builder after it was seeded` | Your edit is kept. To replace it: `docker compose run --rm -e SEED_OVERWRITE=1 builders-import`. |
| `kept, no seed marker` | The flow comes from an older lab version or was made by hand. Replace it once with `SEED_OVERWRITE=1`. |
| An n8n agent is not published | The `n8n-import` log names what it needs. Set it in `.env`, then `docker compose run --rm n8n-import`. |
| `note: ... needs DOMAIN` | Set `DOMAIN` (lab hosts) and re-run the importers. |
| `Langflow is not running ... skipped` | Expected in the Standard lab. For Langflow, set `COMPOSE_PROFILES=complete` and run `docker compose up -d`. |
| The Flowise prediction API refuses a call | Send the Flowise API key **Lab Agents API** (`Authorization: Bearer <key>`). |
| A gateway agent shows 0 tools | The gateway lists tools once at start. Run `docker compose restart mcp-gateway`. |
| An agent answers "not configured" | Set that product's keys in `.env`, then `docker compose up -d`. |

For an end-to-end check of the seeded agents, run `./scripts/doctor.sh --post-start`, then
`tests/acceptance/run.sh` (see [tests/README.md](../tests/README.md)).
