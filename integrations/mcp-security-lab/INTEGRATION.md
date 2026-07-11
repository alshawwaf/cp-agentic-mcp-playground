# MCP Security Lab — integration wiring

Everything the integrator must add to the **shared** files (`docker-compose.yml`,
`.env-example`) to enable this opt-in teaching lab. New standalone files
(`vuln_mcp_server.py`, the guide, the n8n workflow) are already committed and need
no wiring — the workflow is re-imported on every redeploy like the others.

> ☠️ **Opt-in only.** The `vuln-mcp` server is *intentionally vulnerable* and runs
> **only** when you enable the `security-lab` compose profile. It never starts
> with the default stack or CI. It has **no host port** and lives only on the
> internal `demo` network. See `docs/guides/MCP_Security_Lab.md`.

---

## 1. `docker-compose.yml` — add this service

Paste under `services:` (2-space indent, same level as the other sidecars).
**Dockerfile-less:** it runs the stock `python:3.12-alpine` image and executes the
stdlib-only script mounted in read-only — no build step, no dependencies.

```yaml
  # ─────────── MCP Security Lab (opt-in, INTENTIONALLY VULNERABLE) ───────────
  # ☠️ A deliberately-unsafe MCP server used ONLY to teach MCP attack/defend
  # (tool poisoning, indirect prompt injection, over-permissioned tools,
  # rug-pull). Everything it does is SIMULATED and clearly labelled — no real
  # file reads, no exfiltration. Behind the `security-lab` profile so it NEVER
  # starts with the default stack or CI, and it publishes NO host port (internal
  # `demo` network only). Do not front a real workload with this.
  # Enable: COMPOSE_PROFILES=...,security-lab   (see integrations/mcp-security-lab/INTEGRATION.md)
  # Guide:  docs/guides/MCP_Security_Lab.md
  vuln-mcp:
    image: python:3.12-alpine
    container_name: vuln-mcp
    profiles: [ "security-lab" ]
    networks: [ "demo" ]
    working_dir: /app
    volumes:
      - ./integrations/mcp-security-lab:/app:ro
    environment:
      - MCP_PORT=3099
      # MCP_BEARER_TOKEN is intentionally left UNSET — the lab demonstrates a
      # direct, unauthenticated sidecar (contrast the gateway's mandatory Bearer).
    command: [ "python3", "/app/vuln_mcp_server.py" ]
    healthcheck:
      test: [ "CMD", "python3", "-c", "import socket; socket.create_connection(('127.0.0.1',3099),3)" ]
      interval: 10s
      timeout: 5s
      retries: 12
      start_period: 10s
    restart: unless-stopped
```

**No Traefik labels / no host ports** — this server has no web UI and must stay
unreachable from outside the internal network. The n8n agent reaches it
container-to-container at `http://vuln-mcp:3099`.

> Do **not** add `vuln-mcp` to `mcp-gateway/catalog.yaml` or to the gateway's
> `--servers=` list. Keeping the vulnerable server OFF the gateway is part of the
> lesson (§6 of the guide shows the gateway as the *defence*, not a host for it).

---

## 2. `.env-example` — add this block

No secrets are required. The only addition is a documentation note for the new
profile (the port is fixed at `3099` in the compose block above).

```bash
# ---------------- MCP Security Lab (opt-in: profile `security-lab`) ----------------
# ☠️ Enables `vuln-mcp`, an INTENTIONALLY VULNERABLE MCP server for the
# attack/detect/defend teaching lab. Simulated only — no real file reads or
# exfiltration; no host port; internal `demo` network only. Add `security-lab`
# to COMPOSE_PROFILES to turn it on. See docs/guides/MCP_Security_Lab.md.
# (No API keys or secrets needed. Auth is deliberately OFF to model a direct,
#  unauthenticated sidecar.)
```

And append `security-lab` to the documented profile list in the `COMPOSE_PROFILES`
comment near the bottom of `.env-example`:

```
#   security-lab → the INTENTIONALLY VULNERABLE MCP server (attack/defend lab)
```

---

## 3. One-time enable (live deploy)

The `security-lab` profile is off by default. Turn it on and start just this
service:

```bash
COMPOSE_PROFILES=security-lab docker compose up -d vuln-mcp
```

If you already run other profiles, add it comma-separated (in `.env` or inline):

```bash
COMPOSE_PROFILES=exercises,security-lab docker compose up -d vuln-mcp
```

Verify it's listening (from a container on the `demo` network, since there's no
host port):

```bash
docker compose exec n8n sh -lc 'curl -s -X POST http://vuln-mcp:3099/mcp \
  -H "Accept: text/event-stream" \
  -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/list\"}"'
```

You should see an SSE `event: message` frame listing `weather_lookup`,
`read_local_file`, `fetch_ticket`, `currency_convert`.

Then open the **MCP Security Lab — Vulnerable Agent (DEMO)** workflow in n8n and
follow `docs/guides/MCP_Security_Lab.md` (attack → detect → defend).

**Tear down when done teaching:**

```bash
docker compose rm -sf vuln-mcp
```

---

## Files added by this feature

| File | What it is |
|------|------------|
| `integrations/mcp-security-lab/vuln_mcp_server.py` | The intentionally-vulnerable, stdlib-only Streamable-HTTP MCP server (4 teaching tools). |
| `integrations/mcp-security-lab/INTEGRATION.md` | This file — the compose/env wiring. |
| `docs/guides/MCP_Security_Lab.md` | Threat model + hands-on attack/detect/defend walkthrough. |
| `n8n/backup/workflows/mcp-security-lab-agent.json` | The vulnerable n8n chat agent wired directly to `vuln-mcp:3099`. |
