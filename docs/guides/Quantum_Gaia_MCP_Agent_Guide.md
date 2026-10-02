# Gaia Agents (MCP Gateway and Direct)

The Gaia agents read the Gaia operating system configuration of a Security Gateway through its Gaia REST
API: interfaces, routes, BGP and OSPF, DNS, ARP, DHCP, NAT pools, and more. Gaia talks to the gateway
itself, not to the Management Server, so it needs its own settings.

Two agents ship in n8n, Flowise, and Langflow: **Gaia Agent (MCP Gateway)** and **Gaia Agent (Direct)**. They have the same 42 tools and the same prompt. Only the path of the tool calls differs.

Every tool of these agents only reads. No tool changes interfaces, routes, DNS, or NTP. When you ask for
an operating system change, the agent gives the Gaia clish commands an administrator would run and
says that it did not apply them. `manage_gaia_credentials` only clears cached gateway logins.

## At a glance

|  | MCP Gateway agent | Direct agent |
|---|---|---|
| Agent name | Gaia Agent (MCP Gateway) | Gaia Agent (Direct) |
| Endpoint | `http://mcp-gateway:8080/mcp`, Bearer token `MCP_GATEWAY_TOKEN` | `http://quantum-gaia-mcp:3011/mcp`, no token (internal `lab` network only) |
| Tools | 42 of the gateway's 190, selected | All 42 of the server |
| n8n MCP Client Tool node | `MCP Gateway` | `Gaia MCP` |
| Tool names the model sees in n8n | `MCP_Gateway_show_dns` | `Gaia_MCP_show_dns` |
| n8n workflow file | `n8n/backup/workflows/quantum-gaia-via-gateway.json` | `n8n/backup/workflows/quantum-gaia-mcp-agent.json` |
| Flowise file | `integrations/flowise/quantum-gaia.flowdata.json` | `integrations/flowise/direct-gaia.flowdata.json` |
| Langflow file | `integrations/langflow/quantum-gaia.flow.json` | `integrations/langflow/direct-gaia.flow.json` |

The MCP server runs as the Compose service `quantum-gaia-mcp` (catalog key `gaia` in `mcp-gateway/catalog.yaml`).

## Tools (42)

- **Routing:** `show_routes`, `show_routes_static`, `show_routes_direct`, `show_routes_kernel`, `show_routes_aggregate`, `show_static_routes`, `show_routemaps`, `show_router_id`, `show_pbr_rules`, `show_pbr_tables`
- **BGP:** `show_bgp_summary`, `show_bgp_peers`, `show_bgp_groups`, `show_bgp_paths`, `show_bgp_routes_in`, `show_bgp_routes_out`, `show_bgp_routemaps`, `show_routes_bgp`, `show_configuration_bgp`, `show_inbound_route_filter_bgp_policy`
- **OSPF, RIP and IS-IS:** `show_ospf_summary`, `show_routes_ospf`, `show_inbound_route_filter_ospf`, `show_routes_rip`, `show_inbound_route_filter_rip`, `show_isis_info`
- **Multicast:** `show_pim_summary`, `show_ipv6_pim_summary`, `show_igmp_interfaces`, `show_igmp_groups`, `show_static_mroutes`
- **Interfaces and network services:** `show_interfaces_by_type`, `show_ipv6`, `show_arp`, `show_dns`, `show_proxy`, `show_dhcp`, `show_dhcp6`, `show_bootp_interfaces`, `show_date_time`, `show_nat_pools`
- **Credentials:** `manage_gaia_credentials`

Gaia is the only Check Point MCP server in the lab whose URL ends in `/mcp` (`http://quantum-gaia-mcp:3011/mcp`); the other Check Point servers answer at their root URL.

## Prerequisites

1. **The lab is running.** `./scripts/doctor.sh --post-start` reports `no blockers` (with 1Password references in `.env`: `op run --env-file=.env -- ./scripts/doctor.sh --post-start`)
2. **A model for `lab-chat`.** One model provider key in `.env`, or the local Ollama model (slow on a CPU). Every agent calls `lab-chat` through LiteLLM; builders never hold provider keys
3. **The Gaia settings in `.env`** (table below). `./setup.sh` asks for them, or edit `.env` (with `./setup.sh --1password`, `.env` holds `op://` references instead of values). Then run `docker compose up -d`: it recreates the servers whose settings changed. With 1Password references in `.env`, start every `docker compose` command in this guide with `op run --env-file=.env --` ([Secrets and 1Password](../REFERENCE.md#secrets-and-1password))
4. **Sign-in.** The lab admin for n8n, Flowise, and Langflow: `N8N_ADMIN_EMAIL` (default `admin@lab.local`) and `N8N_ADMIN_PASSWORD`

| Setting | Value |
|---|---|
| `GAIA_GATEWAY_IP` | IP address or host name of the Security Gateway whose Gaia REST API the agent reads. It must be reachable from the Docker host. |
| `GAIA_GATEWAY_PORT` | Gaia REST API port. Default `443`. |
| `GAIA_USERNAME`, `GAIA_PASSWORD` | A Gaia administrator of that gateway. |
| `GAIA_ALLOWED_GATEWAYS` | Optional. More gateways, comma-separated, that may receive the same credentials. |
| `GAIA_CA_CERT`, `GAIA_TLS_SERVERNAME` | Optional. For a self-signed certificate, see **Certificates** below. |

**The Gaia allow-list.** The server sends the Gaia credentials only to `GAIA_GATEWAY_IP` and to the hosts
in `GAIA_ALLOWED_GATEWAYS`. Any other gateway the model names in a tool call (`gateway_ip`) is refused,
and nothing is sent to it. This protects the gateway password from a prompt that names another host.
When a tool call leaves `gateway_ip` empty, the server uses `GAIA_GATEWAY_IP`. To read a second
gateway with the same credentials, add it to `GAIA_ALLOWED_GATEWAYS` and run
`docker compose up -d quantum-gaia-mcp`.

### Certificates

The Gaia server always verifies the gateway's TLS certificate. If it is self-signed:

1. Save the certificate as `certs/gateway.pem` and confirm its SHA-256 fingerprint with the gateway's
   administrator. The commands are in [`certs/README.md`](../../certs/README.md)
2. Set `GAIA_CA_CERT=/certs/gateway.pem` in `.env`
3. If you connect by IP address and the certificate names a host, set `GAIA_TLS_SERVERNAME` to that
   host name
4. Recreate the server: `docker compose up -d quantum-gaia-mcp`

## Chat with the agents

### n8n

1. Sign in to n8n and open the workflow **Gaia Agent (Direct)**
2. Click **Open chat** and send one of the prompts below
3. Open the **Executions** tab and the latest run. The MCP Client Tool **Gaia MCP** shows the tools the agent called, for example `Gaia_MCP_show_dns`
4. Repeat with **Gaia Agent (MCP Gateway)**. Its MCP Client Tool **MCP Gateway** selects the same 42 tools under **Tools to Include** and uses the credential **MCP Gateway Bearer**. The tool calls appear as `MCP_Gateway_show_dns`

The chat trigger also has a public **Chat URL** (it ends in `/webhook/<id>/chat`). That page asks for HTTP Basic authentication: the lab admin email and password (credential **Lab Agents Chat**).

### Flowise

1. Sign in to Flowise, open **Chatflows**, and open **Gaia Agent (Direct)** or **Gaia Agent (MCP Gateway)**
2. Click the chat icon at the top right of the canvas and send a prompt
3. The **Custom MCP** node shows the endpoint under **MCP Server Config** and the 42 selected tools under **Available Actions**. The gateway agent sends `Authorization: Bearer {{$vars.MCP_GATEWAY_TOKEN}}`, the Flowise variable that `builders-import` keeps in sync with `.env`

### Langflow (Complete lab)

Langflow runs in the Complete lab (`COMPOSE_PROFILES=complete`), or on its own with the `langflow` profile.

1. Sign in to Langflow and open the flow **Gaia Agent (Direct)** or **Gaia Agent (MCP Gateway)**
2. Click **Playground** (Langflow's chat panel) and send a prompt
3. In the gateway flow, the **MCP Tools** node lists all 190 gateway tools under **Actions**, with only this server's 42 switched on

## Try these

- *Show the interfaces and their IPs*
- *What are the static routes?*
- *Show the DNS and proxy settings*
- *Show the BGP peers and their state*

## Expected result

The agent reads the configured gateway and answers with short tables: interfaces by type with their
addresses, the routing table or static routes, DNS servers and proxy settings, and BGP or OSPF summaries.
Without the Gaia settings, every tool answers that Gaia is not configured.

## How the agents are built

| n8n node | Type | What it does |
|---|---|---|
| `When chat message received` | Chat Trigger | Starts one run per message. Shows the greeting and starter prompts. Public chat behind HTTP Basic authentication (credential **Lab Agents Chat**) |
| `Normalize input` | Edit Fields | Takes `chatInput` and `sessionId` from the chat, or from the body of a webhook call |
| `Gaia Agent` | AI Agent | Holds the system prompt and decides which tools to call. On an error it continues to `Friendly error` |
| `OpenAI Chat Model` | OpenAI Chat Model | Model `lab-chat` with the credential **Lab Model (LiteLLM)** (`http://litellm:4000/v1`) |
| `Conversation Memory` | Simple Memory | Keeps the recent turns of the chat session |
| `Gaia MCP` or `MCP Gateway` | MCP Client Tool | The 42 tools: direct to `http://quantum-gaia-mcp:3011/mcp`, or through the gateway |
| `Friendly error` | Code | Turns a failure into a plain explanation with the command that fixes it |

In Flowise, a **Tool Agent** uses the **OpenAI** chat model node (`lab-chat`, credential **Lab Model (LiteLLM)**, Base Path `http://litellm:4000/v1`), **Buffer Memory**, and one **Custom MCP** node. In Langflow, **Chat Input** feeds an **Agent** with the **OpenAI** component (`lab-chat`, OpenAI API Base `http://litellm:4000/v1`, API key from the global variable `LITELLM_MASTER_KEY`) and one **MCP Tools** node, and the answer goes to **Chat Output**. For a node-by-node walkthrough, see the [Threat Prevention deep dive](CheckPoint_Threat_Prevention_Guide.md).

**Updates.** `n8n-import` and `builders-import` re-import these agents on every deploy and keep an agent you changed. To keep your own variant, duplicate it first. `N8N_SEED_OVERWRITE=1` (n8n) or `SEED_OVERWRITE=1` (Flowise and Langflow) replaces a changed agent with the repository version; set it back to `0` after that run.

**Data handling.** Every tool result goes to the model provider behind `lab-chat`. With a cloud provider, that is an external service. Use lab data only. Never connect the lab to customer environments or load customer data unless the provider is approved for that data.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Gaia is not configured on this MCP server` | `GAIA_GATEWAY_IP`, `GAIA_USERNAME`, or `GAIA_PASSWORD` is blank | Set all three in `.env`, then `docker compose up -d quantum-gaia-mcp` |
| `Refusing to connect to gateway "<host>"` | The model asked for a gateway outside the allow-list | Ask about the configured gateway, or add the host to `GAIA_ALLOWED_GATEWAYS` and recreate the server |
| `No gateway given: pass gateway_ip of an allowed gateway (GAIA_GATEWAY_IP is not set)` | Only `GAIA_ALLOWED_GATEWAYS` is set | Set `GAIA_GATEWAY_IP`, or name an allowed gateway in the prompt |
| `TLS certificate verification failed` | Self-signed certificate, or the name does not match | See **Certificates** above |
| The MCP Gateway agent answers with HTTP 401 | The token in the builder differs from `MCP_GATEWAY_TOKEN` in `.env` | n8n: `docker compose run --rm n8n-import`. Flowise and Langflow: `docker compose run --rm builders-import` |
| The MCP Gateway agent has no tools, or fails while the Direct agent works | The gateway started before the server was ready, or still uses sessions to an old server container | `docker compose restart mcp-gateway` |
| **Friendly error**: the lab model endpoint is not reachable | LiteLLM is not running | `docker compose up -d litellm` |
| **Friendly error**: LiteLLM could not serve the `lab-chat` model | No working model provider key, or the provider failed | Check the key in `.env`, then `docker compose logs litellm` |
| The hosted chat URL asks for a password | HTTP Basic authentication on the chat trigger | Sign in with the lab admin email and password |

---

*Related:* [The MCP Gateway, Explained](MCP_Gateway_Explained.md) · [Direct and MCP Gateway agents lab](MCP_Gateway_Agent_Guide.md) · [Gateway CLI](Quantum_Gateway_CLI_MCP_Agent_Guide.md)
