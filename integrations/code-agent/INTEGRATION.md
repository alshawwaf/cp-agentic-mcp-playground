# INTEGRATION — Code-First Agent

Everything an integrator must add to the shared files lives here, so this feature
merges cleanly alongside the others. **This feature adds no compose service** —
`mcp_gateway_client.py` and `agent_loop.py` are runnable examples, not
long-running services. There is nothing to add to `docker-compose.yml`.

---

## 1. `docker-compose.yml`

**No changes.** No new service, no Traefik labels (there is no web UI). The
scripts run on demand with `docker run` against the existing `mcp-gateway`
service on the `demo` network.

---

## 2. `.env-example` — keys to add

The gateway URL and token are already covered by the existing
`MCP_GATEWAY_TOKEN` key (default `cp-mcp-gateway-training-token`). The only new
key is the Anthropic API key that `agent_loop.py` uses for the LLM brain. Append
under an appropriate section (e.g. near the other model keys):

```dotenv
# ---------------- Code-first agent (integrations/code-agent) ----------------
# Used ONLY by integrations/code-agent/agent_loop.py (the code-first LLM tool-use
# loop) to call the Anthropic Messages API. Leave blank if you only run the raw
# MCP client (mcp_gateway_client.py), which needs no LLM key. Never commit a real key.
ANTHROPIC_API_KEY=
# Optional override; defaults to claude-opus-4-8.
ANTHROPIC_MODEL=claude-opus-4-8
```

No secrets are committed anywhere; the scripts read these from the environment
at runtime.

---

## 3. One-time / on-demand run commands

The gateway has **no published host port** — reach it container-to-container on
the `demo` network. First find the actual network name (compose prefixes it with
the project, e.g. `cp-agentic-mcp-playground_demo`):

```bash
docker network ls | grep demo
```

Then, from this folder (`integrations/code-agent/`), mount it into a stock
Python image and run either example. Replace `<demo>` with the network name.

```bash
# Raw MCP client — full handshake + tools/list + one read-only reputation_ip call
docker run --rm --network <demo> \
  -v "$PWD":/app \
  -e MCP_GATEWAY_TOKEN=cp-mcp-gateway-training-token \
  python:3.12-alpine python /app/mcp_gateway_client.py
```

```bash
# Code-first agent loop — Anthropic brain + MCP tools over the same gateway
docker run --rm --network <demo> \
  -v "$PWD":/app \
  -e MCP_GATEWAY_TOKEN=cp-mcp-gateway-training-token \
  -e ANTHROPIC_API_KEY=sk-ant-... \
  python:3.12-alpine python /app/agent_loop.py
```

Notes:

- On a stock stack the `-e MCP_GATEWAY_TOKEN=...` flag is optional — the scripts
  default to `cp-mcp-gateway-training-token` and `GATEWAY_URL`
  `http://mcp-gateway:8080/mcp`. Pass `-e GATEWAY_URL=...` to target a
  differently-named gateway.
- If `tools/list` shows 0 tools on a running stack, the gateway enumerated
  before its sidecars were ready — `docker restart mcp-gateway` and re-run.
- No `pip install` is performed or required; both scripts are stdlib-only, so
  the plain `python:3.12-alpine` image is enough.
