# Code-First Agent — the same gateway, in code

This is the **graduation path** from the low-code agent builders (n8n, Flowise,
Langflow) to plain Python. Everything here hits the **exact same Docker MCP
Gateway** the low-code agents use:

```
http://mcp-gateway:8080/mcp        (Streamable-HTTP, Bearer auth)
```

Nothing new is deployed. These are **runnable examples** you drop into the
existing demo network. There are no PyPI dependencies anywhere — both scripts are
Python **standard library only** (`urllib`), so they run unchanged inside a bare
`python:3.12-alpine` container, matching the zero-dependency pattern in
`exercises/build-your-own-mcp/`.

| File | What it teaches |
|------|-----------------|
| `mcp_gateway_client.py` | The raw MCP handshake by hand — `initialize` → capture `Mcp-Session-Id` → `notifications/initialized` → `tools/list` → one read-only `tools/call`. Parses the SSE `data:` frames itself. Heavily commented. |
| `agent_loop.py` | A ~120-line LLM tool-use loop that wraps the client: maps the MCP tools to the Anthropic tool schema and runs a `tool_use → tools/call → tool_result` loop against the Anthropic Messages API (via urllib — no `anthropic` SDK). |

---

## Run it

Run **inside the demo network** — the gateway has no published host port, so it
is only reachable container-to-container. Mount this folder and run it with the
stock Python image (find your network name with `docker network ls | grep demo`,
usually `<compose-project>_demo`):

```bash
# 1) The raw MCP client — handshake + tools/list + one reputation_ip call
docker run --rm --network <demo> \
  -v "$PWD":/app \
  -e MCP_GATEWAY_TOKEN=cp-mcp-gateway-training-token \
  python:3.12-alpine python /app/mcp_gateway_client.py
```

```bash
# 2) The agent loop — needs an Anthropic API key for the brain
docker run --rm --network <demo> \
  -v "$PWD":/app \
  -e MCP_GATEWAY_TOKEN=cp-mcp-gateway-training-token \
  -e ANTHROPIC_API_KEY=sk-ant-... \
  python:3.12-alpine python /app/agent_loop.py
```

`<demo>` is the compose network (e.g. `cp-agentic-mcp-playground_demo`). Both
scripts default `GATEWAY_URL` to `http://mcp-gateway:8080/mcp` and the token to
`cp-mcp-gateway-training-token`, so on a stock stack you can omit the `-e` flags.

Handy overrides:

```bash
# point the client at a different read-only tool
-e DEMO_TOOL=reputation_ip -e DEMO_TOOL_ARGS='{"ip":"1.1.1.1"}'
# ask the agent something else
-e USER_PROMPT='Check the reputation of 9.9.9.9 and explain the verdict.'
-e ANTHROPIC_MODEL=claude-opus-4-8
```

> **Expected 0-tools gotcha.** A `tools/list` *before* the handshake returns
> zero tools — that's MCP being stateful, not a bug. If a running stack ever
> shows an empty catalog, restart the gateway: `docker restart mcp-gateway`
> (it enumerates tools once at boot). See
> `docs/guides/MCP_Gateway_Explained.md`.

---

## How it maps to the n8n / Flowise / Langflow versions

Same gateway, same Bearer token, same ~180 tools — only the **agent host**
changes. This folder is the "in code" column:

| Concern | n8n `*-via-gateway` | Flowise / Langflow | **Code-first (here)** |
|---------|---------------------|--------------------|-----------------------|
| MCP endpoint | `mcpClientTool` node → `http://mcp-gateway:8080/mcp` | `customMCP` / `MCPTools` → same URL | `MCPGatewayClient` → same URL |
| Auth | credential **CP MCP Gateway Bearer** | header `Authorization: Bearer <token>` | `MCP_GATEWAY_TOKEN` env → Bearer header |
| Handshake | node does it for you | node does it for you | **you do it** (`initialize` + session id + `notifications/initialized`) |
| Tool schema | auto-discovered | auto-discovered | `mcp_tools_to_anthropic()` maps `inputSchema` → `input_schema` |
| The brain (LLM) | Chat Model node (Azure default) | OpenAI-compatible model node | Anthropic Messages API (`claude-opus-4-8`) via urllib |
| Tool-use loop | inside the Agent node | inside the Tool Agent | the `while` loop in `agent_loop.py` |

The point of the table: **MCP governs the tools, not the LLM.** Swap the brain
(n8n Chat Model node ↔ Anthropic here ↔ Bedrock ↔ Ollama) and every tool call
over the gateway is byte-for-byte identical. Reading `agent_loop.py` is the
fastest way to see what the visual "Agent" node is actually doing.

See `INTEGRATION.md` in this folder for the exact `docker run` commands and the
one `.env-example` key to add — there is **no compose service to add**.
