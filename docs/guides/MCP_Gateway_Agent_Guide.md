# Direct and MCP Gateway Agents: Hands-On Lab

An AI agent in this lab reaches a Check Point MCP server in one of two ways:

1. **Direct.** The agent connects to one product's MCP server, for example
   `http://mcp-quantum-management:3002`. No token: the server is reachable only on the internal
   `lab` network
2. **MCP Gateway.** The agent connects to `http://mcp-gateway:8080/mcp`, which fronts 11 Check Point
   MCP servers (190 tools) and requires the Bearer token `MCP_GATEWAY_TOKEN`

Each of the 11 products behind the gateway has one agent for each path, with the same tools and the
same prompt. In this lab you run both, compare them, and see what the gateway adds. For the concepts
behind it, read [The MCP Gateway, Explained](MCP_Gateway_Explained.md) first.

---

## Architecture

```
  n8n, Flowise, Langflow
   |
   |-- "<Product> Agent (Direct)" ------- HTTP, no token -------> one MCP server ----+
   |                                                                                  |
   |-- "<Product> Agent (MCP Gateway)" -- HTTP + Bearer token --> mcp-gateway:8080 ---+--> 11 MCP servers
   |                                                                                  |
   |-- "Check Point MCP Gateway Agent" -- HTTP + Bearer token --> mcp-gateway:8080 ---+
                                                                                      |
                                                       Your Check Point environment <-+
                                                       (Management Server, gateways, Check Point cloud services)
```

The gateway and the MCP servers have no host ports. Only containers on the `lab` network reach them.

---

## The agent pairs

The names are the same in n8n, Flowise, and Langflow.

| Product | MCP Gateway agent | Direct agent | Direct endpoint | Tools |
|---|---|---|---|---|
| Documentation | Documentation Agent (MCP Gateway) | Documentation Agent (Direct) | `http://mcp-documentation:3000` | 1 |
| Management | Management Agent (MCP Gateway) | Management Agent (Direct) | `http://mcp-quantum-management:3002` | 50 |
| Management Logs | Management Logs Agent (MCP Gateway) | Management Logs Agent (Direct) | `http://mcp-management-logs:3003` | 7 |
| Policy Insights | Policy Insights Agent (MCP Gateway) | Policy Insights Agent (Direct) | `http://policy-insights-mcp:3013` | 10 |
| Threat Prevention | Threat Prevention Agent (MCP Gateway) | Threat Prevention Agent (Direct) | `http://threat-prevention-mcp:3005` | 25 |
| HTTPS Inspection | HTTPS Inspection Agent (MCP Gateway) | HTTPS Inspection Agent (Direct) | `http://mcp-https-inspection:3001` | 9 |
| Gaia | Gaia Agent (MCP Gateway) | Gaia Agent (Direct) | `http://quantum-gaia-mcp:3011/mcp` | 42 |
| Gateway CLI | Gateway CLI Agent (MCP Gateway) | Gateway CLI Agent (Direct) | `http://quantum-gw-cli-mcp:3009` | 26 |
| CPInfo Analysis | CPInfo Analysis Agent (MCP Gateway) | CPInfo Analysis Agent (Direct) | `http://cpinfo-analysis-mcp:3012` | 12 |
| Reputation Service | Reputation Service Agent (MCP Gateway) | Reputation Service Agent (Direct) | `http://reputation-service-mcp:3007` | 3 |
| Threat Emulation | Threat Emulation Agent (MCP Gateway) | Threat Emulation Agent (Direct) | `http://threat-emulation-mcp:3004` | 5 |

One more agent uses the gateway across products: **Check Point MCP Gateway Agent**, with a read-first
core of 48 tools from all 11 servers.

Each product has its own guide with its settings and prompts, for example the
[Management guide](Quantum_Management_MCP_Agent_Guide.md) and the
[Documentation guide](Documentation_MCP_Agent_Guide.md).

---

## Prerequisites

1. **The lab is running.** `./scripts/doctor.sh --post-start` reports `no blockers`, and
   `190 tools with MCP_GATEWAY_TOKEN`. With 1Password references in `.env`, run it as
   `op run --env-file=.env -- ./scripts/doctor.sh --post-start`
2. **A model for `lab-chat`.** Every agent uses `lab-chat` through LiteLLM. Give one provider key in
   `.env` (Azure OpenAI needs `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, and
   `AZURE_OPENAI_DEPLOYMENT`; or `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or `GEMINI_API_KEY`). With no key,
   `lab-chat` runs on the local Ollama model, which is slow on a CPU
3. **The settings of the product you test.** This lab starts with the Documentation agents, which
   need only `DOC_CLIENT_ID`, `DOC_SECRET_KEY`, and `DOC_REGION` (EU or US). The Management agents need
   `MANAGEMENT_HOST` (or `S1C_URL`) and `MANAGEMENT_API_KEY`. `./setup.sh` asks for each product's
   settings; after a change run `docker compose up -d`. With 1Password references in `.env`, start
   every `docker compose` command in this lab with `op run --env-file=.env --`
4. **Sign-in.** n8n, Flowise, and Langflow share the lab admin: `N8N_ADMIN_EMAIL` (default
   `admin@lab.local`) and `N8N_ADMIN_PASSWORD` from `.env` (or from your 1Password item)
5. **The builder addresses.** On a lab host: `https://n8n.<DOMAIN>`, `https://flowise.<DOMAIN>`, and
   `https://langflow.<DOMAIN>`. On your own computer the lab publishes no ports; publish them on
   `127.0.0.1` in a local `docker-compose.override.yml` next to `docker-compose.yml` (it is
   git-ignored), then run `docker compose up -d`:

   ```yaml
   services:
     n8n:
       ports: ["127.0.0.1:5678:5678"]
     flowise:
       ports: ["127.0.0.1:3020:3020"]   # both sides equal FLOWISE_PORT
     langflow:
       ports: ["127.0.0.1:7860:7860"]   # Complete lab, or the langflow profile
   ```

Langflow runs in the Complete lab (`COMPOSE_PROFILES=complete`), or on its own with the `langflow`
profile. The Standard lab has n8n and Flowise.

---

## Walkthrough A: the direct path (n8n)

1. Sign in to n8n and open the workflow **Documentation Agent (Direct)**
2. Look at the canvas: `When chat message received` → `Normalize input` → `Documentation Agent`,
   with `OpenAI Chat Model`, `Conversation Memory`, and the MCP Client Tool **Documentation MCP**
   attached. The MCP node points at `http://mcp-documentation:3000` and has no credential
3. Click **Open chat** and ask: *How do I enable Identity Awareness on R82?*
4. Open the **Executions** tab and the latest run. The tool call is
   `Documentation_MCP_ask-checkpoint-docs`

Expected result: an answer from Check Point documentation, with sources.

## Walkthrough B: the MCP Gateway path (n8n)

1. Open the workflow **Documentation Agent (MCP Gateway)**
2. Open the MCP Client Tool node **MCP Gateway**. It points at `http://mcp-gateway:8080/mcp`, uses
   the credential **MCP Gateway Bearer**, and under **Tools to Include** selects one tool:
   `ask-checkpoint-docs`
3. Click **Open chat** and ask the same question
4. In **Executions**, the tool call is now `MCP_Gateway_ask-checkpoint-docs`

Expected result: the same answer. The tools and the prompt are the same; only the path differs. The
acceptance test `N8N-RUN` runs exactly this pair.

## Walkthrough C: one endpoint, many products

1. Open the workflow **Check Point MCP Gateway Agent**. Its MCP Client Tool **MCP Gateway** selects
   48 of the 190 gateway tools, from all 11 servers
2. Click **Open chat** and ask questions that span products, for example:
   - *Which gateways do I have, and what are their IP addresses?* (Management)
   - *Show the Threat Prevention profiles* (Threat Prevention)
   - *What is the reputation of 8.8.8.8?* (Reputation Service)
3. In **Executions**, see the tools of several servers called through one node and one token

Expected result: answers from each product you configured. For a product without settings, the tool
call fails and the agent reports why.

## The same agents in Flowise and Langflow

**Flowise.** Open **Chatflows**, open **Documentation Agent (Direct)**, and click the chat icon at the
top right of the canvas. Then do the same with **Documentation Agent (MCP Gateway)**. The **Custom
MCP** node shows the endpoint under **MCP Server Config** and the selected tool under **Available
Actions**. The gateway agent sends `Authorization: Bearer {{$vars.MCP_GATEWAY_TOKEN}}`; the value is
the Flowise variable `MCP_GATEWAY_TOKEN`, which `builders-import` keeps in sync with `.env`.

**Langflow (Complete lab).** Open the flow **Documentation Agent (MCP Gateway)** and click
**Playground** (Langflow's chat panel). The **MCP Tools** node lists all 190 gateway tools under
**Actions**, with only `ask-checkpoint-docs` switched on. The direct flow's **MCP Tools** node points
at `http://mcp-documentation:3000`.

The acceptance tests `FLOWISE-RUN` and `LANGFLOW-RUN` run these pairs.

---

## Exercises

1. **Compare the tool lists.** Open the MCP node of **Management Agent (Direct)** and of **Management
   Agent (MCP Gateway)**. The direct node binds all 50 tools of its server. The gateway node selects
   the same 50 from 190. Where do the other 140 come from?
2. **Break the token on purpose.** In n8n, open the credential **MCP Gateway Bearer**, change one
   character of the token, save, and chat with **Documentation Agent (MCP Gateway)**. The gateway
   answers HTTP 401 and the agent's **Friendly error** step explains it. The direct agent still works.
   Restore the credential from `.env`: `docker compose run --rm n8n-import`
3. **Feel the 128-tool limit.** In Flowise, open **Management Agent (MCP Gateway)** and click
   **Refresh** on **Available Actions**: the list shows all 190 gateway tools. The OpenAI and Azure
   OpenAI APIs accept at most 128 tools per request. Leave the selection at 50, or close the flow
   without saving
4. **Add your own server.** The [Build Your Own MCP Server](Build_Your_Own_MCP_Exercise.md) exercise
   puts a new server behind the gateway. Its tools appear in the gateway catalog without any change to
   the existing agents; you then select them in the agents that should use them
5. **Discuss the policy point.** Every gateway call passes through one endpoint. Where would you
   enforce tool allow and deny lists, argument checks, or description scanning? See section 7 of
   [The MCP Gateway, Explained](MCP_Gateway_Explained.md) and the [MCP Security Lab](MCP_Security_Lab.md)

---

## Chat outside the editor

- **Open chat** in the n8n editor needs no extra sign-in
- Every agent's chat trigger is public and shows a **Chat URL** that ends in `/webhook/<id>/chat`. That
  hosted chat page, and the same URL called as a webhook, ask for HTTP Basic authentication: the lab
  admin email and password (n8n credential **Lab Agents Chat**, filled in by `n8n-import`)
- Flowise's prediction API (`/api/v1/prediction`) needs the Flowise API key **Lab Agents API**. The
  chat on the Flowise canvas does not

## How the lab keeps the agents in sync

- `n8n-import` runs at every start. It re-syncs the n8n credentials from `.env` (edits made in the
  n8n UI are replaced), imports the workflows, and publishes them. A workflow whose prerequisites are
  missing (for example a token or `DOMAIN`) is imported but not published, and the log says what it
  needs. A workflow you changed in n8n is kept and named in the log; `N8N_SEED_OVERWRITE=1` replaces it
  with the repository version. To keep your own variant, duplicate the workflow first
- `builders-import` does the same for Flowise and Langflow: it re-syncs the model credential and the
  variables from `.env`, updates seeded flows in place, and keeps flows you changed unless
  `SEED_OVERWRITE=1`
- To re-run them after a change to `.env`: `docker compose run --rm n8n-import` and
  `docker compose run --rm builders-import`

---

## Lab connectivity: reaching the Management Server

The MCP servers of the Management, Management Logs, Threat Prevention, HTTPS Inspection, Policy
Insights, and Gateway CLI agents call the Management API at
`https://<MANAGEMENT_HOST>:<MANAGEMENT_PORT>/web_api`. The servers of the Documentation, Reputation
Service, and Threat Emulation agents call Check Point cloud services instead, so those agents work
while the Management Server is unreachable.

1. **`MANAGEMENT_HOST` is reachable from the Docker host.** Host name or IP address only, no
   `https://`. `./scripts/doctor.sh --preflight --online` tests TCP reachability of the Check Point
   hosts in `.env`
2. **The certificate is trusted.** TLS verification is always on. For a self-signed Management
   Server, save its certificate as `certs/<name>.pem`, confirm its fingerprint with the administrator,
   and set `MANAGEMENT_CA_CERT=/certs/<name>.pem`. If you connect by IP address and the certificate
   names a host, set `MANAGEMENT_TLS_SERVERNAME` to that host name. The steps are in
   [`certs/README.md`](../../certs/README.md)
3. **Replies route back.** If the Management Server's default route points into a training network,
   replies to your Docker host can get lost and tool calls time out. Add a specific route for your
   client subnet in Gaia clish on the Management Server, for example:

   ```
   set static-route <your-client-subnet>/24 nexthop gateway address <lab-router-ip> on
   save config
   ```

**CloudShare labs.** The internal `10.1.1.x` address of the Management Server is not reachable from
outside the environment. Set `MANAGEMENT_HOST` to the Management Server's public IP (CloudShare:
**Networks**, the Management Server's adapter, **Inbound Access: Public IP**). That address can change
when the environment is recreated. `MANAGEMENT_HOST` is the only place to update: the agents point at
the MCP servers, not at the Management Server. After the change run `docker compose up -d` (on a
Dokploy lab host, change it in the project's environment settings and redeploy). If the certificate
does not name the public IP, set `MANAGEMENT_TLS_SERVERNAME` to a host name it does contain. If a
CloudShare revert removed your static route, add it again.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| The gateway agent answers with HTTP 401 | The **MCP Gateway Bearer** credential (n8n) or the builder variable differs from `MCP_GATEWAY_TOKEN` in `.env` | n8n: `docker compose run --rm n8n-import`. Flowise and Langflow: `docker compose run --rm builders-import` |
| A server shows 0 tools through the gateway | The server was not ready when the gateway started | `docker compose restart mcp-gateway` |
| The gateway agent fails, the direct agent works, after you recreated a server | The gateway may still use sessions to the old server container | `docker compose restart mcp-gateway` |
| **Friendly error**: the lab model endpoint is not reachable | LiteLLM is not running | `docker compose up -d litellm` |
| **Friendly error**: LiteLLM could not serve the `lab-chat` model | No working provider key, or the provider failed | Check the key in `.env`, then `docker compose logs litellm` |
| Management tools fail with `MCP error -32001: Request timed out` | `MANAGEMENT_HOST` is unreachable, or replies do not route back | See **Lab connectivity** above |
| `TLS certificate verification failed` | Self-signed certificate, or the name does not match | `MANAGEMENT_CA_CERT` and `MANAGEMENT_TLS_SERVERNAME` ([`certs/README.md`](../../certs/README.md)) |
| `Either management host or S1C URL must be provided` | No management settings in `.env` | Set `MANAGEMENT_HOST` (or `S1C_URL`) and `MANAGEMENT_API_KEY`, then `docker compose up -d` |
| Management tools fail with HTTP 429 (too many requests) | The Management Server limits logins; every new MCP session logs in | Wait one or two minutes. Keep one chat session per exercise |
| The hosted chat URL asks for a password | HTTP Basic authentication on the chat trigger | Sign in with the lab admin email and password |
| The model provider rejects the request: too many tools | The agent binds more than 128 tools | Select fewer tools in the agent's MCP node |
| Your change to a seeded workflow or flow is not replaced after an update | Changed agents are kept on purpose | `N8N_SEED_OVERWRITE=1` (n8n) or `SEED_OVERWRITE=1` (Flowise, Langflow) for one run, then set it back to `0` |

---

## Reference

- `docker-compose.yml`, service `mcp-gateway`: the `--servers=` list, `MCP_GATEWAY_AUTH_TOKEN` from
  `MCP_GATEWAY_TOKEN`, and `depends_on` on all 11 servers
- `mcp-gateway/catalog.yaml`: the servers the gateway fronts
- n8n: credential **MCP Gateway Bearer** (`n8n/backup/credentials_public/gateway-bearer.json`); the
  gateway workflows `n8n/backup/workflows/*-via-gateway.json` and `mcp-gateway-agent.json`
- Flowise and Langflow: `integrations/flowise/` and `integrations/langflow/`, listed with their agent
  names and tool counts in `integrations/builders_agents.json`
- Tool scopes: `SERVER_TOOLS` in `scripts/flows/langflow_fix.py`
