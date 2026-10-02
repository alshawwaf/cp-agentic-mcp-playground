# Code-first agent: integration notes

How the code-first agent fits into the lab. To run it, see [README.md](README.md).

## What it adds to the lab

Nothing runs permanently. `mcp_gateway_client.py` and `agent_loop.py` are examples you start on
demand in a throwaway `python:3.12-alpine` container. There is no compose service, no web UI, no
route and no published port.

| Part | Uses |
|------|------|
| MCP tools | `mcp-gateway` at `http://mcp-gateway:8080/mcp`, Bearer `MCP_GATEWAY_TOKEN` |
| Model | `litellm` at `http://litellm:4000/v1`, model `lab-chat`, Bearer `LITELLM_MASTER_KEY` |
| Network | `<project>_lab`, the lab network (both services are on it, neither has a host port) |
| Tracing | Every `lab-chat` call is traced in Langfuse by LiteLLM, like the builders' model calls |

## Settings

No new setting. The scripts read two values that `./setup.sh` already writes to `.env`:

| Variable | Needed by |
|----------|-----------|
| `MCP_GATEWAY_TOKEN` | `mcp_gateway_client.py`, `agent_loop.py` |
| `LITELLM_MASTER_KEY` | `agent_loop.py` |

The provider key (`AZURE_OPENAI_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` or `GEMINI_API_KEY`)
stays in the `litellm` container. The scripts never need it. Pass the two values above, and nothing
else, into the container: the commands in the README read them from `.env` by name, or let
`op run` resolve 1Password references. The folder is mounted read-only.

## Verify

```sh
tests/acceptance/run.sh --only CODE-AGENT
```

The check runs the client (handshake and `tools/list`, 190 tools) and, when model calls are on, the
agent loop (one tool call through the gateway, then an answer). Both passed on the lab's end-to-end runs,
with the local Ollama model and with Azure OpenAI behind `lab-chat`.

## Offline tests

```sh
.github/scripts/py-isolated.sh -- python3 integrations/code-agent/test_code_agent.py
```

They cover Server-Sent Events and plain JSON replies, Bearer auth, JSON-RPC errors, pagination, a
new session after HTTP 404, `DELETE` on close, tool scoping, and a failing tool call that goes back
to the model instead of stopping the loop.
