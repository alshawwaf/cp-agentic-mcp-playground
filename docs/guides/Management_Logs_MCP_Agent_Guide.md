# Management Logs Agents (MCP Gateway and Direct)

The Management Logs agents search your connection, security, and audit logs on the Management Server and
turn them into clear findings. They build a log filter from your question, run the query, and page
through the results.

Two agents ship in n8n, Flowise, and Langflow: **Management Logs Agent (MCP Gateway)** and **Management Logs Agent (Direct)**. They have the same seven tools and the same prompt. Only the path of the tool calls differs.

Every tool of these agents only reads. The agents never change your environment.

## At a glance

|  | MCP Gateway agent | Direct agent |
|---|---|---|
| Agent name | Management Logs Agent (MCP Gateway) | Management Logs Agent (Direct) |
| Endpoint | `http://mcp-gateway:8080/mcp`, Bearer token `MCP_GATEWAY_TOKEN` | `http://mcp-management-logs:3003`, no token (internal `lab` network only) |
| Tools | 7 of the gateway's 190, selected | All 7 of the server |
| n8n MCP Client Tool node | `MCP Gateway` | `Management Logs MCP` |
| Tool names the model sees in n8n | `MCP_Gateway_management-logs__init` | `Management_Logs_MCP_management-logs__init` |
| n8n workflow file | `n8n/backup/workflows/management-logs-via-gateway.json` | `n8n/backup/workflows/management-logs-mcp-webhook-OpenAI.json` |
| Flowise file | `integrations/flowise/management-logs.flowdata.json` | `integrations/flowise/direct-logs.flowdata.json` |
| Langflow file | `integrations/langflow/management-logs.flow.json` | `integrations/langflow/direct-logs.flow.json` |

The MCP server runs as the Compose service `mcp-management-logs` (catalog key `management-logs` in `mcp-gateway/catalog.yaml`).

## Tools (7)

- **Session:** `management-logs__init`
- **Log queries:** `build_logs_query_filter`, `run_logs_query`, `get_next_query_page`
- **Context:** `management-logs__show_gateways_and_servers`, `management-logs__show_objects`, `management-logs__show_object`

## Prerequisites

1. **The lab is running.** `./scripts/doctor.sh --post-start` reports `no blockers` (with 1Password references in `.env`: `op run --env-file=.env -- ./scripts/doctor.sh --post-start`)
2. **A model for `lab-chat`.** One model provider key in `.env`, or the local Ollama model (slow on a CPU). Every agent calls `lab-chat` through LiteLLM; builders never hold provider keys
3. **The Management Logs settings in `.env`** (table below). `./setup.sh` asks for them, or edit `.env` (with `./setup.sh --1password`, `.env` holds `op://` references instead of values). Then run `docker compose up -d`: it recreates the servers whose settings changed. With 1Password references in `.env`, start every `docker compose` command in this guide with `op run --env-file=.env --` ([Secrets and 1Password](../REFERENCE.md#secrets-and-1password))
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

1. Sign in to n8n and open the workflow **Management Logs Agent (Direct)**
2. Click **Open chat** and send one of the prompts below
3. Open the **Executions** tab and the latest run. The MCP Client Tool **Management Logs MCP** shows the tools the agent called, for example `Management_Logs_MCP_management-logs__init`
4. Repeat with **Management Logs Agent (MCP Gateway)**. Its MCP Client Tool **MCP Gateway** selects the same seven tools under **Tools to Include** and uses the credential **MCP Gateway Bearer**. The tool calls appear as `MCP_Gateway_management-logs__init`

The chat trigger also has a public **Chat URL** (it ends in `/webhook/<id>/chat`). That page asks for HTTP Basic authentication: the lab admin email and password (credential **Lab Agents Chat**).

### Flowise

1. Sign in to Flowise, open **Chatflows**, and open **Management Logs Agent (Direct)** or **Management Logs Agent (MCP Gateway)**
2. Click the chat icon at the top right of the canvas and send a prompt
3. The **Custom MCP** node shows the endpoint under **MCP Server Config** and the seven selected tools under **Available Actions**. The gateway agent sends `Authorization: Bearer {{$vars.MCP_GATEWAY_TOKEN}}`, the Flowise variable that `builders-import` keeps in sync with `.env`

### Langflow (Complete lab)

Langflow runs in the Complete lab (`COMPOSE_PROFILES=complete`), or on its own with the `langflow` profile.

1. Sign in to Langflow and open the flow **Management Logs Agent (Direct)** or **Management Logs Agent (MCP Gateway)**
2. Click **Playground** (Langflow's chat panel) and send a prompt
3. In the gateway flow, the **MCP Tools** node lists all 190 gateway tools under **Actions**, with only this server's seven switched on

## Try these

- *Show the last 10 dropped connections*
- *Any preventions in the past 24 hours? Summarize by blade*
- *Who changed the policy this week?*

## Expected result

The agent calls `management-logs__init`, builds a filter (`build_logs_query_filter`), runs it
(`run_logs_query`) and fetches more pages when needed (`get_next_query_page`). Expect a short summary
with a table of the matching log entries: time, source, destination, action, blade, or the
administrator and change for audit logs. With no matching logs, the agent says so.

## How the agents are built

| n8n node | Type | What it does |
|---|---|---|
| `When chat message received` | Chat Trigger | Starts one run per message. Shows the greeting and starter prompts. Public chat behind HTTP Basic authentication (credential **Lab Agents Chat**) |
| `Normalize input` | Edit Fields | Takes `chatInput` and `sessionId` from the chat, or from the body of a webhook call |
| `Management Logs Agent` | AI Agent | Holds the system prompt and decides which tools to call. On an error it continues to `Friendly error` |
| `OpenAI Chat Model` | OpenAI Chat Model | Model `lab-chat` with the credential **Lab Model (LiteLLM)** (`http://litellm:4000/v1`) |
| `Conversation Memory` | Simple Memory | Keeps the recent turns of the chat session |
| `Management Logs MCP` or `MCP Gateway` | MCP Client Tool | The seven tools: direct to `http://mcp-management-logs:3003`, or through the gateway |
| `Friendly error` | Code | Turns a failure into a plain explanation with the command that fixes it |

In Flowise, a **Tool Agent** uses the **OpenAI** chat model node (`lab-chat`, credential **Lab Model (LiteLLM)**, Base Path `http://litellm:4000/v1`), **Buffer Memory**, and one **Custom MCP** node. In Langflow, **Chat Input** feeds an **Agent** with the **OpenAI** component (`lab-chat`, OpenAI API Base `http://litellm:4000/v1`, API key from the global variable `LITELLM_MASTER_KEY`) and one **MCP Tools** node, and the answer goes to **Chat Output**. For a node-by-node walkthrough, see the [Threat Prevention deep dive](CheckPoint_Threat_Prevention_Guide.md).

**Updates.** `n8n-import` and `builders-import` re-import these agents on every deploy and keep an agent you changed. To keep your own variant, duplicate it first. `N8N_SEED_OVERWRITE=1` (n8n) or `SEED_OVERWRITE=1` (Flowise and Langflow) replaces a changed agent with the repository version; set it back to `0` after that run.

**Data handling.** Logs hold user names, IP addresses, and traffic details, and every tool result goes to the model provider
behind `lab-chat`. With a cloud provider, that is an external service. Use lab logs only. Never connect
the lab to customer environments unless the provider is approved for that data.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Either management host or S1C URL must be provided` | No management settings in `.env` | Set `MANAGEMENT_HOST` (or `S1C_URL`) and `MANAGEMENT_API_KEY`, then `docker compose up -d mcp-management-logs` |
| `docker compose logs mcp-management-logs` shows `WARNING: MANAGEMENT_HOST is set without MANAGEMENT_API_KEY` | A host without a way to sign in is ignored | Add `MANAGEMENT_API_KEY` (or `MANAGEMENT_USERNAME` and `MANAGEMENT_PASSWORD`), then `docker compose up -d mcp-management-logs` |
| `TLS certificate verification failed` | Self-signed certificate, or the name does not match | See **Certificates** above |
| `MCP error -32001: Request timed out` | `MANAGEMENT_HOST` is unreachable from the Docker host, or replies do not route back | `./scripts/doctor.sh --preflight --online` tests reachability. See **Lab connectivity** in the [Direct and MCP Gateway agents lab](MCP_Gateway_Agent_Guide.md) |
| HTTP 429 (too many requests) from the Management Server | The Management Server limits logins; every new MCP session logs in | Wait one or two minutes. Keep one chat session per exercise |
| The MCP Gateway agent answers with HTTP 401 | The token in the builder differs from `MCP_GATEWAY_TOKEN` in `.env` | n8n: `docker compose run --rm n8n-import`. Flowise and Langflow: `docker compose run --rm builders-import` |
| The MCP Gateway agent has no tools, or fails while the Direct agent works | The gateway started before the server was ready, or still uses sessions to an old server container | `docker compose restart mcp-gateway` |
| **Friendly error**: the lab model endpoint is not reachable | LiteLLM is not running | `docker compose up -d litellm` |
| **Friendly error**: LiteLLM could not serve the `lab-chat` model | No working model provider key, or the provider failed | Check the key in `.env`, then `docker compose logs litellm` |
| The hosted chat URL asks for a password | HTTP Basic authentication on the chat trigger | Sign in with the lab admin email and password |

---

*Related:* [The MCP Gateway, Explained](MCP_Gateway_Explained.md) · [Direct and MCP Gateway agents lab](MCP_Gateway_Agent_Guide.md) · [Management](Quantum_Management_MCP_Agent_Guide.md)
