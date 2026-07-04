# Exercise: Build Your Own MCP Server

Everything else in this playground *consumes* prebuilt MCP servers. This exercise
is the other side: you **author one**. You'll wrap Check Point's IPS/CVE
Publications API as an MCP server with two tools, then register it behind the
Docker MCP Gateway so any agent in the lab can use it.

- **Scaffold (your task):** `exercises/build-your-own-mcp/scaffold/ips_cve_mcp.py` — 5 TODOs.
- **Solution:** `exercises/build-your-own-mcp/solution/ips_cve_mcp.py` — peek if stuck.
- **Zero dependencies:** pure Python standard library. No pip, no MCP SDK — so you
  see the actual protocol (initialize → tools/list → tools/call) that a framework
  like FastMCP hides. (A "going further with FastMCP" note is at the end.)

## What you're building

Two tools over **Streamable HTTP** (the transport the gateway and n8n speak):

| Tool | Args | Returns |
|---|---|---|
| `ips_latest_protections` | none | latest published Check Point IPS protections |
| `ips_protections_by_cve` | `cve_id` (string) | protections covering that CVE |

The API client and all the HTTP/JSON-RPC plumbing are written for you. You fill
in **5 TODOs**, all about declaring and dispatching tools.

## Prerequisites

An Infinity Portal API key (Client ID + Access Key) with the IPS service. Set:

```bash
export IPS_CLIENT_ID=...      # from Infinity Portal → Global Settings → API Keys
export IPS_ACCESS_KEY=...
```

## Step 1 — Run the scaffold

```bash
cd exercises/build-your-own-mcp/scaffold
python3 ips_cve_mcp.py           # starts on :3013
```

## Step 2 — Do the 5 TODOs

1. **Declare the tools** in the `TOOLS` registry (name, description, `inputSchema`, handler).
2. **`tools/list`** — return every tool as `{name, description, inputSchema}`.
3. **`tools/call`** — look the tool up by name (return error `-32602` if unknown).
4. **Run it** — call the handler and wrap the result as `{"content":[{"type":"text","text": <json>}]}`.
5. **Handle errors** — on exception return the same shape with `"isError": true` (never crash).

## Step 3 — Test it (no gateway needed)

```bash
# initialize — grab the mcp-session-id from the response headers
curl -s -D- -X POST localhost:3013/mcp -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | grep -i mcp-session-id

SID=<the id from above>

# tools/list — you should see both tools
curl -s -X POST localhost:3013/mcp -H 'Content-Type: application/json' -H "mcp-session-id: $SID" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'

# tools/call — real data (needs the API key)
curl -s -X POST localhost:3013/mcp -H 'Content-Type: application/json' -H "mcp-session-id: $SID" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"ips_protections_by_cve","arguments":{"cve_id":"CVE-2024-3400"}}}'
```

Responses come back as Server-Sent-Events (`event: message` / `data: {...}`) —
that's Streamable HTTP.

## Step 4 — Run it as a gateway sidecar

The container is pre-wired as an **opt-in** compose service (profile `exercises`,
so it never touches the default stack). Build + run it, then register it in the
gateway:

```bash
# from the repo root, with IPS_CLIENT_ID / IPS_ACCESS_KEY in your .env
docker compose --profile exercises up -d --build ips-cve-mcp
```

Then add it to the gateway (this is part of the lesson — extending the gateway):

1. In `mcp-gateway/catalog.yaml`, add a server:
   ```yaml
     ips-cve:
       description: "Check Point IPS/CVE protections (exercise)"
       title: "IPS / CVE"
       type: "remote"
       remote:
         url: "http://ips-cve-mcp:3013/"
         transport_type: "streamable"
   ```
2. In `docker-compose.yml`, add `ips-cve` to the gateway's `--servers=` list and
   a `depends_on` on `ips-cve-mcp`.
3. `docker compose up -d mcp-gateway` and confirm the new tools appear:
   ```bash
   ./scripts/health-check.sh --profile cpu    # gateway tool count goes up by 2
   ```

Now an n8n agent (see the [MCP Gateway guide](MCP_Gateway_Agent_Guide.md)) can
call `ips_protections_by_cve` — e.g. *"Which IPS protections cover CVE-2024-3400?"*

## Going further — FastMCP

In the real world you'd usually reach for the official MCP SDK (`mcp` /
`FastMCP`), which turns a tool into a decorated function and handles all the
transport/handshake code you see here:

```python
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("Check Point IPS/CVE")

@mcp.tool()
def ips_protections_by_cve(cve_id: str) -> list:
    "Get the Check Point IPS protections that cover a specific CVE."
    return CLIENT.by_cve(cve_id)

mcp.run(transport="streamable-http")
```

That's the whole server. This exercise builds it by hand so you understand what
those three lines actually do — and because the `mcp` SDK installs via
Artifactory in this org, the stdlib version is what runs out-of-the-box here.

> **Security note:** never commit real API keys. Keep `IPS_CLIENT_ID` /
> `IPS_ACCESS_KEY` in `.env` (gitignored), not in code.
