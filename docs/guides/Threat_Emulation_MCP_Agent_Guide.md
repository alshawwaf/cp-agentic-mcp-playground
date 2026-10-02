# Threat Emulation Agents (MCP Gateway and Direct)

The Threat Emulation agents check files and file hashes with the Check Point Threat Emulation cloud
service and explain the verdicts. They look up existing results by hash, submit files for emulation,
download reports, and show your API quota.

Two agents ship in n8n, Flowise, and Langflow: **Threat Emulation Agent (MCP Gateway)** and **Threat Emulation Agent (Direct)**. They have the same five tools and the same prompt. Only the path of the tool calls differs.

These agents do more than read: `upload_file` and `scan_file` send a file to the Threat Emulation cloud
service. Use test files only. Never submit customer files or files that hold confidential data.

## At a glance

|  | MCP Gateway agent | Direct agent |
|---|---|---|
| Agent name | Threat Emulation Agent (MCP Gateway) | Threat Emulation Agent (Direct) |
| Endpoint | `http://mcp-gateway:8080/mcp`, Bearer token `MCP_GATEWAY_TOKEN` | `http://threat-emulation-mcp:3004`, no token (internal `lab` network only) |
| Tools | 5 of the gateway's 190, selected | All 5 of the server |
| n8n MCP Client Tool node | `MCP Gateway` | `Threat Emulation MCP` |
| Tool names the model sees in n8n | `MCP_Gateway_upload_file` | `Threat_Emulation_MCP_upload_file` |
| n8n workflow file | `n8n/backup/workflows/threat-emulation-via-gateway.json` | `n8n/backup/workflows/threat-emulation-mcp-agent.json` |
| Flowise file | `integrations/flowise/threat-emulation.flowdata.json` | `integrations/flowise/direct-threat-emulation.flowdata.json` |
| Langflow file | `integrations/langflow/threat-emulation.flow.json` | `integrations/langflow/direct-threat-emulation.flow.json` |

The MCP server runs as the Compose service `threat-emulation-mcp` (catalog key `threat-emulation` in `mcp-gateway/catalog.yaml`).

## Tools (5)

- **Files and verdicts:** `query_file`, `upload_file`, `scan_file`, `download_report`
- **Account:** `get_quota`

## Prerequisites

1. **The lab is running.** `./scripts/doctor.sh --post-start` reports `no blockers` (with 1Password references in `.env`: `op run --env-file=.env -- ./scripts/doctor.sh --post-start`)
2. **A model for `lab-chat`.** One model provider key in `.env`, or the local Ollama model (slow on a CPU). Every agent calls `lab-chat` through LiteLLM; builders never hold provider keys
3. **The Threat Emulation settings in `.env`** (table below). `./setup.sh` asks for them, or edit `.env` (with `./setup.sh --1password`, `.env` holds `op://` references instead of values). Then run `docker compose up -d`: it recreates the servers whose settings changed. With 1Password references in `.env`, start every `docker compose` command in this guide with `op run --env-file=.env --` ([Secrets and 1Password](../REFERENCE.md#secrets-and-1password))
4. **Sign-in.** The lab admin for n8n, Flowise, and Langflow: `N8N_ADMIN_EMAIL` (default `admin@lab.local`) and `N8N_ADMIN_PASSWORD`

| Setting | Value |
|---|---|
| `TE_API_KEY` | A Threat Prevention API key for the Threat Emulation service. |

**Files to scan.** Put them in the folder `n8n/shared` next to `docker-compose.yml` (Docker creates it at
the first start). The server sees that folder read-only as `/data/shared` and reads only regular files
under `TE_ALLOWED_DIRS` (`/data/shared` in `docker-compose.yml`). A plain file name is resolved against
`/data/shared`. Symbolic links and `..` are resolved first, so they cannot reach other folders.

## Chat with the agents

### n8n

1. Sign in to n8n and open the workflow **Threat Emulation Agent (Direct)**
2. Click **Open chat** and send one of the prompts below
3. Open the **Executions** tab and the latest run. The MCP Client Tool **Threat Emulation MCP** shows the tools the agent called, for example `Threat_Emulation_MCP_upload_file`
4. Repeat with **Threat Emulation Agent (MCP Gateway)**. Its MCP Client Tool **MCP Gateway** selects the same five tools under **Tools to Include** and uses the credential **MCP Gateway Bearer**. The tool calls appear as `MCP_Gateway_upload_file`

The chat trigger also has a public **Chat URL** (it ends in `/webhook/<id>/chat`). That page asks for HTTP Basic authentication: the lab admin email and password (credential **Lab Agents Chat**).

### Flowise

1. Sign in to Flowise, open **Chatflows**, and open **Threat Emulation Agent (Direct)** or **Threat Emulation Agent (MCP Gateway)**
2. Click the chat icon at the top right of the canvas and send a prompt
3. The **Custom MCP** node shows the endpoint under **MCP Server Config** and the five selected tools under **Available Actions**. The gateway agent sends `Authorization: Bearer {{$vars.MCP_GATEWAY_TOKEN}}`, the Flowise variable that `builders-import` keeps in sync with `.env`

### Langflow (Complete lab)

Langflow runs in the Complete lab (`COMPOSE_PROFILES=complete`), or on its own with the `langflow` profile.

1. Sign in to Langflow and open the flow **Threat Emulation Agent (Direct)** or **Threat Emulation Agent (MCP Gateway)**
2. Click **Playground** (Langflow's chat panel) and send a prompt
3. In the gateway flow, the **MCP Tools** node lists all 190 gateway tools under **Actions**, with only this server's five switched on

## Try these

- *How much Threat Emulation quota is left?*
- *Check this SHA-256 hash for a verdict: <hash>*
- *Run a full scan of the file <file name> in the shared folder*

## Expected result

For a hash, the agent calls `query_file` and reports the verdict (for example benign or malicious) with
the details the service returns. For a file, `scan_file` uploads it if needed and waits up to 30 seconds
for the result. If the emulation takes longer, the tool reports that it is still processing: ask again
later. `get_quota` shows the remaining quota. Without `TE_API_KEY`, every tool call fails.

## How the agents are built

| n8n node | Type | What it does |
|---|---|---|
| `When chat message received` | Chat Trigger | Starts one run per message. Shows the greeting and starter prompts. Public chat behind HTTP Basic authentication (credential **Lab Agents Chat**) |
| `Normalize input` | Edit Fields | Takes `chatInput` and `sessionId` from the chat, or from the body of a webhook call |
| `Threat Emulation Agent` | AI Agent | Holds the system prompt and decides which tools to call. On an error it continues to `Friendly error` |
| `OpenAI Chat Model` | OpenAI Chat Model | Model `lab-chat` with the credential **Lab Model (LiteLLM)** (`http://litellm:4000/v1`) |
| `Conversation Memory` | Simple Memory | Keeps the recent turns of the chat session |
| `Threat Emulation MCP` or `MCP Gateway` | MCP Client Tool | The five tools: direct to `http://threat-emulation-mcp:3004`, or through the gateway |
| `Friendly error` | Code | Turns a failure into a plain explanation with the command that fixes it |

In Flowise, a **Tool Agent** uses the **OpenAI** chat model node (`lab-chat`, credential **Lab Model (LiteLLM)**, Base Path `http://litellm:4000/v1`), **Buffer Memory**, and one **Custom MCP** node. In Langflow, **Chat Input** feeds an **Agent** with the **OpenAI** component (`lab-chat`, OpenAI API Base `http://litellm:4000/v1`, API key from the global variable `LITELLM_MASTER_KEY`) and one **MCP Tools** node, and the answer goes to **Chat Output**. For a node-by-node walkthrough, see the [Threat Prevention deep dive](CheckPoint_Threat_Prevention_Guide.md).

**Updates.** `n8n-import` and `builders-import` re-import these agents on every deploy and keep an agent you changed. To keep your own variant, duplicate it first. `N8N_SEED_OVERWRITE=1` (n8n) or `SEED_OVERWRITE=1` (Flowise and Langflow) replaces a changed agent with the repository version; set it back to `0` after that run.

**Data handling.** Files you scan go to the Threat Emulation cloud service, and every tool result goes to the model provider
behind `lab-chat`. Use test files and lab data only.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Access denied: <path> is not a file under the allowed directories (/data/shared)` | The file is outside `n8n/shared`, or is not a regular file | Copy the file into `n8n/shared` and use its name |
| Every tool call fails | `TE_API_KEY` is blank or wrong | Set `TE_API_KEY`, then `docker compose up -d threat-emulation-mcp` |
| The MCP Gateway agent answers with HTTP 401 | The token in the builder differs from `MCP_GATEWAY_TOKEN` in `.env` | n8n: `docker compose run --rm n8n-import`. Flowise and Langflow: `docker compose run --rm builders-import` |
| The MCP Gateway agent has no tools, or fails while the Direct agent works | The gateway started before the server was ready, or still uses sessions to an old server container | `docker compose restart mcp-gateway` |
| **Friendly error**: the lab model endpoint is not reachable | LiteLLM is not running | `docker compose up -d litellm` |
| **Friendly error**: LiteLLM could not serve the `lab-chat` model | No working model provider key, or the provider failed | Check the key in `.env`, then `docker compose logs litellm` |
| The hosted chat URL asks for a password | HTTP Basic authentication on the chat trigger | Sign in with the lab admin email and password |

---

*Related:* [The MCP Gateway, Explained](MCP_Gateway_Explained.md) · [Direct and MCP Gateway agents lab](MCP_Gateway_Agent_Guide.md) · [Reputation Service](Reputation_Service_MCP_Agent_Guide.md)
