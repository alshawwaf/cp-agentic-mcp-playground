# The MCP Gateway, Explained

This guide explains what the `mcp-gateway` service does, how the lab configures it, and how the
agents use it. Every name, port, and count below comes from the lab's files and test runs, so you can
check each one yourself.

For the hands-on comparison of the two paths, see the
[Direct and MCP Gateway agents lab](MCP_Gateway_Agent_Guide.md).

---

## 1. What it is

The MCP Gateway is a single front door for the Check Point MCP servers. MCP, the Model Context
Protocol, is the open standard an AI agent uses to discover and call tools. The lab runs one MCP
server per Check Point product. The gateway puts 11 of them behind one endpoint and one token:

```
http://mcp-gateway:8080/mcp        Streamable HTTP, "Authorization: Bearer <MCP_GATEWAY_TOKEN>"
```

A client connects once. The gateway lists the tools of all 11 servers (190 tools) and forwards each
tool call to the server that owns the tool. It is the same idea as an API gateway, applied to agent
tools.

The service runs Docker's MCP Gateway image, `docker/mcp-gateway:v0.44.1`, pinned by digest in
`docker-compose.yml`.

---

## 2. Direct or MCP Gateway

Each of the 11 Check Point products behind the gateway has two agents with the same tools and the
same prompt. Only the path of the tool calls differs.

| | Direct agent | MCP Gateway agent |
|---|---|---|
| Example | Management Agent (Direct) | Management Agent (MCP Gateway) |
| Endpoint | The product's own server, for example `http://mcp-quantum-management:3002` | `http://mcp-gateway:8080/mcp` |
| Authentication | None. The server is reachable only on the internal `lab` network | Bearer token `MCP_GATEWAY_TOKEN` on every request |
| Tools the gateway or server offers | That server's tools only | 190 tools from 11 servers |
| Tools the agent binds | All tools of its server | The same tools, selected from the 190 |
| One place for authentication and policy | No | Yes |

Use the direct path when an agent needs one product and you want the shortest path. Use the gateway
when you want one authenticated endpoint for many products, or one place to apply controls. The cost
of the gateway is its large catalog: an agent must select the tools it needs (section 5).

---

## 3. How the lab configures it

Two files define the gateway. Keep them in sync: a custom catalog does not turn its servers on by
itself, so each server must also be named in `--servers=`.

### `mcp-gateway/catalog.yaml`

The catalog declares each server as a `remote` MCP server with a `streamable` transport:

| Product | Catalog key | Server URL | Tools | Needs in `.env` |
|---|---|---|---|---|
| Documentation | `documentation` | `http://mcp-documentation:3000` | 1 | `DOC_CLIENT_ID`, `DOC_SECRET_KEY`, `DOC_REGION` |
| Management | `quantum-management` | `http://mcp-quantum-management:3002` | 50 | Management settings |
| Policy Insights | `policy-insights` | `http://policy-insights-mcp:3013` | 10 | Management settings (Management API v2.1, R82.10 or later, with Policy Insights enabled) |
| CPInfo Analysis | `cpinfo-analysis` | `http://cpinfo-analysis-mcp:3012` | 12 | None. Reads CPInfo files from `n8n/shared` |
| HTTPS Inspection | `https-inspection` | `http://mcp-https-inspection:3001` | 9 | Management settings |
| Management Logs | `management-logs` | `http://mcp-management-logs:3003` | 7 | Management settings |
| Gaia | `gaia` | `http://quantum-gaia-mcp:3011/mcp` | 42 | `GAIA_GATEWAY_IP`, `GAIA_USERNAME`, `GAIA_PASSWORD` |
| Gateway CLI | `gw-cli` | `http://quantum-gw-cli-mcp:3009` | 26 | Management settings |
| Reputation Service | `reputation-service` | `http://reputation-service-mcp:3007` | 3 | `REPUTATION_API_KEY` |
| Threat Emulation | `threat-emulation` | `http://threat-emulation-mcp:3004` | 5 | `TE_API_KEY` |
| Threat Prevention | `threat-prevention` | `http://threat-prevention-mcp:3005` | 25 | Management settings |
| **Total** | | | **190** | |

"Management settings" means `MANAGEMENT_HOST` (or `S1C_URL` for Smart-1 Cloud) plus
`MANAGEMENT_API_KEY` (or, for an on-premises server only, `MANAGEMENT_USERNAME` and `MANAGEMENT_PASSWORD`).

The tool counts are the live `tools/list` answers of each server. The lab keeps them in
`SERVER_TOOLS` in `scripts/flows/langflow_fix.py`, and the acceptance tests compare the running
gateway against that list. Tool names are unique across the 11 servers (several servers prefix their
own, for example `management__init` and `management-logs__init`), so the gateway lists them side by
side.

Three of the 14 Check Point MCP servers in the lab are not behind the gateway: `spark-management-mcp`
(Spark Management), `harmony-sase-mcp` (SASE), and `quantum-gw-connection-analysis-mcp` (Gateway
Connection Analysis).
Spark Management and SASE stop with one "not configured" log line until their settings are in
`.env`.

### The `mcp-gateway` service in `docker-compose.yml`

The parts that matter:

```yaml
mcp-gateway:
  image: docker/mcp-gateway:v0.44.1@sha256:...
  networks: [ "lab" ]
  command:
    - "--transport=streaming"
    - "--port=8080"
    - "--catalog=checkpoint-mcp.yaml"
    - "--servers=documentation,quantum-management,policy-insights,cpinfo-analysis,https-inspection,management-logs,gaia,gw-cli,reputation-service,threat-emulation,threat-prevention"
    - "--preserve-tool-schema-dialect"
  environment:
    - DOCKER_MCP_IN_CONTAINER=1
    - DOCKER_HOST=tcp://docker-socket-proxy:2375
    - DOCKER_MCP_ALLOW_INSECURE_REMOTE_URLS=1
    - MCP_GATEWAY_AUTH_TOKEN=${MCP_GATEWAY_TOKEN:?MCP_GATEWAY_TOKEN is not set. Run ./setup.sh}
  volumes:
    - ./mcp-gateway/catalog.yaml:/root/.docker/mcp/catalogs/checkpoint-mcp.yaml:ro
  depends_on:              # the socket proxy and all 11 servers, each with condition: service_healthy
```

What each part does:

- **The token is required and fixed.** `./setup.sh` generates `MCP_GATEWAY_TOKEN` in `.env`. Compose
  passes it to the gateway as `MCP_GATEWAY_AUTH_TOKEN`, and refuses to start the lab without it.
  Without a fixed token the gateway would create a new random token on every restart, and every
  client would get HTTP 401
- **The gateway waits for its servers.** It lists each server's tools once, when it starts. A server
  that is not listening yet would stay at 0 tools until the gateway restarts. So the gateway waits
  until the socket proxy and all 11 servers report healthy
- **No host port.** The gateway is reachable only from containers on the `lab` network. Your browser
  and your host shell cannot reach it directly
- **Read-only Docker access.** The gateway has no Docker socket. It reads containers and networks
  through `docker-socket-proxy`, which refuses every write request (`POST=0`), opens only the
  containers and networks API sections (`CONTAINERS=1`, `NETWORKS=1`), and mounts the Docker socket
  read-only. The gateway itself runs with all capabilities dropped
- **Plain HTTP inside the lab.** The servers speak plain HTTP on the private `lab` network, which
  `DOCKER_MCP_ALLOW_INSECURE_REMOTE_URLS=1` allows
- **Runs in a container.** `DOCKER_MCP_IN_CONTAINER=1` is required when the gateway runs in a
  container outside Docker Desktop (a lab host, Docker Engine)
- **Tool schemas pass through unchanged.** `--preserve-tool-schema-dialect` keeps each server's tool
  schemas exactly as the server publishes them

At start-up the gateway logs about 90 `audit event dropped due to backpressure` lines. They are
harmless: outside Docker Desktop the gateway's audit is a no-op, and its queue is smaller than the
190 tools it lists.

---

## 4. How it works at runtime

MCP is stateful. A client opens a session before it can list or call tools:

```
1. POST initialize                 -> the reply carries an Mcp-Session-Id header
2. POST notifications/initialized  -> the client confirms it is ready (no reply)
3. POST tools/list                 -> the catalog: 190 tools
4. POST tools/call                 -> one tool call
5. DELETE                          -> closes the session
```

Rules every client follows:

- Send `Authorization: Bearer <MCP_GATEWAY_TOKEN>` on every request. A missing or wrong token gets
  HTTP 401
- Send the `Mcp-Session-Id` from step 1 on every later request. A `tools/list` without a session is
  rejected with a JSON-RPC error (`method "tools/list" is invalid during session initialization`)
- Send `Accept: application/json, text/event-stream`. The gateway answers HTTP 400 without both
- Read replies as Server-Sent Events: each reply is an `event: message` frame with the JSON in a
  `data:` line
- If the gateway answers HTTP 404 to a session ID (it restarted or closed an idle session), start a
  new session

The agent builders do all of this for you. To see it by hand, try the two checks below. Both run in
a throwaway container on the lab network, because the gateway has no host port.

**Find the lab network.** Its name is `<project>_lab`, for example
`cp-agentic-mcp-playground_lab`:

```bash
docker network ls --filter name=_lab
```

**Check 1: no token, no access.** This request has no `Authorization` header:

```bash
docker run --rm --network <project>_lab curlimages/curl:8.22.0 \
  -s -o /dev/null -w '%{http_code}\n' -X POST http://mcp-gateway:8080/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}'
```

Expected result: `401`.

**Check 2: the full handshake.** `integrations/code-agent/mcp_gateway_client.py` is a standard-library
Python client that runs steps 1 to 5 and prints each one. Pass only the token into the container,
never the whole `.env`. Run it from `integrations/code-agent` (bash or zsh):

```bash
cd integrations/code-agent
docker run --rm --network <project>_lab \
  --env-file <(grep '^MCP_GATEWAY_TOKEN=' ../../.env) \
  -v "$PWD":/app:ro python:3.12-alpine python /app/mcp_gateway_client.py
```

If `.env` holds 1Password references, resolve them with 1Password CLI:

```bash
op run --env-file=../../.env -- docker run --rm --network <project>_lab \
  -e MCP_GATEWAY_TOKEN -v "$PWD":/app:ro python:3.12-alpine python /app/mcp_gateway_client.py
```

Expected result: `initialize OK` with a session ID, `tools/list returned 190 tools`, one call of the
read-only tool `reputation_ip`, then `session closed (HTTP DELETE)`. Without `REPUTATION_API_KEY`
the tool call reports an error, which is expected. `SAMPLE_TOOL` and `SAMPLE_TOOL_ARGS` choose
another read-only tool.

---

## 5. How the agents use it

Each per-product agent has one MCP node in every builder. A gateway agent points that node at the
gateway and selects only its own server's tools, so it binds the same tools as its direct twin.

| Builder | MCP node | Gateway authentication | How the agent selects its tools |
|---|---|---|---|
| n8n | MCP Client Tool node named `MCP Gateway` | Credential `MCP Gateway Bearer` (filled from `MCP_GATEWAY_TOKEN` by `n8n-import`) | **Tools to Include**: Selected, with the server's tool list |
| Flowise | Custom MCP node | Header `Authorization: Bearer {{$vars.MCP_GATEWAY_TOKEN}}`; `builders-import` keeps the Flowise variable `MCP_GATEWAY_TOKEN` in sync with `.env` | **Available Actions**: the server's tools |
| Langflow | MCP Tools node | Header `Authorization` with the token, filled in by `builders-import` | **Actions**: all 190 tools listed, only the server's tools switched on |

In n8n, tool names reach the model with the node name as a prefix. A gateway agent's tools appear as
`MCP_Gateway_<tool>` (for example `MCP_Gateway_threat-prevention__init`). A direct agent's node is
named after its server, so its tools appear as, for example, `Threat_Prevention_MCP_threat-prevention__init`.

The agents that use the gateway:

| Agent | Tools it binds |
|---|---|
| The 11 per-product agents, "<Product> Agent (MCP Gateway)" | Its own server's tools: 1 to 50 (table in section 3) |
| Check Point MCP Gateway Agent | 48: a read-first core across all 11 servers |
| SOC Response Chain Agent | 62: Management Logs, Reputation Service, Management, and two Threat Prevention IOC feed tools (in n8n, split over three chained agents) |
| Fleet Commander Estate Agent | 117: a curated core across every server |
| Guarded Agent (Lakera Guard) | The same 117, screened by Lakera Guard (needs `LAKERA_API_KEY`) |

No agent binds all 190 tools. The OpenAI and Azure OpenAI APIs accept at most 128 tool definitions per
request, and a long tool list costs tokens and makes tool choice harder. Keep every agent at 128 tools
or fewer.

---

## 6. The model is separate from the tools

MCP governs the tools, not the model. Every seeded agent uses one model, `lab-chat`, through the
lab's LiteLLM proxy (`http://litellm:4000/v1`). `LAB_MODEL_PROVIDER` in `.env` picks the provider:
`auto` (default), `azure`, `openai`, `anthropic`, `gemini`, or `ollama`. To change the provider, edit
`.env` and run `docker compose up -d litellm`. With 1Password references in `.env`, edit the 1Password
item and run `op run --env-file=.env -- docker compose up -d litellm`. The gateway, its token, and its
tools stay the same.

Any MCP client that speaks Streamable HTTP can use the gateway from the lab network with the token.
The code-first agent in `integrations/code-agent` (`agent_loop.py`) is one example: it calls
`lab-chat` and the gateway with nothing but the Python standard library.

**Data handling.** Every tool result (rules, objects, logs, IP addresses) goes to the model provider
behind `lab-chat`. With a cloud provider, that is an external service. Use lab data only. Never load
customer configurations, logs, or CPInfo files unless the provider is approved for that data.

---

## 7. Extending it, and the gateway as the policy point

### Add a server to the gateway

The Build Your Own MCP exercise ([guide](Build_Your_Own_MCP_Exercise.md)) walks through this with
its own server. The steps:

1. Run the server on the `lab` network with a healthcheck. It must accept concurrent sessions: the
   gateway holds several at once. All 14 Check Point servers in this lab already do (see
   `docker/n8n/mcp-src/PATCHES.md`, patches 1 and 11)
2. Add an entry to `mcp-gateway/catalog.yaml` (`type: "remote"`, `remote.url`,
   `transport_type: "streamable"`)
3. Add its key to `--servers=` in `docker-compose.yml`, and add the server to the gateway's
   `depends_on` with `condition: service_healthy`
4. Recreate the gateway: `docker compose up -d mcp-gateway`
5. Select the new tools in the agents that should use them: **Tools to Include** in n8n, **Available
   Actions** (after **Refresh**) in Flowise, and **Actions** in Langflow. Keep each agent at 128 tools or
   fewer

The acceptance check `GW-TOOLS` compares the gateway with `SERVER_TOOLS` in
`scripts/flows/langflow_fix.py`. While the new server is behind the gateway, `GW-TOOLS` fails with
`<n> tools not in SERVER_TOOLS`. That is expected during the exercise. A server that stays in the lab
also needs its tools in `SERVER_TOOLS` and the new total in `EXPECTED_GATEWAY_TOOLS`
(`tests/acceptance/acceptance.py`).

### The gateway as the policy point

Today the lab uses the gateway for one control: a single Bearer token in front of 11 servers. Tool
scoping happens in each agent. The gateway is also the natural place for controls the lab does not
configure: tool allow and deny lists, checks on tool arguments, and scanning of tool descriptions.
Every tool call of every gateway agent passes through it, so this single front door is where such
controls belong. The [MCP Security Lab](MCP_Security_Lab.md) shows the attacks these controls stop.

The Security Lab's intentionally vulnerable server, `vuln-mcp` (profile `security-lab`), stays off the
gateway on purpose. It runs only on the internal `security-lab` network, which has no internet access,
and the gateway is not on that network.

---

## 8. Check it

Two lab tools test the gateway end to end:

```bash
./scripts/doctor.sh --post-start
tests/acceptance/run.sh --only GW-AUTH,GW-TOOLS,DIRECT,CODE-AGENT
```

With 1Password references in `.env`, start both with `op run --env-file=.env --`.

On a healthy lab, `doctor.sh` reports `request without a token: HTTP 401`,
`request with a wrong token: HTTP 401`, `190 tools with MCP_GATEWAY_TOKEN`, and the tool count of each
server. The acceptance checks report:

| Check | Expected result |
|---|---|
| `GW-AUTH` | HTTP 401 without a token and with a wrong token, 200 with `MCP_GATEWAY_TOKEN` |
| `GW-TOOLS` | 190 tools; all 11 servers match `SERVER_TOOLS` |
| `DIRECT` | 11 servers answer `tools/list` directly with their `SERVER_TOOLS` (190 tools) |
| `CODE-AGENT` | `mcp_gateway_client.py`: handshake and `tools/list` OK (190 tools) |

---

## 9. Troubleshooting

With 1Password references in `.env`, start every `docker compose` command below with
`op run --env-file=.env --`.

| Symptom | Cause | Fix |
|---|---|---|
| `docker compose up` stops with `MCP_GATEWAY_TOKEN is not set. Run ./setup.sh` | `.env` has no gateway token | Run `./setup.sh` (it keeps your values and fills the blank secrets), then `docker compose up -d` |
| A server shows 0 tools through the gateway, or `GW-TOOLS` fails with a server at 0 | The server was not ready when the gateway started | `docker compose restart mcp-gateway` |
| Agents get HTTP 401 from the gateway | The token in a builder differs from `MCP_GATEWAY_TOKEN` in `.env` | n8n: `docker compose run --rm n8n-import`. Flowise and Langflow: `docker compose run --rm builders-import` |
| `GW-AUTH` fails: HTTP 401 even with `MCP_GATEWAY_TOKEN` | You changed the token in `.env`, and the gateway still runs with the old one | `docker compose up -d mcp-gateway`, then re-sync the builders as in the row above |
| HTTP 400 from the gateway (hand-written client) | `Accept` does not list both `application/json` and `text/event-stream` | Send both |
| `method "tools/list" is invalid during session initialization` | No `initialize` handshake before `tools/list` | Run `initialize` and `notifications/initialized` first, and send the `Mcp-Session-Id` |
| HTTP 404 for an existing session ID | The gateway restarted or closed the session | Start a new session |
| The model provider rejects the request: too many tools | The agent binds more than 128 tools | Select fewer tools in the agent's MCP node |
| The gateway agent fails after you changed `.env` and recreated a server, while the direct agent works | The gateway may still use sessions it opened to the old server container | `docker compose restart mcp-gateway` |
| About 90 `audit event dropped due to backpressure` lines in `docker compose logs mcp-gateway` | Normal start-up output | None needed |

---

*Related:* [Direct and MCP Gateway agents lab](MCP_Gateway_Agent_Guide.md) ·
[Build Your Own MCP Server](Build_Your_Own_MCP_Exercise.md) · [MCP Security Lab](MCP_Security_Lab.md) ·
`integrations/code-agent/` (the code-first agent).
