# Exercise: Build Your Own MCP Server

Everything else in the lab consumes MCP servers. In this exercise you write one. You wrap the Check Point IPS protections API as an MCP server with two tools, run it as a lab service, register it with the MCP Gateway, and give the tools to an agent.

The server uses the Python standard library only: no pip, no MCP SDK. So you see the protocol itself: `initialize`, `notifications/initialized`, `tools/list`, `tools/call`.

| File | What it is |
|---|---|
| `exercises/build-your-own-mcp/scaffold/ips_cve_mcp.py` | Your task: five TODOs |
| `exercises/build-your-own-mcp/solution/ips_cve_mcp.py` | The finished server. Peek only if you are stuck |
| `exercises/build-your-own-mcp/test_byo_mcp.py` | The self-test: offline, against your running server, or through the gateway |
| `exercises/build-your-own-mcp/Dockerfile` | Builds the solution, or your scaffold with `IPS_CVE_VARIANT=scaffold` |

## What you build

Two tools over Streamable HTTP, the transport the gateway and the builders use:

| Tool | Arguments | Returns |
|---|---|---|
| `ips_latest_protections` | none | The latest published Check Point IPS protections |
| `ips_protections_by_cve` | `cve_id` (string, for example `CVE-2024-3400`) | The protections that cover that CVE |

The API client and the HTTP and JSON-RPC plumbing are done. Your five TODOs are about declaring and dispatching tools.

## Prerequisites

- Python 3 on your computer, for the scaffold and the offline self-test
- The lab running, for Steps 4 to 6
- For real data: an **Account API key** from the Check Point portal (portal.checkpoint.com: Global Settings > API Keys > New). Put it in `.env` as `IPS_CLIENT_ID` and `IPS_ACCESS_KEY`, or enter it in `./setup.sh` (step 4, "the Build Your Own MCP exercise")
- `IPS_REGION`: `EU` (default, `cloudinfra-gw.portal.checkpoint.com`) or `US` (`cloudinfra-gw-us.portal.checkpoint.com`). It must match your portal account. To use other hosts, set both `IPS_AUTH_URL` and `IPS_SERVICE_URL` for the same region

The offline steps need no key, no Docker and no internet.

## Step 1: Run the self-test on the scaffold

```sh
cd exercises/build-your-own-mcp/scaffold
python3 ../test_byo_mcp.py ips_cve_mcp.py
```

The test starts a mock Check Point portal on `127.0.0.1`, loads your file, and walks the real protocol. Each check names the TODO it proves.

**Expected result:** the plumbing checks pass and the TODO checks fail:

```
FAIL  TODO 1    declare both tools in TOOLS   ...
...
8/13 checks passed
Still open: TODO 1, TODO 2, TODO 3, TODO 4, TODO 5. Edit the file, then run this test again.
```

## Step 2: Do the five TODOs

Open `scaffold/ips_cve_mcp.py`:

1. **TODO 1: declare the tools** in the `TOOLS` registry: a description (one clear sentence; the model reads it to decide when to call), an `inputSchema` and a handler. `ips_latest_protections` takes no arguments and calls `CLIENT.latest()`. `ips_protections_by_cve` takes one required string `cve_id` and calls `CLIENT.by_cve(cve_id)`
2. **TODO 2: `tools/list`** returns every tool as `{name, description, inputSchema}`
3. **TODO 3: `tools/call`** looks the tool up by name and returns the JSON-RPC error `-32602` when it is unknown
4. **TODO 4: run it.** Call the handler and wrap the result as `{"content": [{"type": "text", "text": <json string>}]}`
5. **TODO 5: handle errors.** On an exception, return the same shape with `"isError": true` and the message as text. Never let the server crash

Run the self-test after each TODO. **Expected result when you are done:** `13/13 checks passed`.

## Step 3: Call your server with curl (optional)

Start it on your computer. It listens on `127.0.0.1:3013` only:

```sh
python3 ips_cve_mcp.py
```

Stop it with Ctrl+C and start it again after every edit. In a second terminal:

```sh
# initialize: note the mcp-session-id response header
curl -s -D - -X POST http://127.0.0.1:3013/mcp \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"1"}}}'

SID=<the mcp-session-id from above>

# tell the server the client is ready (answers 202, no body)
curl -s -X POST http://127.0.0.1:3013/mcp -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' -H "mcp-session-id: $SID" \
  -d '{"jsonrpc":"2.0","method":"notifications/initialized"}'

# tools/list: both tools
curl -s -X POST http://127.0.0.1:3013/mcp -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' -H "mcp-session-id: $SID" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'

# tools/call
curl -s -X POST http://127.0.0.1:3013/mcp -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' -H "mcp-session-id: $SID" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"ips_protections_by_cve","arguments":{"cve_id":"CVE-2024-3400"}}}'
```

Replies come back as Server-Sent Events (`event: message`, then `data: {...}`). With `Accept: application/json` alone, the server answers plain JSON.

Without the API key in this shell, `tools/call` returns an `isError` result that says `IPS_CLIENT_ID / IPS_ACCESS_KEY are not set`. That is TODO 5 working. Real data is easiest in the next step, where the lab passes the key from `.env`.

## Step 4: Run it as a lab service

The compose service `ips-cve-mcp` is in the profile `exercises`. By default it runs the published solution image. To run **your** code, build it from the repository:

1. In `.env`, add `exercises` to `COMPOSE_PROFILES` and set `IPS_CVE_VARIANT=scaffold`
2. Build and start it:

   ```sh
   docker compose up -d --build ips-cve-mcp
   ```

3. After every edit, run the same command again

**Expected result**

- `docker compose logs ips-cve-mcp` shows `IPS/CVE MCP server (scaffold) listening on 0.0.0.0:3013/mcp` and whether the portal credentials are set
- `tests/acceptance/run.sh --only EXERCISES` shows `PASS` on the `EXERCISES` line: `ips-cve-mcp answers tools/list (2 tools: ...)`

Inside the lab the server binds `0.0.0.0`, but only on the lab's Docker network: no host port is published. To test it from the lab network with real data, run the self-test in a throwaway container. Find the lab network name first; it ends in `_lab`:

```sh
docker network ls --filter name=_lab
cd exercises/build-your-own-mcp
docker run --rm --network <lab-network> -v "$PWD":/x:ro python:3.12-alpine \
  python3 /x/test_byo_mcp.py --url http://ips-cve-mcp:3013/mcp --call CVE-2024-3400
```

To go back to the solution, set `IPS_CVE_VARIANT=solution` and run `docker compose up -d --build ips-cve-mcp` again.

## Step 5: Register it with the MCP Gateway

The gateway lists the tools of the servers in its catalog and in its `--servers` list. Add yours to both.

1. In `mcp-gateway/catalog.yaml`, under `registry:`, add:

   ```yaml
     ips-cve:
       description: "Check Point IPS protections by CVE (Build Your Own MCP exercise)"
       title: "Check Point IPS CVE (exercise)"
       type: "remote"
       remote:
         url: "http://ips-cve-mcp:3013/mcp"
         transport_type: "streamable"
       metadata:
         category: "checkpoint"
         tags: ["ips", "exercise"]
   ```

2. Next to `docker-compose.yml`, in your local `docker-compose.override.yml` (git ignores it), give the gateway the longer server list. If the file already exists (for example with the local ports from the README), add the `mcp-gateway` block under its `services:` key. Copy the other options exactly: an override replaces the whole `command` list. `required: false` keeps every other Compose command working when the `exercises` profile is off:

   ```yaml
   services:
     mcp-gateway:
       command:
         - "--transport=streaming"
         - "--port=8080"
         - "--catalog=checkpoint-mcp.yaml"
         - "--servers=documentation,quantum-management,policy-insights,cpinfo-analysis,https-inspection,management-logs,gaia,gw-cli,reputation-service,threat-emulation,threat-prevention,ips-cve"
         - "--preserve-tool-schema-dialect"
       depends_on:
         ips-cve-mcp:
           condition: service_healthy
           required: false
   ```

3. Recreate the gateway. It lists every server's tools once, at start:

   ```sh
   docker compose up -d mcp-gateway
   ```

4. Check that the gateway lists both tools. The self-test needs the gateway token in its environment; it never prints it. From `exercises/build-your-own-mcp`:

   ```sh
   MCP_GATEWAY_TOKEN="$(grep '^MCP_GATEWAY_TOKEN=' ../../.env | cut -d= -f2-)" \
     docker run --rm --network <lab-network> -e MCP_GATEWAY_TOKEN -v "$PWD":/x:ro python:3.12-alpine \
     python3 /x/test_byo_mcp.py --gateway http://mcp-gateway:8080/mcp
   ```

   With 1Password, use `op run --env-file=../../.env -- docker run ...` instead of the first line.

**Expected result:** `PASS  gateway   exercise tools listed by the gateway  192 gateway tools, including ips_latest_protections, ips_protections_by_cve` (the gateway's 190 lab tools plus your two). The gateway adds a server prefix only to tool names that collide with another server's.

While your server is registered, the acceptance check `GW-TOOLS` reports `FAIL` with `2 tools not in SERVER_TOOLS`. That is expected. It passes again once you remove the registration (Step 7).

## Step 6: Give the tools to an agent

The gateway agents select their tools from a fixed list, so no agent sees your tools until you add them. Add them to the **Check Point MCP Gateway Agent** (48 tools). Keep any agent at **128 tools or fewer**: OpenAI and Azure OpenAI models accept at most 128 tools per call.

| Builder | How |
|---|---|
| n8n | Open **Check Point MCP Gateway Agent**, open the **MCP Gateway** node, and select both tools under **Tools to Include** (50 in total). Save |
| Flowise | Open the agent, select **Refresh** on the **Custom MCP** node's Available Actions, tick both tools, and save. Refresh lists all gateway tools: keep the selection at 128 or fewer |
| Langflow (Complete lab) | Open the agent, refresh the **MCP Tools** component, and switch both tools on. A refresh can switch every gateway tool on: check that 128 or fewer stay on |

Then ask the agent: *Which IPS protections cover CVE-2024-3400?*

Your edits are kept: `n8n-import` and `builders-import` do not overwrite an agent you changed, unless you set `N8N_SEED_OVERWRITE=1` or `SEED_OVERWRITE=1`.

## Step 7: Clean up

1. Remove the `mcp-gateway` block from `docker-compose.override.yml`, then run `docker compose up -d mcp-gateway`
2. Remove the `ips-cve` entry from `mcp-gateway/catalog.yaml`. `./update.sh` stops if your local edits would conflict with an update
3. Remove `exercises` from `COMPOSE_PROFILES`, and `IPS_CVE_VARIANT` from `.env`
4. Remove the two tools from the agents you changed

## Security notes

- Keep `IPS_CLIENT_ID` and `IPS_ACCESS_KEY` in `.env` or 1Password, never in code. `.env` is readable only by you and ignored by git
- Run on your computer, the server binds `127.0.0.1` only. A browser request from a foreign origin gets HTTP 403, which blocks DNS-rebinding attacks from web pages
- `IPS_MCP_BEARER_TOKEN` (optional) makes the server require `Authorization: Bearer <token>`. The catalog entry above sends no token, so leave it blank while the gateway fronts the server
- The container runs as an unprivileged user on a read-only file system, with no capabilities

## Going further

The official MCP Python SDK (FastMCP) turns a tool into a decorated function and hides the handshake and transport code you just wrote. This exercise builds it by hand so you know what that code does. To see how a server goes wrong, continue with the [MCP Security Lab](MCP_Security_Lab.md).

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| The self-test still lists open TODOs after your edit | The check names the TODO and the reason | Read the `-> ...` hint on the `FAIL` line |
| `curl` gets `Connection refused` | The server is not running, or it runs in the container (no host port) | Start it with `python3 ips_cve_mcp.py` |
| `tools/call` returns `IPS_CLIENT_ID / IPS_ACCESS_KEY are not set` | No key in that environment | Set the key in `.env` and use the lab service (Step 4) |
| `authentication ... failed: HTTP 401` or `403` | Wrong key type or region | Use an Account API key, and set `IPS_REGION` to your portal region |
| The container still runs the solution | It was not rebuilt, or `IPS_CVE_VARIANT` is not `scaffold` | Set `IPS_CVE_VARIANT=scaffold`, then `docker compose up -d --build ips-cve-mcp` |
| The gateway check finds neither tool | The gateway started before your server, or the catalog entry or `--servers` change is missing | Check both, then `docker compose restart mcp-gateway` |
| The agent never calls your tools | They are not selected in the agent | Step 6 |
