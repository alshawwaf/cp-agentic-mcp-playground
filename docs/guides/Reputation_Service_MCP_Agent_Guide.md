# Reputation Service Agents (MCP Gateway and Direct)

The Reputation Service agents look up the Check Point reputation of URLs, IP addresses, and file hashes,
and explain the classification and risk.

Two agents ship in n8n, Flowise, and Langflow: **Reputation Service Agent (MCP Gateway)** and **Reputation Service Agent (Direct)**. They have the same three tools and the same prompt. Only the path of the tool calls differs.

Every tool of these agents only reads. The agents never change your environment.

## At a glance

|  | MCP Gateway agent | Direct agent |
|---|---|---|
| Agent name | Reputation Service Agent (MCP Gateway) | Reputation Service Agent (Direct) |
| Endpoint | `http://mcp-gateway:8080/mcp`, Bearer token `MCP_GATEWAY_TOKEN` | `http://reputation-service-mcp:3007`, no token (internal `lab` network only) |
| Tools | 3 of the gateway's 190, selected | All 3 of the server |
| n8n MCP Client Tool node | `MCP Gateway` | `Reputation Service MCP` |
| Tool names the model sees in n8n | `MCP_Gateway_reputation_url` | `Reputation_Service_MCP_reputation_url` |
| n8n workflow file | `n8n/backup/workflows/reputation-service-via-gateway.json` | `n8n/backup/workflows/reputation-service-mcp-agent.json` |
| Flowise file | `integrations/flowise/reputation-service.flowdata.json` | `integrations/flowise/direct-reputation.flowdata.json` |
| Langflow file | `integrations/langflow/reputation-service.flow.json` | `integrations/langflow/direct-reputation.flow.json` |

The MCP server runs as the Compose service `reputation-service-mcp` (catalog key `reputation-service` in `mcp-gateway/catalog.yaml`).

## Tools (3)

- **Lookups:** `reputation_url`, `reputation_ip`, `reputation_file`

## Prerequisites

1. **The lab is running.** `./scripts/doctor.sh --post-start` reports `no blockers` (with 1Password references in `.env`: `op run --env-file=.env -- ./scripts/doctor.sh --post-start`)
2. **A model for `lab-chat`.** One model provider key in `.env`, or the local Ollama model (slow on a CPU). Every agent calls `lab-chat` through LiteLLM; builders never hold provider keys
3. **The Reputation Service settings in `.env`** (table below). `./setup.sh` asks for them, or edit `.env` (with `./setup.sh --1password`, `.env` holds `op://` references instead of values). Then run `docker compose up -d`: it recreates the servers whose settings changed. With 1Password references in `.env`, start every `docker compose` command in this guide with `op run --env-file=.env --` ([Secrets and 1Password](../REFERENCE.md#secrets-and-1password))
4. **Sign-in.** The lab admin for n8n, Flowise, and Langflow: `N8N_ADMIN_EMAIL` (default `admin@lab.local`) and `N8N_ADMIN_PASSWORD`

| Setting | Value |
|---|---|
| `REPUTATION_API_KEY` | A Reputation Service API key. |

## Chat with the agents

### n8n

1. Sign in to n8n and open the workflow **Reputation Service Agent (Direct)**
2. Click **Open chat** and send one of the prompts below
3. Open the **Executions** tab and the latest run. The MCP Client Tool **Reputation Service MCP** shows the tools the agent called, for example `Reputation_Service_MCP_reputation_url`
4. Repeat with **Reputation Service Agent (MCP Gateway)**. Its MCP Client Tool **MCP Gateway** selects the same three tools under **Tools to Include** and uses the credential **MCP Gateway Bearer**. The tool calls appear as `MCP_Gateway_reputation_url`

The chat trigger also has a public **Chat URL** (it ends in `/webhook/<id>/chat`). That page asks for HTTP Basic authentication: the lab admin email and password (credential **Lab Agents Chat**).

### Flowise

1. Sign in to Flowise, open **Chatflows**, and open **Reputation Service Agent (Direct)** or **Reputation Service Agent (MCP Gateway)**
2. Click the chat icon at the top right of the canvas and send a prompt
3. The **Custom MCP** node shows the endpoint under **MCP Server Config** and the three selected tools under **Available Actions**. The gateway agent sends `Authorization: Bearer {{$vars.MCP_GATEWAY_TOKEN}}`, the Flowise variable that `builders-import` keeps in sync with `.env`

### Langflow (Complete lab)

Langflow runs in the Complete lab (`COMPOSE_PROFILES=complete`), or on its own with the `langflow` profile.

1. Sign in to Langflow and open the flow **Reputation Service Agent (Direct)** or **Reputation Service Agent (MCP Gateway)**
2. Click **Playground** (Langflow's chat panel) and send a prompt
3. In the gateway flow, the **MCP Tools** node lists all 190 gateway tools under **Actions**, with only this server's three switched on

## Try these

- *What is the reputation of 8.8.8.8?*
- *Is example.com safe to visit?*
- *Check the reputation of this file hash: <SHA-256>*

## Expected result

The agent calls `reputation_ip`, `reputation_url`, or `reputation_file` and summarizes the answer: the
verdict, the classification, the risk score, and the confidence the service returns. Without
`REPUTATION_API_KEY`, every tool call fails.

## How the agents are built

| n8n node | Type | What it does |
|---|---|---|
| `When chat message received` | Chat Trigger | Starts one run per message. Shows the greeting and starter prompts. Public chat behind HTTP Basic authentication (credential **Lab Agents Chat**) |
| `Normalize input` | Edit Fields | Takes `chatInput` and `sessionId` from the chat, or from the body of a webhook call |
| `Reputation Service Agent` | AI Agent | Holds the system prompt and decides which tools to call. On an error it continues to `Friendly error` |
| `OpenAI Chat Model` | OpenAI Chat Model | Model `lab-chat` with the credential **Lab Model (LiteLLM)** (`http://litellm:4000/v1`) |
| `Conversation Memory` | Simple Memory | Keeps the recent turns of the chat session |
| `Reputation Service MCP` or `MCP Gateway` | MCP Client Tool | The three tools: direct to `http://reputation-service-mcp:3007`, or through the gateway |
| `Friendly error` | Code | Turns a failure into a plain explanation with the command that fixes it |

In Flowise, a **Tool Agent** uses the **OpenAI** chat model node (`lab-chat`, credential **Lab Model (LiteLLM)**, Base Path `http://litellm:4000/v1`), **Buffer Memory**, and one **Custom MCP** node. In Langflow, **Chat Input** feeds an **Agent** with the **OpenAI** component (`lab-chat`, OpenAI API Base `http://litellm:4000/v1`, API key from the global variable `LITELLM_MASTER_KEY`) and one **MCP Tools** node, and the answer goes to **Chat Output**. For a node-by-node walkthrough, see the [Threat Prevention deep dive](CheckPoint_Threat_Prevention_Guide.md).

**Updates.** `n8n-import` and `builders-import` re-import these agents on every deploy and keep an agent you changed. To keep your own variant, duplicate it first. `N8N_SEED_OVERWRITE=1` (n8n) or `SEED_OVERWRITE=1` (Flowise and Langflow) replaces a changed agent with the repository version; set it back to `0` after that run.

**Data handling.** Every tool result goes to the model provider behind `lab-chat`. With a cloud provider, that is an external service. Use lab data only. Never connect the lab to customer environments or load customer data unless the provider is approved for that data.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Every tool call fails | `REPUTATION_API_KEY` is blank or wrong | Set `REPUTATION_API_KEY`, then `docker compose up -d reputation-service-mcp` |
| The MCP Gateway agent answers with HTTP 401 | The token in the builder differs from `MCP_GATEWAY_TOKEN` in `.env` | n8n: `docker compose run --rm n8n-import`. Flowise and Langflow: `docker compose run --rm builders-import` |
| The MCP Gateway agent has no tools, or fails while the Direct agent works | The gateway started before the server was ready, or still uses sessions to an old server container | `docker compose restart mcp-gateway` |
| **Friendly error**: the lab model endpoint is not reachable | LiteLLM is not running | `docker compose up -d litellm` |
| **Friendly error**: LiteLLM could not serve the `lab-chat` model | No working model provider key, or the provider failed | Check the key in `.env`, then `docker compose logs litellm` |
| The hosted chat URL asks for a password | HTTP Basic authentication on the chat trigger | Sign in with the lab admin email and password |

---

*Related:* [The MCP Gateway, Explained](MCP_Gateway_Explained.md) · [Direct and MCP Gateway agents lab](MCP_Gateway_Agent_Guide.md) · [Threat Emulation](Threat_Emulation_MCP_Agent_Guide.md)
