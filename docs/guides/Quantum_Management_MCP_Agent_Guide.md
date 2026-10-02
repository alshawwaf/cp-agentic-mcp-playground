# Management Agents (MCP Gateway and Direct)

The Management agents read your security policy on the Management Server: objects, access and NAT
rules, layers, gateways, clusters, and VPN communities. They work with a Security Management Server, a
Multi-Domain Server, or Smart-1 Cloud, through the Management API.

Two agents ship in n8n, Flowise, and Langflow: **Management Agent (MCP Gateway)** and **Management Agent (Direct)**. They have the same 50 tools and the same prompt. Only the path of the tool calls differs.

Every tool of these agents only reads. The agents never change your environment. When you ask for a change, they draft it for an administrator to apply in SmartConsole.

## At a glance

|  | MCP Gateway agent | Direct agent |
|---|---|---|
| Agent name | Management Agent (MCP Gateway) | Management Agent (Direct) |
| Endpoint | `http://mcp-gateway:8080/mcp`, Bearer token `MCP_GATEWAY_TOKEN` | `http://mcp-quantum-management:3002`, no token (internal `lab` network only) |
| Tools | 50 of the gateway's 190, selected | All 50 of the server |
| n8n MCP Client Tool node | `MCP Gateway` | `Management MCP` |
| Tool names the model sees in n8n | `MCP_Gateway_management__init` | `Management_MCP_management__init` |
| n8n workflow file | `n8n/backup/workflows/quantum-management-via-gateway.json` | `n8n/backup/workflows/quantum-management-mcp.json` |
| Flowise file | `integrations/flowise/quantum-management.flowdata.json` | `integrations/flowise/direct-management.flowdata.json` |
| Langflow file | `integrations/langflow/quantum-management.flow.json` | `integrations/langflow/direct-management.flow.json` |

The MCP server runs as the Compose service `mcp-quantum-management` (catalog key `quantum-management` in `mcp-gateway/catalog.yaml`).

## Tools (50)

- **Session:** `management__init`
- **Access and NAT policy:** `show_access_layers`, `show_access_layer`, `show_access_rulebase`, `show_access_section`, `show_access_rule`, `show_nat_rulebase`, `show_nat_section`, `find_zero_hits_rules`
- **Gateways and clusters:** `management__show_gateways_and_servers`, `show_simple_gateways`, `show_simple_gateway`, `show_simple_clusters`, `show_simple_cluster`, `show_cluster_members`, `show_cluster_member`, `show_lsm_gateways`, `show_lsm_gateway`, `show_lsm_clusters`, `show_lsm_cluster`
- **VPN communities:** `show_vpn_communities_star`, `show_vpn_community_star`, `show_vpn_communities_meshed`, `show_vpn_community_meshed`, `show_vpn_communities_remote_access`, `show_vpn_community_remote_access`
- **Multi-Domain:** `show_domains`, `show_mdss`
- **Network objects:** `show_hosts`, `show_networks`, `show_groups`, `show_address_ranges`, `show_multicast_address_ranges`, `show_wildcards`, `show_security_zones`, `show_dynamic_objects`, `show_dns_domains`, `show_time_groups`, `show_access_point_names`, `show_tags`, `management__show_objects`, `management__show_object`
- **Services and applications:** `show_services_tcp`, `show_services_udp`, `show_services_icmp`, `show_services_icmp6`, `show_service_groups`, `show_application_sites`, `show_application_site_groups`, `show_application_site_categories`

The agent calls `management__init` once per conversation to sign in to the Management Server.

## Prerequisites

1. **The lab is running.** `./scripts/doctor.sh --post-start` reports `no blockers` (with 1Password references in `.env`: `op run --env-file=.env -- ./scripts/doctor.sh --post-start`)
2. **A model for `lab-chat`.** One model provider key in `.env`, or the local Ollama model (slow on a CPU). Every agent calls `lab-chat` through LiteLLM; builders never hold provider keys
3. **The Management settings in `.env`** (table below). `./setup.sh` asks for them, or edit `.env` (with `./setup.sh --1password`, `.env` holds `op://` references instead of values). Then run `docker compose up -d`: it recreates the servers whose settings changed. With 1Password references in `.env`, start every `docker compose` command in this guide with `op run --env-file=.env --` ([Secrets and 1Password](../REFERENCE.md#secrets-and-1password))
4. **Sign-in.** The lab admin for n8n, Flowise, and Langflow: `N8N_ADMIN_EMAIL` (default `admin@lab.local`) and `N8N_ADMIN_PASSWORD`

| Setting | Value |
|---|---|
| `MANAGEMENT_HOST` | Host name or IP address of the Security Management Server or Multi-Domain Server, without `https://`. It must be reachable from the Docker host. |
| `S1C_URL` | Instead of `MANAGEMENT_HOST`, for Smart-1 Cloud: the Web API URL of your tenant, without `/login` (Smart-1 Cloud portal: **Settings > API & SmartConsole**). |
| `MANAGEMENT_API_KEY` | API key of a management administrator. On-premises: SmartConsole, **Manage & Settings > Permissions & Administrators > Administrators**, Authentication Method **API Key**. Smart-1 Cloud accepts an API key only. |
| `MANAGEMENT_USERNAME`, `MANAGEMENT_PASSWORD` | Optional, on-premises only: a user name and password instead of the API key. Set both or neither. |
| `MANAGEMENT_PORT` | Optional. Management API port of an on-premises server. Default `443`. |
| `MANAGEMENT_CA_CERT`, `MANAGEMENT_TLS_SERVERNAME` | Optional. For a self-signed certificate, see **Certificates** below. |

### Certificates

The MCP servers always verify the Management Server's TLS certificate. If it is self-signed:

1. Save the certificate as `certs/sms.pem` and confirm its SHA-256 fingerprint with the server's
   administrator. The commands are in [`certs/README.md`](../../certs/README.md)
2. Set `MANAGEMENT_CA_CERT=/certs/sms.pem` in `.env`. The `certs` folder is mounted read-only at
   `/certs` in the MCP server containers
3. If you connect by IP address and the certificate names a host, set `MANAGEMENT_TLS_SERVERNAME` to
   that host name
4. Recreate the servers: `docker compose up -d`

## Chat with the agents

### n8n

1. Sign in to n8n and open the workflow **Management Agent (Direct)**
2. Click **Open chat** and send one of the prompts below
3. Open the **Executions** tab and the latest run. The MCP Client Tool **Management MCP** shows the tools the agent called, for example `Management_MCP_management__init`
4. Repeat with **Management Agent (MCP Gateway)**. Its MCP Client Tool **MCP Gateway** selects the same 50 tools under **Tools to Include** and uses the credential **MCP Gateway Bearer**. The tool calls appear as `MCP_Gateway_management__init`

The chat trigger also has a public **Chat URL** (it ends in `/webhook/<id>/chat`). That page asks for HTTP Basic authentication: the lab admin email and password (credential **Lab Agents Chat**).

### Flowise

1. Sign in to Flowise, open **Chatflows**, and open **Management Agent (Direct)** or **Management Agent (MCP Gateway)**
2. Click the chat icon at the top right of the canvas and send a prompt
3. The **Custom MCP** node shows the endpoint under **MCP Server Config** and the 50 selected tools under **Available Actions**. The gateway agent sends `Authorization: Bearer {{$vars.MCP_GATEWAY_TOKEN}}`, the Flowise variable that `builders-import` keeps in sync with `.env`

### Langflow (Complete lab)

Langflow runs in the Complete lab (`COMPOSE_PROFILES=complete`), or on its own with the `langflow` profile.

1. Sign in to Langflow and open the flow **Management Agent (Direct)** or **Management Agent (MCP Gateway)**
2. Click **Playground** (Langflow's chat panel) and send a prompt
3. In the gateway flow, the **MCP Tools** node lists all 190 gateway tools under **Actions**, with only this server's 50 switched on

## Try these

- *Show me all the gateways and their IPs*
- *List the rules in the Network layer as a table*
- *Which access rules have zero hits?*
- *List the hosts and their IP addresses*

## Expected result

The agent first calls `management__init` to sign in to the Management Server, then the read tools it
needs. Expect short Markdown answers: a table of gateways and servers with their IP addresses, the
rules of a layer as a table, or the rules without hits (`find_zero_hits_rules`). Long lists show the
most relevant items and say how many more exist. Without management settings, the tool calls fail and
the agent reports the error.

## How the agents are built

| n8n node | Type | What it does |
|---|---|---|
| `When chat message received` | Chat Trigger | Starts one run per message. Shows the greeting and starter prompts. Public chat behind HTTP Basic authentication (credential **Lab Agents Chat**) |
| `Normalize input` | Edit Fields | Takes `chatInput` and `sessionId` from the chat, or from the body of a webhook call |
| `Management Agent` | AI Agent | Holds the system prompt and decides which tools to call. On an error it continues to `Friendly error` |
| `OpenAI Chat Model` | OpenAI Chat Model | Model `lab-chat` with the credential **Lab Model (LiteLLM)** (`http://litellm:4000/v1`) |
| `Conversation Memory` | Simple Memory | Keeps the recent turns of the chat session |
| `Management MCP` or `MCP Gateway` | MCP Client Tool | The 50 tools: direct to `http://mcp-quantum-management:3002`, or through the gateway |
| `Friendly error` | Code | Turns a failure into a plain explanation with the command that fixes it |

In Flowise, a **Tool Agent** uses the **OpenAI** chat model node (`lab-chat`, credential **Lab Model (LiteLLM)**, Base Path `http://litellm:4000/v1`), **Buffer Memory**, and one **Custom MCP** node. In Langflow, **Chat Input** feeds an **Agent** with the **OpenAI** component (`lab-chat`, OpenAI API Base `http://litellm:4000/v1`, API key from the global variable `LITELLM_MASTER_KEY`) and one **MCP Tools** node, and the answer goes to **Chat Output**. For a node-by-node walkthrough, see the [Threat Prevention deep dive](CheckPoint_Threat_Prevention_Guide.md).

**Updates.** `n8n-import` and `builders-import` re-import these agents on every deploy and keep an agent you changed. To keep your own variant, duplicate it first. `N8N_SEED_OVERWRITE=1` (n8n) or `SEED_OVERWRITE=1` (Flowise and Langflow) replaces a changed agent with the repository version; set it back to `0` after that run.

**Data handling.** Every tool result goes to the model provider behind `lab-chat`. With a cloud provider, that is an external service. Use lab data only. Never connect the lab to customer environments or load customer data unless the provider is approved for that data.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Either management host or S1C URL must be provided` | No management settings in `.env` | Set `MANAGEMENT_HOST` (or `S1C_URL`) and `MANAGEMENT_API_KEY`, then `docker compose up -d mcp-quantum-management` |
| `docker compose logs mcp-quantum-management` shows `WARNING: MANAGEMENT_HOST is set without MANAGEMENT_API_KEY` | A host without a way to sign in is ignored | Add `MANAGEMENT_API_KEY` (or `MANAGEMENT_USERNAME` and `MANAGEMENT_PASSWORD`), then `docker compose up -d mcp-quantum-management` |
| `TLS certificate verification failed` | Self-signed certificate, or the name does not match | See **Certificates** above |
| `MCP error -32001: Request timed out` | `MANAGEMENT_HOST` is unreachable from the Docker host, or replies do not route back | `./scripts/doctor.sh --preflight --online` tests reachability. See **Lab connectivity** in the [Direct and MCP Gateway agents lab](MCP_Gateway_Agent_Guide.md) |
| HTTP 429 (too many requests) from the Management Server | The Management Server limits logins; every new MCP session logs in | Wait one or two minutes. Keep one chat session per exercise |
| The MCP Gateway agent answers with HTTP 401 | The token in the builder differs from `MCP_GATEWAY_TOKEN` in `.env` | n8n: `docker compose run --rm n8n-import`. Flowise and Langflow: `docker compose run --rm builders-import` |
| The MCP Gateway agent has no tools, or fails while the Direct agent works | The gateway started before the server was ready, or still uses sessions to an old server container | `docker compose restart mcp-gateway` |
| **Friendly error**: the lab model endpoint is not reachable | LiteLLM is not running | `docker compose up -d litellm` |
| **Friendly error**: LiteLLM could not serve the `lab-chat` model | No working model provider key, or the provider failed | Check the key in `.env`, then `docker compose logs litellm` |
| The hosted chat URL asks for a password | HTTP Basic authentication on the chat trigger | Sign in with the lab admin email and password |

---

*Related:* [The MCP Gateway, Explained](MCP_Gateway_Explained.md) · [Direct and MCP Gateway agents lab](MCP_Gateway_Agent_Guide.md) · [Management Logs](Management_Logs_MCP_Agent_Guide.md) · [Threat Prevention](Threat_Prevention_MCP_Agent_Guide.md)
