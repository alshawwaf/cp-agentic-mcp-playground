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

## Importing — automatic on every deploy

The **`builders-import`** one-shot compose service (parity with `n8n-import`) runs
`seed_builders.py` on every `docker compose up`: it waits for Flowise + Langflow, substitutes the
placeholders from the environment, and imports each flow **idempotently** — a flow named
**"CP MCP Gateway Agent"** is never imported twice, and a flow list that can't be positively parsed
refuses to import rather than risk a duplicate.

**No manual API keys are required.** Authentication, in order:

| Builder  | Default (no keys)                                                            | Override |
|----------|------------------------------------------------------------------------------|----------|
| Flowise  | Login with the stack admin account (`N8N_ADMIN_EMAIL` / `N8N_ADMIN_PASSWORD` — the same creds used at Flowise first-setup). Session cookies are replayed with the `x-request-from: internal` header (Flowise only honours cookie-JWT auth for "internal" requests). | `FLOWISE_API_KEY` (Bearer) |
| Langflow | Login as the provisioned superuser (same stack admin creds) → JWT Bearer.    | `LANGFLOW_API_KEY` (x-api-key) |

Implementation notes (learned the hard way, kept for posterity):
- Langflow gzips `GET /api/v1/flows/` regardless of `Accept-Encoding` — the seeder decompresses by
  magic bytes before parsing, otherwise the idempotency check silently sees an empty list.
- Langflow's create endpoint rejects the export's slug `id` (wants a UUID) — the seeder strips it.
- Flowise import: `POST /api/v1/chatflows`, body
  `{"name","type":"CHATFLOW","deployed":true,"flowData":"<stringified graph>"}`.
- Langflow import: `POST /api/v1/flows/` with the flow JSON (sans `id`).

## Running the seeder manually

Same code path as the deploy (the `.sh` is a thin wrapper around `seed_builders.py`):

```sh
cd /path/to/repo && set -a && . ./.env && set +a
ADMIN_EMAIL="$N8N_ADMIN_EMAIL" ADMIN_PASSWORD="$N8N_ADMIN_PASSWORD" \
  sh integrations/seed_builders.sh
```

Or just re-run the compose service: `docker compose up builders-import`.

The seeder substitutes placeholders into **in-memory copies only** (repo files are never modified)
and never prints secrets.
