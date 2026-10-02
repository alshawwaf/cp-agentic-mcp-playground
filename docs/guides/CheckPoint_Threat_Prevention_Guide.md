# Threat Prevention Agent: Node-by-Node Deep Dive

This guide takes one seeded agent apart: the n8n workflow **Threat Prevention Agent (Direct)** and its
twin **Threat Prevention Agent (MCP Gateway)**. Every per-product agent in the lab follows the same
pattern. Only the agent name, the system prompt, and the MCP node differ.

To set up and use the agents, start with the
[Threat Prevention agents guide](Threat_Prevention_MCP_Agent_Guide.md). Come back here to see how they
work inside.

---

## The workflow at a glance

```
When chat message received --> Normalize input --> Threat Prevention Agent --> (answer)
                                                        |   on error --> Friendly error --> (explanation)
                                                        |
                                                        +-- model:  OpenAI Chat Model
                                                        +-- memory: Conversation Memory
                                                        +-- tools:  Threat Prevention MCP   (gateway twin: MCP Gateway)
```

The workflow files are `n8n/backup/workflows/threat-prevention-mcp-agent.json` (Direct) and
`n8n/backup/workflows/threat-prevention-via-gateway.json` (MCP Gateway). Two sticky notes on the
canvas, **Banner** and **Guide**, repeat the starter prompts and the prerequisites.

---

## 1. `When chat message received` (Chat Trigger)

- **Type:** `@n8n/n8n-nodes-langchain.chatTrigger`, version 1.1
- **Public chat:** on. The hosted chat page and the webhook ask for HTTP Basic authentication with the
  credential **Lab Agents Chat**. `n8n-import` fills it with the lab admin email and password
  (`N8N_ADMIN_EMAIL`, `N8N_ADMIN_PASSWORD`). **Open chat** in the editor needs no extra sign-in
- **Response mode:** last node. The chat shows the agent's answer, or the text of `Friendly error`
- **Chat page:** title `Threat Prevention Agent (Direct)`, subtitle
  `Threat Prevention profiles, protections, and exceptions, read-only.`, and a greeting with three
  starter prompts

## 2. `Normalize input` (Edit Fields)

Sets two fields so the agent works the same from the chat page and from a webhook call:

| Field | Value |
|---|---|
| `chatInput` | `{{ $json?.chatInput \|\| $json.body.chatInput }}` |
| `sessionId` | `{{ $json?.sessionId \|\| $json.body.sessionId }}` |

## 3. `Threat Prevention Agent` (AI Agent)

- **Type:** `@n8n/n8n-nodes-langchain.agent`, version 3.1
- **Source for prompt:** defined below, with the text `{{ $json.chatInput }}`
- **On error:** continues on the error output, which leads to `Friendly error`. A failed tool or model
  call gives a plain explanation instead of an HTTP 500
- **System message**, in sections:
  - *What you can do:* call `threat-prevention__init` once per conversation to sign in to the
    Management Server; read layers, rules, exceptions, profiles, IPS protections, indicators, and IOC
    feeds; check CVE coverage with `check_cve_protection`
  - *How to work:* use only the tools exposed to it, never invent tool names, read before answering,
    report a failed call in one line with the next step
  - *Read-only:* never claim a change; draft the change for an administrator to apply in SmartConsole
  - *Gateway* (MCP Gateway twin only): the tools arrive through the lab's MCP Gateway, scoped to the
    Threat Prevention server's 25 tools; for another product, name the agent to use
  - *Output style:* concise Markdown, small tables, no raw JSON unless asked, at most about 15 items of
    a long list with the count of the rest

## 4. `OpenAI Chat Model`

- **Type:** `@n8n/n8n-nodes-langchain.lmChatOpenAi`, version 1.2
- **Model:** `lab-chat`, set by ID
- **Credential:** **Lab Model (LiteLLM)**: base URL `http://litellm:4000/v1`, and an API key that
  `n8n-import` fills from `LITELLM_MASTER_KEY`

LiteLLM forwards `lab-chat` to the provider that `.env` selects (`LAB_MODEL_PROVIDER`). To change the
provider, edit `.env` and run `docker compose up -d litellm` (with 1Password references in `.env`:
`op run --env-file=.env -- docker compose up -d litellm`). Do not add provider keys to n8n: no
builder holds one. LiteLLM also sends each model call to Langfuse when the Langfuse keys are set.

## 5. `Conversation Memory` (Simple Memory)

- **Type:** `@n8n/n8n-nodes-langchain.memoryBufferWindow`, version 1.3, default settings
- Keeps the recent turns of each chat session, so you can ask follow-up questions such as *Which of
  those protections are in Detect mode?*

## 6. `Threat Prevention MCP` (MCP Client Tool)

- **Type:** `@n8n/n8n-nodes-langchain.mcpClientTool`, version 1.2
- **Direct agent:** node `Threat Prevention MCP`, endpoint `http://threat-prevention-mcp:3005`, no
  authentication. It binds all 25 tools of the server
- **MCP Gateway agent:** node `MCP Gateway`, endpoint `http://mcp-gateway:8080/mcp`, authentication
  **Bearer Auth** with the credential **MCP Gateway Bearer** (filled from `MCP_GATEWAY_TOKEN`), and
  **Tools to Include** set to **Selected** with the same 25 tools
- **Tool names:** n8n prefixes each tool with the node name, so the model sees
  `Threat_Prevention_MCP_check_cve_protection` (Direct) or `MCP_Gateway_check_cve_protection` (MCP
  Gateway). The tool list is in the [Threat Prevention agents guide](Threat_Prevention_MCP_Agent_Guide.md#tools-25)

## 7. `Friendly error` (Code)

Runs once for each failed item. It reads the error message and adds the likely cause and fix:

| The error comes from | The explanation says |
|---|---|
| The MCP node (Direct) | The Threat Prevention MCP server is not reachable: `docker compose up -d threat-prevention-mcp` |
| The MCP node (MCP Gateway) | Check that `mcp-gateway` runs (`docker compose ps mcp-gateway`); `MCP_GATEWAY_TOKEN` and the **MCP Gateway Bearer** credential may differ: `docker compose run --rm n8n-import` |
| The model node | The **Lab Model (LiteLLM)** credential may be missing: `docker compose run --rm n8n-import` |
| A connection error or timeout | LiteLLM is not reachable: `docker compose up -d litellm` |
| HTTP 401 or 403 | LiteLLM rejected the key: `docker compose run --rm n8n-import` re-syncs it from `LITELLM_MASTER_KEY` |
| HTTP 429, 5xx, model, or quota errors | LiteLLM could not serve `lab-chat`: check the provider key in `.env` and `docker compose logs litellm` |

Every explanation starts with `I could not complete this request.`, says that the agent only reads
(so nothing was changed), and shows the original error after `Reason:`.

---

## How one request flows

1. You ask: *Is CVE-2021-44228 covered by an IPS protection?*
2. `Normalize input` passes `chatInput` and `sessionId` to the agent
3. The agent sends the system message, the conversation so far, and the 25 tool definitions to
   `lab-chat`
4. The model calls `threat-prevention__init`, then `check_cve_protection`
5. The MCP server calls the Management API on `MANAGEMENT_HOST` and returns the result. On the gateway
   path the call passes through `http://mcp-gateway:8080/mcp` first
6. The model summarizes the result. The chat shows the answer, and the **Executions** tab shows every
   tool call

---

## The same agent in Flowise and Langflow

| Role | n8n | Flowise | Langflow |
|---|---|---|---|
| Chat | `When chat message received` | The chat on the canvas | **Chat Input** and **Chat Output** |
| Agent | `Threat Prevention Agent` (AI Agent) | **Tool Agent** | **Agent** |
| Model | `OpenAI Chat Model`, credential **Lab Model (LiteLLM)** | **OpenAI** chat model node, credential **Lab Model (LiteLLM)**, Base Path `http://litellm:4000/v1` | **OpenAI**, OpenAI API Base `http://litellm:4000/v1`, API key from the global variable `LITELLM_MASTER_KEY` |
| Memory | `Conversation Memory` | **Buffer Memory** | The chat history Langflow stores per session |
| Tools | `Threat Prevention MCP` or `MCP Gateway` | **Custom MCP**, **Available Actions** | **MCP Tools**, **Actions** |
| Files | `n8n/backup/workflows/threat-prevention-*.json` | `integrations/flowise/threat-prevention.flowdata.json`, `direct-threat-prevention.flowdata.json` | `integrations/langflow/threat-prevention.flow.json`, `direct-threat-prevention.flow.json` |

---

## Good practice

- **Be specific.** *Show the High severity protections that are in Detect mode in the Optimized
  profile* gets a better answer than *show protections*
- **Changes stay with an administrator.** The agent drafts the change. An administrator applies it in
  SmartConsole and installs the policy
- **Use the memory.** After a list of profiles, ask *compare the first two* without repeating the
  question
- **Duplicate before you edit.** `n8n-import` keeps a workflow you changed, but a duplicate is the safe
  way to keep your own variant next to the seeded one
- **Keep the tool scope small.** When you build your own agent on the gateway, select only the tools it
  needs, and never more than 128
