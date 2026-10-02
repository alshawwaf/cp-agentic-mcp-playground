# Threat Prevention Agents (MCP Gateway and Direct)

The Threat Prevention agents read your Threat Prevention policy on the Management Server: layers, rules,
exceptions, profiles, IPS protections, indicators, and IOC feeds. They also check whether a CVE is
covered by an IPS protection.

Two agents ship in n8n, Flowise, and Langflow: **Threat Prevention Agent (MCP Gateway)** and **Threat Prevention Agent (Direct)**. They have the same 25 tools and the same prompt. Only the path of the tool calls differs.

Every tool of these agents only reads. The agents never create, change, or install anything. When you ask
for a change, they draft the profile, exception, or indicator change for an administrator to apply in
SmartConsole.

## At a glance

|  | MCP Gateway agent | Direct agent |
|---|---|---|
| Agent name | Threat Prevention Agent (MCP Gateway) | Threat Prevention Agent (Direct) |
| Endpoint | `http://mcp-gateway:8080/mcp`, Bearer token `MCP_GATEWAY_TOKEN` | `http://threat-prevention-mcp:3005`, no token (internal `lab` network only) |
| Tools | 25 of the gateway's 190, selected | All 25 of the server |
| n8n MCP Client Tool node | `MCP Gateway` | `Threat Prevention MCP` |
| Tool names the model sees in n8n | `MCP_Gateway_threat-prevention__init` | `Threat_Prevention_MCP_threat-prevention__init` |
| n8n workflow file | `n8n/backup/workflows/threat-prevention-via-gateway.json` | `n8n/backup/workflows/threat-prevention-mcp-agent.json` |
| Flowise file | `integrations/flowise/threat-prevention.flowdata.json` | `integrations/flowise/direct-threat-prevention.flowdata.json` |
| Langflow file | `integrations/langflow/threat-prevention.flow.json` | `integrations/langflow/direct-threat-prevention.flow.json` |

The MCP server runs as the Compose service `threat-prevention-mcp` (catalog key `threat-prevention` in `mcp-gateway/catalog.yaml`).

## Tools (25)

- **Session:** `threat-prevention__init`
- **Policy:** `show_threat_layers`, `show_threat_layer`, `show_threat_rulebase`, `show_threat_rule`, `show_threat_rule_exception_rulebase`, `show_exception_groups`, `show_exception_group`
- **Profiles and settings:** `show_threat_profiles`, `show_threat_profile`, `show_threat_advanced_settings`
- **IPS protections:** `show_threat_protections`, `show_threat_protection`, `show_ips_protection_extended_attributes`, `show_ips_protection_extended_attribute`, `show_ips_status`, `show_ips_update_schedule`, `check_cve_protection`
- **Indicators and IOC feeds:** `show_threat_indicators`, `show_threat_indicator`, `show_threat_ioc_feeds`, `show_threat_ioc_feed`
- **Context:** `threat-prevention__show_gateways_and_servers`, `threat-prevention__show_objects`, `threat-prevention__show_object`

## Prerequisites

1. **The lab is running.** `./scripts/doctor.sh --post-start` reports `no blockers` (with 1Password references in `.env`: `op run --env-file=.env -- ./scripts/doctor.sh --post-start`)
2. **A model for `lab-chat`.** One model provider key in `.env`, or the local Ollama model (slow on a CPU). Every agent calls `lab-chat` through LiteLLM; builders never hold provider keys
3. **The Threat Prevention settings in `.env`** (table below). `./setup.sh` asks for them, or edit `.env` (with `./setup.sh --1password`, `.env` holds `op://` references instead of values). Then run `docker compose up -d`: it recreates the servers whose settings changed. With 1Password references in `.env`, start every `docker compose` command in this guide with `op run --env-file=.env --` ([Secrets and 1Password](../REFERENCE.md#secrets-and-1password))
4. **Sign-in.** The lab admin for n8n, Flowise, and Langflow: `N8N_ADMIN_EMAIL` (default `admin@lab.local`) and `N8N_ADMIN_PASSWORD`

| Setting | Value |
|---|---|
| `MANAGEMENT_HOST` | Host name or IP address of the Security Management Server or Multi-Domain Server, without `https://`. It must be reachable from the Docker host. |
| `S1C_URL` | Instead of `MANAGEMENT_HOST`, for Smart-1 Cloud: the Web API URL of your tenant, without `/login` (Smart-1 Cloud portal: **Settings > API & SmartConsole**). |
| `MANAGEMENT_API_KEY` | API key of a management administrator. On-premises: SmartConsole, **Manage & Settings > Permissions & Administrators > Administrators**, Authentication Method **API Key**. Smart-1 Cloud accepts an API key only. |
| `MANAGEMENT_USERNAME`, `MANAGEMENT_PASSWORD` | Optional, on-premises only: a user name and password instead of the API key. Set both or neither. |
| `MANAGEMENT_PORT` | Optional. Management API port of an on-premises server. Default `443`. |
| `MANAGEMENT_CA_CERT`, `MANAGEMENT_TLS_SERVERNAME` | Optional. For a self-signed certificate, see **Certificates** below. |

### Certificates

The MCP servers always verify the Management Server's TLS certificate. If it is self-signed:

1. Save the certificate as `certs/sms.pem` and confirm its SHA-256 fingerprint with the server's
   administrator. The commands are in [`certs/README.md`](../../certs/README.md)
2. Set `MANAGEMENT_CA_CERT=/certs/sms.pem` in `.env`. The `certs` folder is mounted read-only at
   `/certs` in the MCP server containers
3. If you connect by IP address and the certificate names a host, set `MANAGEMENT_TLS_SERVERNAME` to
   that host name
4. Recreate the servers: `docker compose up -d`

## Chat with the agents

### n8n

1. Sign in to n8n and open the workflow **Threat Prevention Agent (Direct)**
2. Click **Open chat** and send one of the prompts below
3. Open the **Executions** tab and the latest run. The MCP Client Tool **Threat Prevention MCP** shows the tools the agent called, for example `Threat_Prevention_MCP_threat-prevention__init`
4. Repeat with **Threat Prevention Agent (MCP Gateway)**. Its MCP Client Tool **MCP Gateway** selects the same 25 tools under **Tools to Include** and uses the credential **MCP Gateway Bearer**. The tool calls appear as `MCP_Gateway_threat-prevention__init`

The chat trigger also has a public **Chat URL** (it ends in `/webhook/<id>/chat`). That page asks for HTTP Basic authentication: the lab admin email and password (credential **Lab Agents Chat**).

### Flowise

1. Sign in to Flowise, open **Chatflows**, and open **Threat Prevention Agent (Direct)** or **Threat Prevention Agent (MCP Gateway)**
2. Click the chat icon at the top right of the canvas and send a prompt
3. The **Custom MCP** node shows the endpoint under **MCP Server Config** and the 25 selected tools under **Available Actions**. The gateway agent sends `Authorization: Bearer {{$vars.MCP_GATEWAY_TOKEN}}`, the Flowise variable that `builders-import` keeps in sync with `.env`

### Langflow (Complete lab)

Langflow runs in the Complete lab (`COMPOSE_PROFILES=complete`), or on its own with the `langflow` profile.

1. Sign in to Langflow and open the flow **Threat Prevention Agent (Direct)** or **Threat Prevention Agent (MCP Gateway)**
2. Click **Playground** (Langflow's chat panel) and send a prompt
3. In the gateway flow, the **MCP Tools** node lists all 190 gateway tools under **Actions**, with only this server's 25 switched on

## Try these

- *List the Threat Prevention profiles*
- *Which profile is strictest and what does it activate?*
- *Is CVE-2021-44228 covered by an IPS protection?*
- *When is the next IPS update scheduled?*

## Expected result

The agent calls `threat-prevention__init`, then the read tools it needs. Expect a table of profiles, a
comparison of what each profile activates, or a CVE answer from `check_cve_protection` that names the
matching IPS protections. Without management settings, the tool calls fail and the agent reports the
error.

## How the agents are built

| n8n node | Type | What it does |
|---|---|---|
| `When chat message received` | Chat Trigger | Starts one run per message. Shows the greeting and starter prompts. Public chat behind HTTP Basic authentication (credential **Lab Agents Chat**) |
| `Normalize input` | Edit Fields | Takes `chatInput` and `sessionId` from the chat, or from the body of a webhook call |
| `Threat Prevention Agent` | AI Agent | Holds the system prompt and decides which tools to call. On an error it continues to `Friendly error` |
| `OpenAI Chat Model` | OpenAI Chat Model | Model `lab-chat` with the credential **Lab Model (LiteLLM)** (`http://litellm:4000/v1`) |
| `Conversation Memory` | Simple Memory | Keeps the recent turns of the chat session |
| `Threat Prevention MCP` or `MCP Gateway` | MCP Client Tool | The 25 tools: direct to `http://threat-prevention-mcp:3005`, or through the gateway |
| `Friendly error` | Code | Turns a failure into a plain explanation with the command that fixes it |

In Flowise, a **Tool Agent** uses the **OpenAI** chat model node (`lab-chat`, credential **Lab Model (LiteLLM)**, Base Path `http://litellm:4000/v1`), **Buffer Memory**, and one **Custom MCP** node. In Langflow, **Chat Input** feeds an **Agent** with the **OpenAI** component (`lab-chat`, OpenAI API Base `http://litellm:4000/v1`, API key from the global variable `LITELLM_MASTER_KEY`) and one **MCP Tools** node, and the answer goes to **Chat Output**. For a node-by-node walkthrough, see the [Threat Prevention deep dive](CheckPoint_Threat_Prevention_Guide.md).

**Updates.** `n8n-import` and `builders-import` re-import these agents on every deploy and keep an agent you changed. To keep your own variant, duplicate it first. `N8N_SEED_OVERWRITE=1` (n8n) or `SEED_OVERWRITE=1` (Flowise and Langflow) replaces a changed agent with the repository version; set it back to `0` after that run.

**Data handling.** Every tool result goes to the model provider behind `lab-chat`. With a cloud provider, that is an external service. Use lab data only. Never connect the lab to customer environments or load customer data unless the provider is approved for that data.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Either management host or S1C URL must be provided` | No management settings in `.env` | Set `MANAGEMENT_HOST` (or `S1C_URL`) and `MANAGEMENT_API_KEY`, then `docker compose up -d threat-prevention-mcp` |
| `docker compose logs threat-prevention-mcp` shows `WARNING: MANAGEMENT_HOST is set without MANAGEMENT_API_KEY` | A host without a way to sign in is ignored | Add `MANAGEMENT_API_KEY` (or `MANAGEMENT_USERNAME` and `MANAGEMENT_PASSWORD`), then `docker compose up -d threat-prevention-mcp` |
| `TLS certificate verification failed` | Self-signed certificate, or the name does not match | See **Certificates** above |
| `MCP error -32001: Request timed out` | `MANAGEMENT_HOST` is unreachable from the Docker host, or replies do not route back | `./scripts/doctor.sh --preflight --online` tests reachability. See **Lab connectivity** in the [Direct and MCP Gateway agents lab](MCP_Gateway_Agent_Guide.md) |
| HTTP 429 (too many requests) from the Management Server | The Management Server limits logins; every new MCP session logs in | Wait one or two minutes. Keep one chat session per exercise |
| The MCP Gateway agent answers with HTTP 401 | The token in the builder differs from `MCP_GATEWAY_TOKEN` in `.env` | n8n: `docker compose run --rm n8n-import`. Flowise and Langflow: `docker compose run --rm builders-import` |
| The MCP Gateway agent has no tools, or fails while the Direct agent works | The gateway started before the server was ready, or still uses sessions to an old server container | `docker compose restart mcp-gateway` |
| **Friendly error**: the lab model endpoint is not reachable | LiteLLM is not running | `docker compose up -d litellm` |
| **Friendly error**: LiteLLM could not serve the `lab-chat` model | No working model provider key, or the provider failed | Check the key in `.env`, then `docker compose logs litellm` |
| The hosted chat URL asks for a password | HTTP Basic authentication on the chat trigger | Sign in with the lab admin email and password |

---

*Related:* [The MCP Gateway, Explained](MCP_Gateway_Explained.md) · [Direct and MCP Gateway agents lab](MCP_Gateway_Agent_Guide.md) · [Threat Prevention deep dive](CheckPoint_Threat_Prevention_Guide.md)
