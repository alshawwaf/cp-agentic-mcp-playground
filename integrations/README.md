# CP MCP Gateway Agent — Flowise & Langflow

Importable "Check Point MCP Gateway Agent" flows for the two other agent builders in this
stack, mirroring the n8n `*-via-gateway` agents. Each is a chat agent that:

- uses an **OpenAI-compatible chat model**, and
- calls Check Point MCP tools through the **Docker MCP Gateway** at
  `http://mcp-gateway:8080/mcp` (HTTP‑streamable) with header
  `Authorization: Bearer <MCP_GATEWAY_TOKEN>`.

The system prompt is a concise Check Point ops assistant: it reads first, summarizes results
faithfully (no raw JSON), and confirms before making any change. One gateway endpoint exposes
the whole Check Point MCP fleet, so the agent can reach management, Gaia, logs, threat
prevention, reputation, etc.

## Versions targeted

| Builder  | Version  | Notes |
|----------|----------|-------|
| Flowise  | **3.1.2** | Custom MCP header auth requires Flowise **≥ 3.0.2**. |
| Langflow | **1.10.1** | Flow shape based on the bundled `Simple Agent` starter (tag `1.10.1rc3`). |

## Files

- `flowise/cp-mcp-gateway-agent.flowdata.json` — the graph object (pretty JSON). The seeder
  stringifies it into the `flowData` field when POSTing.
- `langflow/cp-mcp-gateway-agent.flow.json` — an importable Langflow flow JSON.
- `seed_builders.sh` — substitutes secrets and POSTs both flows to the running builders.

### Flowise graph

`toolAgent` (Tool Agent v2) wired to:
- `customMCP` (Custom MCP Tool v1.1) — `mcpServerConfig` points at the gateway with the Bearer
  header; `mcpActions` is pre-seeded with a representative read-first tool list.
- `chatOpenAI` (v8.2) — OpenAI-compatible; set `basepath` for a non‑OpenAI endpoint. The model
  **API key is a Flowise credential** (`openAIApi`), set in the UI — it does not live in the
  flow JSON, so it is not one of the substituted placeholders.
- `bufferMemory` (v2).

### Langflow graph

`ChatInput → Agent → ChatOutput`, with the Agent's `model` field set to
`connect_other_models` (exposing a Language Model input handle) and wired to:
- `MCPTools` (`component_as_tool` → Agent `tools`) — `mcp_server` embeds the gateway config as
  a fallback; leave `tool` empty to expose all tools.
- `OpenAIModel` (`model_output` → Agent `model`) — the default, has `openai_api_base` for
  OpenAI-compatible endpoints.
- `AzureOpenAIModel` — included but **unwired**; drag its `model_output` onto the Agent to swap.

## Placeholders (this repo is PUBLIC — never commit real keys)

The JSONs ship with placeholder tokens that the seeder replaces from the environment:

| Placeholder | Env var | Used by |
|-------------|---------|---------|
| `__MCP_GATEWAY_TOKEN__` | `MCP_GATEWAY_TOKEN` | Flowise + Langflow (gateway Bearer) |
| `__OPENAI_API_KEY__` | `OPENAI_API_KEY` | Langflow `OpenAIModel` |
| `__AZURE_OPENAI_API_KEY__` | `AZURE_OPENAI_API_KEY` | Langflow `AzureOpenAIModel` |
| `__AZURE_OPENAI_ENDPOINT__` | `AZURE_OPENAI_ENDPOINT` | Langflow `AzureOpenAIModel` |
| `__AZURE_OPENAI_DEPLOYMENT__` | `AZURE_OPENAI_DEPLOYMENT` | Langflow `AzureOpenAIModel` |

Flowise reads the model key from a Flowise **credential**, so no OpenAI/Azure placeholder is
needed in the Flowise JSON — only `__MCP_GATEWAY_TOKEN__`.

## Running the seeder

```sh
export MCP_GATEWAY_TOKEN=...            # bearer for the Docker MCP Gateway
export OPENAI_API_KEY=...               # OpenAI / OpenAI-compatible key (Langflow)
export FLOWISE_API_KEY=...              # Flowise REST API key
export LANGFLOW_API_KEY=...             # Langflow REST API key
# optional Azure (Langflow AzureOpenAIModel):
#   AZURE_OPENAI_API_KEY / AZURE_OPENAI_ENDPOINT / AZURE_OPENAI_DEPLOYMENT
# optional overrides:
#   FLOWISE_URL  (default http://flowise:3001)
#   LANGFLOW_URL (default http://langflow:7860)

sh integrations/seed_builders.sh
```

The seeder:
- skips a builder when its API key is unset,
- is idempotent-ish (skips if a flow named **"CP MCP Gateway Agent"** already exists),
- substitutes placeholders into **temp copies only** — the repo files are never modified,
- never prints secrets.

Flowise import: `POST /api/v1/chatflows` (Bearer `FLOWISE_API_KEY`), body
`{"name","type":"CHATFLOW","deployed":true,"flowData":"<stringified graph>"}`.
Langflow import: `POST /api/v1/flows/` (`x-api-key: LANGFLOW_API_KEY`), the flow JSON as-is.

> Note: the seeder defaults Flowise to `http://flowise:3001` because `docker-compose.yml` sets
> `FLOWISE_PORT=3001`. Override with `FLOWISE_URL` if your instance differs.

## Caveat — one UI round-trip may be needed

These JSONs are built from the authoritative component sources for the targeted versions, but a
few node templates are only fully hydrated by the builder itself. After seeding, **open each
flow and Save once**:

- **Flowise `customMCP`** — click **Refresh** on *Available Actions* so the bound tool names
  match exactly what the live gateway exposes. `mcpActions` ships with a representative
  read-first list (`show_hosts`, `show_networks`, `show_access_rulebase`, …); if a name does not
  match, that tool silently won't bind. The list must be **non-empty** or zero tools bind.
- **Langflow `MCPTools` / `OpenAIModel` / `AzureOpenAIModel`** — these components are not present
  in any bundled starter project, so their templates were assembled from the 1.10.1 component
  sources (embedded in each node's `code` field). Opening + saving lets Langflow re-render them
  from the registered component definition.
