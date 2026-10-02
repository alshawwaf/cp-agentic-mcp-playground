# CPInfo Analysis Agents (MCP Gateway and Direct)

The CPInfo Analysis agents analyze CPInfo diagnostic files: system details, licenses, health, crashes,
performance, and network configuration. They point at anomalies worth a closer look and suggest the next
diagnostic steps. The server reads files only; it needs no Check Point credentials.

Two agents ship in n8n, Flowise, and Langflow: **CPInfo Analysis Agent (MCP Gateway)** and **CPInfo Analysis Agent (Direct)**. They have the same 12 tools and the same prompt. Only the path of the tool calls differs.

Every tool of these agents only reads. The agents never change a file or your environment.

## At a glance

|  | MCP Gateway agent | Direct agent |
|---|---|---|
| Agent name | CPInfo Analysis Agent (MCP Gateway) | CPInfo Analysis Agent (Direct) |
| Endpoint | `http://mcp-gateway:8080/mcp`, Bearer token `MCP_GATEWAY_TOKEN` | `http://cpinfo-analysis-mcp:3012`, no token (internal `lab` network only) |
| Tools | 12 of the gateway's 190, selected | All 12 of the server |
| n8n MCP Client Tool node | `MCP Gateway` | `CPInfo Analysis MCP` |
| Tool names the model sees in n8n | `MCP_Gateway_check_initialization_status` | `CPInfo_Analysis_MCP_check_initialization_status` |
| n8n workflow file | `n8n/backup/workflows/cpinfo-analysis-via-gateway.json` | `n8n/backup/workflows/cp-cpinfo-analysis-mcp-agent.json` |
| Flowise file | `integrations/flowise/cpinfo-analysis.flowdata.json` | `integrations/flowise/direct-cpinfo.flowdata.json` |
| Langflow file | `integrations/langflow/cpinfo-analysis.flow.json` | `integrations/langflow/direct-cpinfo.flow.json` |

The MCP server runs as the Compose service `cpinfo-analysis-mcp` (catalog key `cpinfo-analysis` in `mcp-gateway/catalog.yaml`).

## Tools (12)

- **Files:** `check_initialization_status`, `browse_sections_by_category`, `read_section_content`, `smart_content_search`
- **Analysis:** `analyze_cpinfo_overview`, `comprehensive_health_analysis`, `extract_system_details`, `extract_license_information`, `extract_network_config`, `analyze_performance_metrics`, `audit_security_settings`, `detect_system_crashes`

## Prerequisites

1. **The lab is running.** `./scripts/doctor.sh --post-start` reports `no blockers` (with 1Password references in `.env`: `op run --env-file=.env -- ./scripts/doctor.sh --post-start`)
2. **A model for `lab-chat`.** One model provider key in `.env`, or the local Ollama model (slow on a CPU). Every agent calls `lab-chat` through LiteLLM; builders never hold provider keys
3. **A CPInfo file** (table below). The server needs no Check Point credentials, and `./setup.sh` asks nothing for it. With 1Password references in `.env`, start every `docker compose` command in this guide with `op run --env-file=.env --` ([Secrets and 1Password](../REFERENCE.md#secrets-and-1password))
4. **Sign-in.** The lab admin for n8n, Flowise, and Langflow: `N8N_ADMIN_EMAIL` (default `admin@lab.local`) and `N8N_ADMIN_PASSWORD`

| Setting | Value |
|---|---|
| A CPInfo file | Copy it into the folder `n8n/shared` next to `docker-compose.yml` (Docker creates it at the first start). The server sees that folder as `/data/cpinfo`. No restart is needed. |
| `CPINFO_LOG_LEVEL` | Optional. Log level of the server. Default `info`. |

The server opens only regular files under `/data/cpinfo` (its `CPINFO_ALLOWED_DIRS` default). A plain file
name is resolved against that folder. Symbolic links and `..` are resolved first, so they cannot reach
other folders.

## Chat with the agents

### n8n

1. Sign in to n8n and open the workflow **CPInfo Analysis Agent (Direct)**
2. Click **Open chat** and send one of the prompts below
3. Open the **Executions** tab and the latest run. The MCP Client Tool **CPInfo Analysis MCP** shows the tools the agent called, for example `CPInfo_Analysis_MCP_check_initialization_status`
4. Repeat with **CPInfo Analysis Agent (MCP Gateway)**. Its MCP Client Tool **MCP Gateway** selects the same 12 tools under **Tools to Include** and uses the credential **MCP Gateway Bearer**. The tool calls appear as `MCP_Gateway_check_initialization_status`

The chat trigger also has a public **Chat URL** (it ends in `/webhook/<id>/chat`). That page asks for HTTP Basic authentication: the lab admin email and password (credential **Lab Agents Chat**).

### Flowise

1. Sign in to Flowise, open **Chatflows**, and open **CPInfo Analysis Agent (Direct)** or **CPInfo Analysis Agent (MCP Gateway)**
2. Click the chat icon at the top right of the canvas and send a prompt
3. The **Custom MCP** node shows the endpoint under **MCP Server Config** and the 12 selected tools under **Available Actions**. The gateway agent sends `Authorization: Bearer {{$vars.MCP_GATEWAY_TOKEN}}`, the Flowise variable that `builders-import` keeps in sync with `.env`

### Langflow (Complete lab)

Langflow runs in the Complete lab (`COMPOSE_PROFILES=complete`), or on its own with the `langflow` profile.

1. Sign in to Langflow and open the flow **CPInfo Analysis Agent (Direct)** or **CPInfo Analysis Agent (MCP Gateway)**
2. Click **Playground** (Langflow's chat panel) and send a prompt
3. In the gateway flow, the **MCP Tools** node lists all 190 gateway tools under **Actions**, with only this server's 12 switched on

## Try these

- *Which CPInfo files are loaded?*
- *Summarize the health of the loaded CPInfo file*
- *Analyze <file name> and list the installed licenses*
- *Did the system crash? Show the evidence*

## Expected result

The agent checks which files are loaded (`check_initialization_status`), then runs the analysis tools:
an overview, a health analysis, license and network details, or a search in the file's sections.
Expect a short summary with the findings that need attention and the next steps.

## How the agents are built

| n8n node | Type | What it does |
|---|---|---|
| `When chat message received` | Chat Trigger | Starts one run per message. Shows the greeting and starter prompts. Public chat behind HTTP Basic authentication (credential **Lab Agents Chat**) |
| `Normalize input` | Edit Fields | Takes `chatInput` and `sessionId` from the chat, or from the body of a webhook call |
| `CPInfo Analysis Agent` | AI Agent | Holds the system prompt and decides which tools to call. On an error it continues to `Friendly error` |
| `OpenAI Chat Model` | OpenAI Chat Model | Model `lab-chat` with the credential **Lab Model (LiteLLM)** (`http://litellm:4000/v1`) |
| `Conversation Memory` | Simple Memory | Keeps the recent turns of the chat session |
| `CPInfo Analysis MCP` or `MCP Gateway` | MCP Client Tool | The 12 tools: direct to `http://cpinfo-analysis-mcp:3012`, or through the gateway |
| `Friendly error` | Code | Turns a failure into a plain explanation with the command that fixes it |

In Flowise, a **Tool Agent** uses the **OpenAI** chat model node (`lab-chat`, credential **Lab Model (LiteLLM)**, Base Path `http://litellm:4000/v1`), **Buffer Memory**, and one **Custom MCP** node. In Langflow, **Chat Input** feeds an **Agent** with the **OpenAI** component (`lab-chat`, OpenAI API Base `http://litellm:4000/v1`, API key from the global variable `LITELLM_MASTER_KEY`) and one **MCP Tools** node, and the answer goes to **Chat Output**. For a node-by-node walkthrough, see the [Threat Prevention deep dive](CheckPoint_Threat_Prevention_Guide.md).

**Updates.** `n8n-import` and `builders-import` re-import these agents on every deploy and keep an agent you changed. To keep your own variant, duplicate it first. `N8N_SEED_OVERWRITE=1` (n8n) or `SEED_OVERWRITE=1` (Flowise and Langflow) replaces a changed agent with the repository version; set it back to `0` after that run.

**Data handling.** CPInfo files hold the full configuration of a system. A CPInfo file from a customer system is
confidential customer data. Every tool result goes to the model provider behind `lab-chat`, which may
be an external service. Use CPInfo files from lab systems only.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Access denied: <path> is not a file under the allowed cpinfo directories (/data/cpinfo)` | The file is outside `n8n/shared`, or is not a regular file | Copy the file into `n8n/shared` and use its name |
| The MCP Gateway agent answers with HTTP 401 | The token in the builder differs from `MCP_GATEWAY_TOKEN` in `.env` | n8n: `docker compose run --rm n8n-import`. Flowise and Langflow: `docker compose run --rm builders-import` |
| The MCP Gateway agent has no tools, or fails while the Direct agent works | The gateway started before the server was ready, or still uses sessions to an old server container | `docker compose restart mcp-gateway` |
| **Friendly error**: the lab model endpoint is not reachable | LiteLLM is not running | `docker compose up -d litellm` |
| **Friendly error**: LiteLLM could not serve the `lab-chat` model | No working model provider key, or the provider failed | Check the key in `.env`, then `docker compose logs litellm` |
| The hosted chat URL asks for a password | HTTP Basic authentication on the chat trigger | Sign in with the lab admin email and password |

---

*Related:* [The MCP Gateway, Explained](MCP_Gateway_Explained.md) · [Direct and MCP Gateway agents lab](MCP_Gateway_Agent_Guide.md) · [Gateway CLI](Quantum_Gateway_CLI_MCP_Agent_Guide.md)
