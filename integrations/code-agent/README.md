# Code-first agent: the same gateway and model, in code

This folder is the step from the low-code builders (n8n, Flowise, Langflow) to plain Python. The
scripts use the same MCP Gateway, the same Bearer token and the same lab model as the seeded agents:

```
MCP tools:  http://mcp-gateway:8080/mcp            Streamable HTTP, Bearer MCP_GATEWAY_TOKEN
Model:      http://litellm:4000/v1  model lab-chat  Bearer LITELLM_MASTER_KEY
```

Both scripts use the Python standard library only (`urllib`). There is nothing to install, and they
run unchanged in a plain `python:3.12-alpine` container.

| File | What it teaches |
|------|-----------------|
| `mcp_gateway_client.py` | The MCP handshake by hand: `initialize`, keep the `Mcp-Session-Id`, `notifications/initialized`, `tools/list`, one read-only `tools/call`, then `DELETE` the session. It reads the Server-Sent Events replies itself. |
| `agent_loop.py` | The smallest honest tool-use loop. It maps the MCP tools to OpenAI function tools, sends them to `lab-chat` through LiteLLM (Chat Completions), runs each tool call through the gateway and returns the result to the model until it answers. |
| `test_code_agent.py` | Offline tests: a mock gateway and the lab's mock model provider. No network, no model. |

## Before you start

- The lab is running and `./scripts/doctor.sh --post-start` shows no blockers.
- `.env` has `MCP_GATEWAY_TOKEN` and `LITELLM_MASTER_KEY` (`./setup.sh` generates both).
- `lab-chat` has a provider: a cloud key in `.env`, or the local Ollama model. With the local model
  each answer takes much longer on a CPU.
- The default sample tool is `reputation_ip`. It needs `REPUTATION_API_KEY` for a real verdict;
  without it the tool answers that the service is not configured, and the handshake and the loop
  still work. Pick another tool with `SAMPLE_TOOL` or `MCP_TOOLS`.

## Run it

The gateway and LiteLLM have no host port. Run the scripts in a container on the lab network. Its
name is `<project>_lab`, for example `cp-agentic-mcp-playground_lab` when you cloned into the
default folder. To find it:

```bash
docker network ls --format '{{.Name}}' | grep '_lab$'
```

Pass only the keys a script needs, never the whole `.env` (it also holds provider keys and Check
Point credentials). From this folder (`integrations/code-agent`), in bash or zsh:

```bash
# 1. The MCP client: handshake, tools/list, one reputation_ip call, DELETE
docker run --rm --network <project>_lab \
  --env-file <(grep '^MCP_GATEWAY_TOKEN=' ../../.env) \
  -v "$PWD":/app:ro python:3.12-alpine python /app/mcp_gateway_client.py

# 2. The agent loop: lab-chat plus the gateway tools
docker run --rm --network <project>_lab \
  --env-file <(grep -E '^(MCP_GATEWAY_TOKEN|LITELLM_MASTER_KEY)=' ../../.env) \
  -v "$PWD":/app:ro python:3.12-alpine python /app/agent_loop.py
```

With 1Password references in `.env`, let `op run` resolve them and pass the names only:

```bash
op run --env-file=../../.env -- docker run --rm --network <project>_lab \
  -e MCP_GATEWAY_TOKEN -e LITELLM_MASTER_KEY -v "$PWD":/app:ro \
  python:3.12-alpine python /app/agent_loop.py
```

Expected result:

- The client prints `-> tools/list returned 190 tools.` (on a lab where every gateway server is
  up), a few tool names, the result of the sample call and `-> session closed (HTTP DELETE).`
- The loop prints how many tools it binds, one `-> model called <tool>(...)` line per tool call,
  and then `=== ANSWER ===` with the final answer. Exit status 0.

### Settings

| Variable | Script | Default |
|----------|--------|---------|
| `MCP_GATEWAY_TOKEN` | both | required |
| `LITELLM_MASTER_KEY` | `agent_loop.py` | required |
| `GATEWAY_URL` | both | `http://mcp-gateway:8080/mcp` |
| `MCP_TIMEOUT` | both | 60 seconds per gateway request |
| `SAMPLE_TOOL`, `SAMPLE_TOOL_ARGS` | `mcp_gateway_client.py` | `reputation_ip`, `{"ip": "8.8.8.8"}` |
| `LITELLM_BASE_URL` | `agent_loop.py` | `http://litellm:4000/v1` |
| `LAB_MODEL` | `agent_loop.py` | `lab-chat` |
| `MCP_TOOLS` | `agent_loop.py` | `reputation_*` (comma-separated names or shell patterns; `*` = every tool, at most 128) |
| `USER_PROMPT` | `agent_loop.py` | a reputation lookup on 8.8.8.8 |
| `MAX_TURNS` | `agent_loop.py` | 8 model calls |
| `MAX_TOKENS` | `agent_loop.py` | the provider's default |
| `LLM_TIMEOUT` | `agent_loop.py` | 300 seconds per model call |

Add them with `-e NAME=value`, for example
`-e MCP_TOOLS='show_threat_*' -e USER_PROMPT='Which threat profiles exist?'`.

## What the scripts show

**MCP is stateful.** `initialize` returns an `Mcp-Session-Id`. Every later request sends it. A
`tools/list` without a session is refused with a JSON-RPC error, not an empty list, and the client
shows that error.

**Sessions cost memory.** The gateway keeps each session, plus one session on every MCP server the
session used, until the client sends `DELETE`. The client always closes its session. The Check Point
MCP servers also close sessions that stay idle for 30 minutes (`MCP_SESSION_IDLE_TIMEOUT_SECONDS`,
default 1800) and hold at most 32 (`MCP_MAX_SESSIONS`). When the gateway no longer knows a session
(it restarted or closed it), it answers HTTP 404, and the client opens a new session and retries the
request once.

**Scope the tools.** The gateway serves 190 tools. Sending all of them on every turn costs tens of
thousands of input tokens and breaks providers that accept at most 128 tools per request.
`MCP_TOOLS` keeps only what the agent needs, as the builders do.

**MCP governs the tools, not the model.** Change the provider behind `lab-chat` in `.env`
(`docker compose up -d litellm`) and every tool call stays the same.

| Concern | n8n | Flowise and Langflow | Code-first (this folder) |
|---------|-----|----------------------|--------------------------|
| MCP endpoint | MCP Client Tool node | Custom MCP / MCP Tools node | `MCPGatewayClient` |
| Auth | Credential MCP Gateway Bearer | Bearer header: the Flowise variable `MCP_GATEWAY_TOKEN`; in Langflow, filled in by the seeder | `MCP_GATEWAY_TOKEN` |
| Handshake | the node does it | the node does it | you do it |
| Model | OpenAI Chat Model, credential Lab Model (LiteLLM) | ChatOpenAI or OpenAI node on LiteLLM | `POST /v1/chat/completions` on LiteLLM |
| Tool loop | inside the AI Agent node | inside the agent node | the `while` loop in `agent_loop.py` |

## Data handling

Every tool result (rules, objects, logs, IP addresses) goes to the provider behind `lab-chat`. With a
cloud provider that is an external service. Use lab data only. Never send customer configurations or
telemetry to a provider that is not approved for that data.

## Tests

```bash
python3 integrations/code-agent/test_code_agent.py
```

CI runs it with no network through `.github/scripts/py-isolated.sh`. The acceptance check
`CODE-AGENT` runs both scripts against the running lab (see [tests/README.md](../../tests/README.md)).

## Troubleshooting

| Message | Fix |
|---------|-----|
| `HTTP 401 from the gateway` | `MCP_GATEWAY_TOKEN` does not match `.env`. Pass it from `.env` as shown above. |
| `HTTP 404 from the gateway: session not found` | Check that `GATEWAY_URL` ends in `/mcp`. If it does, the gateway restarted during the run: run the script again. |
| The host name `mcp-gateway` or `litellm` does not resolve | The container is not on the lab network. Use `--network <project>_lab`. |
| The tool count is far below 190 | A server was not ready when the gateway listed tools: `docker compose restart mcp-gateway`. |
| `Model call failed: HTTP 401` | `LITELLM_MASTER_KEY` does not match `.env`. |
| `Model call failed` with a provider message | Check the provider settings: `docker compose run --rm --no-deps litellm --check`. |
| The loop times out on a local model | Raise `LLM_TIMEOUT`, or set a cloud model key in `.env`. |
