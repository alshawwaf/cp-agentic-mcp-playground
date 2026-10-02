# Documentation Agents (MCP Gateway and Direct)

The Documentation agents answer questions from Check Point documentation and SecureKnowledge, with
sources. They call one tool, `ask-checkpoint-docs`, which queries the Check Point documentation
service. They need no Management Server, so they are the quickest way to try both paths. The
acceptance tests run this pair in all three builders.

Two agents ship in n8n, Flowise, and Langflow: **Documentation Agent (MCP Gateway)** and **Documentation Agent (Direct)**. They have the same tool and the same prompt. Only the path of the tool calls differs.

The tool only reads. The agents never change your environment.

## At a glance

|  | MCP Gateway agent | Direct agent |
|---|---|---|
| Agent name | Documentation Agent (MCP Gateway) | Documentation Agent (Direct) |
| Endpoint | `http://mcp-gateway:8080/mcp`, Bearer token `MCP_GATEWAY_TOKEN` | `http://mcp-documentation:3000`, no token (internal `lab` network only) |
| Tools | 1 of the gateway's 190, selected | The server's one tool |
| n8n MCP Client Tool node | `MCP Gateway` | `Documentation MCP` |
| Tool names the model sees in n8n | `MCP_Gateway_ask-checkpoint-docs` | `Documentation_MCP_ask-checkpoint-docs` |
| n8n workflow file | `n8n/backup/workflows/documentation-via-gateway.json` | `n8n/backup/workflows/documentation-mcp-agent.json` |
| Flowise file | `integrations/flowise/documentation.flowdata.json` | `integrations/flowise/direct-documentation.flowdata.json` |
| Langflow file | `integrations/langflow/documentation.flow.json` | `integrations/langflow/direct-documentation.flow.json` |

The MCP server runs as the Compose service `mcp-documentation` (catalog key `documentation` in `mcp-gateway/catalog.yaml`).

## Tools (1)

- **Documentation:** `ask-checkpoint-docs`

`ask-checkpoint-docs` takes the question (`text`) and an optional `product` (for example `s1c`
for Smart-1 Cloud, the default, or `endpoint`).

## Prerequisites

1. **The lab is running.** `./scripts/doctor.sh --post-start` reports `no blockers` (with 1Password references in `.env`: `op run --env-file=.env -- ./scripts/doctor.sh --post-start`)
2. **A model for `lab-chat`.** One model provider key in `.env`, or the local Ollama model (slow on a CPU). Every agent calls `lab-chat` through LiteLLM; builders never hold provider keys
3. **The Documentation settings in `.env`** (table below). `./setup.sh` asks for them, or edit `.env` (with `./setup.sh --1password`, `.env` holds `op://` references instead of values). Then run `docker compose up -d`: it recreates the servers whose settings changed. With 1Password references in `.env`, start every `docker compose` command in this guide with `op run --env-file=.env --` ([Secrets and 1Password](../REFERENCE.md#secrets-and-1password))
4. **Sign-in.** The lab admin for n8n, Flowise, and Langflow: `N8N_ADMIN_EMAIL` (default `admin@lab.local`) and `N8N_ADMIN_PASSWORD`

| Setting | Value |
|---|---|
| `DOC_CLIENT_ID`, `DOC_SECRET_KEY` | Client ID and Secret Key of a Check Point portal API key (portal.checkpoint.com: **Global Settings > API Keys > New**). |
| `DOC_REGION` | Region of your Check Point portal account: `EU` (default) or `US`. |

## Chat with the agents

### n8n

1. Sign in to n8n and open the workflow **Documentation Agent (Direct)**
2. Click **Open chat** and send one of the prompts below
3. Open the **Executions** tab and the latest run. The MCP Client Tool **Documentation MCP** shows the tools the agent called, for example `Documentation_MCP_ask-checkpoint-docs`
4. Repeat with **Documentation Agent (MCP Gateway)**. Its MCP Client Tool **MCP Gateway** selects the same tool under **Tools to Include** and uses the credential **MCP Gateway Bearer**. The tool calls appear as `MCP_Gateway_ask-checkpoint-docs`

The chat trigger also has a public **Chat URL** (it ends in `/webhook/<id>/chat`). That page asks for HTTP Basic authentication: the lab admin email and password (credential **Lab Agents Chat**).

### Flowise

1. Sign in to Flowise, open **Chatflows**, and open **Documentation Agent (Direct)** or **Documentation Agent (MCP Gateway)**
2. Click the chat icon at the top right of the canvas and send a prompt
3. The **Custom MCP** node shows the endpoint under **MCP Server Config** and the selected tool under **Available Actions**. The gateway agent sends `Authorization: Bearer {{$vars.MCP_GATEWAY_TOKEN}}`, the Flowise variable that `builders-import` keeps in sync with `.env`

### Langflow (Complete lab)

Langflow runs in the Complete lab (`COMPOSE_PROFILES=complete`), or on its own with the `langflow` profile.

1. Sign in to Langflow and open the flow **Documentation Agent (Direct)** or **Documentation Agent (MCP Gateway)**
2. Click **Playground** (Langflow's chat panel) and send a prompt
3. In the gateway flow, the **MCP Tools** node lists all 190 gateway tools under **Actions**, with only `ask-checkpoint-docs` switched on

## Try these

- *How do I enable Identity Awareness on R82?*
- *Find the SecureKnowledge article about the Gaia REST API*
- *What does HTTPS Inspection need on the gateway?*

## Expected result

The agent passes your question to `ask-checkpoint-docs` (with a Check Point product when it helps) and
answers with the steps or facts the documentation gives, plus the sources. The MCP Gateway and Direct
agents give the same answer; only the tool name in the execution differs
(`MCP_Gateway_ask-checkpoint-docs` and `Documentation_MCP_ask-checkpoint-docs`).

## How the agents are built

| n8n node | Type | What it does |
|---|---|---|
| `When chat message received` | Chat Trigger | Starts one run per message. Shows the greeting and starter prompts. Public chat behind HTTP Basic authentication (credential **Lab Agents Chat**) |
| `Normalize input` | Edit Fields | Takes `chatInput` and `sessionId` from the chat, or from the body of a webhook call |
| `Documentation Agent` | AI Agent | Holds the system prompt and decides which tools to call. On an error it continues to `Friendly error` |
| `OpenAI Chat Model` | OpenAI Chat Model | Model `lab-chat` with the credential **Lab Model (LiteLLM)** (`http://litellm:4000/v1`) |
| `Conversation Memory` | Simple Memory | Keeps the recent turns of the chat session |
| `Documentation MCP` or `MCP Gateway` | MCP Client Tool | The tool: direct to `http://mcp-documentation:3000`, or through the gateway |
| `Friendly error` | Code | Turns a failure into a plain explanation with the command that fixes it |

In Flowise, a **Tool Agent** uses the **OpenAI** chat model node (`lab-chat`, credential **Lab Model (LiteLLM)**, Base Path `http://litellm:4000/v1`), **Buffer Memory**, and one **Custom MCP** node. In Langflow, **Chat Input** feeds an **Agent** with the **OpenAI** component (`lab-chat`, OpenAI API Base `http://litellm:4000/v1`, API key from the global variable `LITELLM_MASTER_KEY`) and one **MCP Tools** node, and the answer goes to **Chat Output**. For a node-by-node walkthrough, see the [Threat Prevention deep dive](CheckPoint_Threat_Prevention_Guide.md).

**Updates.** `n8n-import` and `builders-import` re-import these agents on every deploy and keep an agent you changed. To keep your own variant, duplicate it first. `N8N_SEED_OVERWRITE=1` (n8n) or `SEED_OVERWRITE=1` (Flowise and Langflow) replaces a changed agent with the repository version; set it back to `0` after that run.

**Data handling.** Every tool result goes to the model provider behind `lab-chat`. With a cloud provider, that is an external service. Use lab data only. Never connect the lab to customer environments or load customer data unless the provider is approved for that data.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Every tool call fails | `DOC_CLIENT_ID` or `DOC_SECRET_KEY` is blank or wrong, or `DOC_REGION` does not match the account | Fix the values, then `docker compose up -d mcp-documentation` |
| The MCP Gateway agent answers with HTTP 401 | The token in the builder differs from `MCP_GATEWAY_TOKEN` in `.env` | n8n: `docker compose run --rm n8n-import`. Flowise and Langflow: `docker compose run --rm builders-import` |
| The MCP Gateway agent has no tools, or fails while the Direct agent works | The gateway started before the server was ready, or still uses sessions to an old server container | `docker compose restart mcp-gateway` |
| **Friendly error**: the lab model endpoint is not reachable | LiteLLM is not running | `docker compose up -d litellm` |
| **Friendly error**: LiteLLM could not serve the `lab-chat` model | No working model provider key, or the provider failed | Check the key in `.env`, then `docker compose logs litellm` |
| The hosted chat URL asks for a password | HTTP Basic authentication on the chat trigger | Sign in with the lab admin email and password |

---

*Related:* [The MCP Gateway, Explained](MCP_Gateway_Explained.md) · [Direct and MCP Gateway agents lab](MCP_Gateway_Agent_Guide.md) · [Visible RAG](Visible_RAG.md)
