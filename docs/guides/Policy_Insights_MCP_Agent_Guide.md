# Policy Insights Agents (MCP Gateway and Direct)

The Policy Insights agents report the suggestions that Policy Insights makes for your Access Control
policy: rules with unused objects, rules to tighten, disabled rules to delete, and rules with zero hits.
They also report the product's status and license, and when the suggestion engine runs. They work with
a Security Management Server, a Multi-Domain Server, or Smart-1 Cloud, through the Management API.

Two agents ship in n8n, Flowise, and Langflow: **Policy Insights Agent (MCP Gateway)** and **Policy Insights Agent (Direct)**. They have the same 10 tools and almost the same prompt: the MCP Gateway agent's adds a short note about the gateway, and the Direct agent's adds one line saying that layers and rules are addressed by UID. Only the path of the tool calls differs.

Every tool of these agents only reads. The agents never change your environment. Suggestions are advisory: the agents recommend a human review, and an administrator applies any change in SmartConsole (or PolicyPilot), never through these agents.

## At a glance

|  | MCP Gateway agent | Direct agent |
|---|---|---|
| Agent name | Policy Insights Agent (MCP Gateway) | Policy Insights Agent (Direct) |
| Endpoint | `http://mcp-gateway:8080/mcp`, Bearer token `MCP_GATEWAY_TOKEN` | `http://policy-insights-mcp:3013`, no token (internal `lab` network only) |
| Tools | 10 of the gateway's 190, selected | All 10 of the server |
| n8n MCP Client Tool node | `MCP Gateway` | `Policy Insights MCP` |
| Tool names the model sees in n8n | `MCP_Gateway_policy-insights__init` | `Policy_Insights_MCP_policy-insights__init` |
| n8n workflow file | `n8n/backup/workflows/policy-insights-via-gateway.json` | `n8n/backup/workflows/policy-insights-mcp-agent.json` |
| Flowise file | `integrations/flowise/policy-insights.flowdata.json` | `integrations/flowise/direct-policy-insights.flowdata.json` |
| Langflow file | `integrations/langflow/policy-insights.flow.json` | `integrations/langflow/direct-policy-insights.flow.json` |

The MCP server runs as the Compose service `policy-insights-mcp` (catalog key `policy-insights` in `mcp-gateway/catalog.yaml`). It is vendored from upstream `@chkp/policy-insights-mcp` 0.3.5 and built with the lab patches: TLS certificates are always verified, no usage telemetry is sent, and nothing secret is logged ([`PATCHES.md`](../../docker/n8n/mcp-src/PATCHES.md), section 11).

## Tools (10)

- `policy-insights__init`: signs in to the Management Server and says whether it is a Multi-Domain system, with its domains
- `ShowPolicyInsightsStatus`: overall product status, the supported Insights API versions, and the license validity and expiration when available
- `ShowState`: whether Policy Insights is enabled or disabled
- `ShowConfig`: the Policy Insights configuration and settings
- `ShowCardInfo`: the information for the Policy Insights card in Infinity Cloud Services
- `ShowSuggestionsSummary`: the number of suggestions per type, the days of traffic logs analyzed, and the publish the suggestions are based on (optionally for one layer)
- `ShowRulesUidsWithSuggestions`: the UIDs of the rules on one layer with suggestions of one type, inline layers included
- `ShowSuggestions`: the content of the suggestions for a layer, a list of rule UIDs, or a list of suggestion UIDs, with filters and paging (`limit`, `offset`)
- `ShowSuggestionsInfo`: for one layer: the last and next engine run, the suggestion status, and onboarding guidance when no insights are visible yet
- `ShowSuggestionEngineMetadata`: the next scheduled engine run for each suggestion type

The suggestion types are `unused-objects`, `tighten-rule`, `delete-disabled-rule`, and `zero-hits-rule`. `ShowSuggestions`, `ShowSuggestionsSummary`, and `ShowRulesUidsWithSuggestions` filter by `confidence-level` and `security-impact` (`HIGH`, `MEDIUM_AND_ABOVE`, `LOW_AND_ABOVE`), `states` (`ACCEPTED`, `REJECTED`), and `user-interaction` (`DECIDE_LATER`, with `IN` or `NOT-IN`). `ShowSuggestions` leaves out accepted and rejected suggestions unless you ask for them. On a Multi-Domain Server, every tool except `policy-insights__init` also takes `domain` and `domains_to_process` (`CURRENT_DOMAIN` or `ALL_DOMAINS_ON_THIS_SERVER`).

**Layers and rules by UID.** These tools address layers and rules by UID, and this server has no tool that lists layers or shows rule content. To find a layer's UID or see a rule, ask the Management agent (`show_access_layers`, `show_access_rule`), then give the UID to the Policy Insights agent. See the [Management guide](Quantum_Management_MCP_Agent_Guide.md).

## Prerequisites

1. **The lab is running.** `./scripts/doctor.sh --post-start` reports `no blockers` (with 1Password references in `.env`: `op run --env-file=.env -- ./scripts/doctor.sh --post-start`)
2. **A model for `lab-chat`.** One model provider key in `.env`, or the local Ollama model (slow on a CPU). Every agent calls `lab-chat` through LiteLLM; builders never hold provider keys
3. **Policy Insights on the Management Server.** Management API v2.1 (R82.10 or later), with Policy Insights enabled. Suggestions appear only after the engine has finished its first analysis
4. **The Management settings in `.env`** (table below). They are shared with the other Management-backed servers. `./setup.sh` asks for them, or edit `.env` (with `./setup.sh --1password`, `.env` holds `op://` references instead of values). Then run `docker compose up -d`: it recreates the servers whose settings changed. With 1Password references in `.env`, start every `docker compose` command in this guide with `op run --env-file=.env --` ([Secrets and 1Password](../REFERENCE.md#secrets-and-1password))
5. **Sign-in.** The lab admin for n8n, Flowise, and Langflow: `N8N_ADMIN_EMAIL` (default `admin@lab.local`) and `N8N_ADMIN_PASSWORD`

| Setting | Value |
|---|---|
| `MANAGEMENT_HOST` | Host name or IP address of the Security Management Server or Multi-Domain Server, without `https://`. It must be reachable from the Docker host. |
| `S1C_URL` | Instead of `MANAGEMENT_HOST`, for Smart-1 Cloud: the Web API URL of your tenant, without `/login` (Smart-1 Cloud portal: **Settings > API & SmartConsole**). When both are set, the servers use `MANAGEMENT_HOST`. |
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

1. Sign in to n8n and open the workflow **Policy Insights Agent (Direct)**
2. Click **Open chat** and send one of the prompts below
3. Open the **Executions** tab and the latest run. The MCP Client Tool **Policy Insights MCP** shows the tools the agent called, for example `Policy_Insights_MCP_policy-insights__init`
4. Repeat with **Policy Insights Agent (MCP Gateway)**. Its MCP Client Tool **MCP Gateway** selects the same 10 tools under **Tools to Include** and uses the credential **MCP Gateway Bearer**. The tool calls appear as `MCP_Gateway_policy-insights__init`

The chat trigger also has a public **Chat URL** (it ends in `/webhook/<id>/chat`). That page asks for HTTP Basic authentication: the lab admin email and password (credential **Lab Agents Chat**).

### Flowise

1. Sign in to Flowise, open **Chatflows**, and open **Policy Insights Agent (Direct)** or **Policy Insights Agent (MCP Gateway)**
2. Click the chat icon at the top right of the canvas and send a prompt
3. The **Custom MCP** node shows the endpoint under **MCP Server Config** and the 10 selected tools under **Available Actions**. The gateway agent sends `Authorization: Bearer {{$vars.MCP_GATEWAY_TOKEN}}`, the Flowise variable that `builders-import` keeps in sync with `.env`

### Langflow (Complete lab)

Langflow runs in the Complete lab (`COMPOSE_PROFILES=complete`), or on its own with the `langflow` profile.

1. Sign in to Langflow and open the flow **Policy Insights Agent (Direct)** or **Policy Insights Agent (MCP Gateway)**
2. Click **Playground** (Langflow's chat panel) and send a prompt
3. In the gateway flow, the **MCP Tools** node lists all 190 gateway tools under **Actions**, with only this server's 10 switched on

## Try these

Replace `<layer UID>` with a UID from the Management agent.

- *What is my Policy Insights status, and when does the license expire?*
- *How many suggestions are there per type, and which publish are they based on?*
- *When is the next engine run for zero-hit rules?*
- *Which rules in layer `<layer UID>` should be tightened? Only high confidence*
- *Show the suggestions I moved to decide later in layer `<layer UID>`*
- *Why do I see no insights for layer `<layer UID>`?*

## Expected result

The agent first calls `policy-insights__init` to sign in. On a Multi-Domain Server, the answer lists the
domains, and the agent asks which one to use when it is not sure. Then it calls the read tools it needs.
Expect short Markdown answers: the product status and license, a table of suggestion counts per type, or
the rules with suggestions and what each suggestion recommends. `ShowRulesUidsWithSuggestions` returns
rule UIDs, not names. Long lists show the 15 most relevant items and say how many more exist. When no
suggestions come back, the agent checks `ShowState` and `ShowPolicyInsightsStatus` first and says what
is missing. Without management settings, the tool calls fail and the agent reports the error.

## Check the setup

```sh
./scripts/doctor.sh --post-start
tests/acceptance/run.sh --only GW-TOOLS,DIRECT
```

With 1Password references in `.env`, start both commands with `op run --env-file=.env --`.

- `doctor.sh` prints `10 tools at http://policy-insights-mcp:3013` for the direct path and `190 tools with MCP_GATEWAY_TOKEN` for the gateway. The row **Check Point: Management (7 servers)** says `ready` when the management settings are complete
- `GW-TOOLS` passes when the gateway lists every server's tools, these 10 included. `DIRECT` passes when every MCP server, `policy-insights-mcp` included, lists exactly its tools in `SERVER_TOOLS` (`scripts/flows/langflow_fix.py`), here these 10
- Listing tools needs no management access. The first tool call (`policy-insights__init`) signs in, so finish with the prompt *What is my Policy Insights status?* The opt-in **Nightly Agent Self-Check** workflow (`NIGHTLY_SELF_QA=1`) sends the same prompt to both agents
- For maintainers: `tests/mcp-src/run.sh policy-insights` builds every vendored MCP server in one throwaway container (it reaches the npm registry only), then checks in a second container with no network that this server lists the same tools as npm 0.3.5 (`tests/mcp-src/fixtures/policy-insights-0.3.5-tools.json`) and keeps the lab patches. See [tests/README.md](../../tests/README.md)

## How the agents are built

| n8n node | Type | What it does |
|---|---|---|
| `When chat message received` | Chat Trigger | Starts one run per message. Shows the greeting and starter prompts. Public chat behind HTTP Basic authentication (credential **Lab Agents Chat**) |
| `Normalize input` | Edit Fields | Takes `chatInput` and `sessionId` from the chat, or from the body of a webhook call |
| `Policy Insights Agent` | AI Agent | Holds the system prompt and decides which tools to call. On an error it continues to `Friendly error` |
| `OpenAI Chat Model` | OpenAI Chat Model | Model `lab-chat` with the credential **Lab Model (LiteLLM)** (`http://litellm:4000/v1`) |
| `Conversation Memory` | Simple Memory | Keeps the recent turns of the chat session |
| `Policy Insights MCP` or `MCP Gateway` | MCP Client Tool | The 10 tools: direct to `http://policy-insights-mcp:3013`, or through the gateway |
| `Friendly error` | Code | Turns a failure into a plain explanation with the command that fixes it |

In Flowise, a **Tool Agent** uses the **OpenAI** chat model node (`lab-chat`, credential **Lab Model (LiteLLM)**, Base Path `http://litellm:4000/v1`), **Buffer Memory**, and one **Custom MCP** node. In Langflow, **Chat Input** feeds an **Agent** with the **OpenAI** component (`lab-chat`, OpenAI API Base `http://litellm:4000/v1`, API key from the global variable `LITELLM_MASTER_KEY`) and one **MCP Tools** node, and the answer goes to **Chat Output**. For a node-by-node walkthrough, see the [Threat Prevention deep dive](CheckPoint_Threat_Prevention_Guide.md).

**Other agents.** Three gateway agents bind some of these tools. **Check Point MCP Gateway Agent** has four: `policy-insights__init`, `ShowPolicyInsightsStatus`, `ShowSuggestionsSummary`, and `ShowSuggestions`. **Fleet Commander Estate Agent** and **Guarded Agent (Lakera Guard)** have those four plus `ShowState` and `ShowRulesUidsWithSuggestions`.

**Updates.** `n8n-import` and `builders-import` re-import these agents on every deploy and keep an agent you changed. To keep your own variant, duplicate it first. `N8N_SEED_OVERWRITE=1` (n8n) or `SEED_OVERWRITE=1` (Flowise and Langflow) replaces a changed agent with the repository version; set it back to `0` after that run.

**Data handling.** Tool results are your policy data, and every tool result goes to the model provider behind `lab-chat`. With a cloud provider, that is an external service. Use lab data only. Never connect the lab to customer environments or load customer data unless the provider is approved for that data.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Error initializing @chkp/policy-insights-mcp connection: Either management host or S1C URL must be provided` | No management settings in `.env` | Set `MANAGEMENT_HOST` (or `S1C_URL`) and `MANAGEMENT_API_KEY`, then `docker compose up -d policy-insights-mcp` |
| `docker compose logs policy-insights-mcp` shows `WARNING: MANAGEMENT_HOST is set without MANAGEMENT_API_KEY` (or `WARNING: S1C_URL is set without MANAGEMENT_API_KEY`) | A host or URL without a way to sign in is ignored | Add `MANAGEMENT_API_KEY` (or, on-premises only, `MANAGEMENT_USERNAME` and `MANAGEMENT_PASSWORD`), then `docker compose up -d policy-insights-mcp` |
| `TLS certificate verification failed` | Self-signed certificate, or the name does not match | See **Certificates** above |
| `Cannot read the CA certificate file`, or `does not contain a PEM certificate` | `MANAGEMENT_CA_CERT` points to a missing file or a file that is not PEM | Put the PEM file in `certs/`, set the path under `/certs/`, then `docker compose up -d policy-insights-mcp` |
| `Error executing tool '<tool>': API request failed: <status> - ...` | The Management Server refused the call. Policy Insights is not enabled, or the server is older than R82.10 (Management API v2.1) | Ask *What is my Policy Insights status?* (`ShowState`, `ShowPolicyInsightsStatus`). See [sk183313](https://support.checkpoint.com/results/sk/sk183313) |
| No suggestions, or the counts are zero | The engine has not finished its first analysis, the Management Server is not connected to Check Point cloud services yet, or the filters hide them | Ask *Why do I see no insights for layer `<layer UID>`?* (`ShowSuggestionsInfo`), and ask for accepted or rejected suggestions explicitly |
| The agent cannot find a layer or rule by name | These tools take UIDs, and this server lists no layers | Get the UID from the Management agent (`show_access_layers`), then ask again with the UID |
| `Error executing tool '<tool>': invalid domain name` | The domain name has control characters or is longer than 255 characters | Use a domain name exactly as `policy-insights__init` lists it |
| `MCP error -32001: Request timed out` | `MANAGEMENT_HOST` is unreachable from the Docker host, or replies do not route back | `./scripts/doctor.sh --preflight --online` tests reachability. See **Lab connectivity** in the [Direct and MCP Gateway agents lab](MCP_Gateway_Agent_Guide.md) |
| HTTP 429 (too many requests) from the Management Server | The Management Server limits logins; every new MCP session logs in | Wait one or two minutes. Keep one chat session per exercise |
| **Friendly error**: the tool node "Policy Insights MCP" could not reach the Policy Insights MCP server | `policy-insights-mcp` is not running | `docker compose up -d policy-insights-mcp` |
| The MCP Gateway agent answers with HTTP 401 | The token in the builder differs from `MCP_GATEWAY_TOKEN` in `.env` | n8n: `docker compose run --rm n8n-import`. Flowise and Langflow: `docker compose run --rm builders-import` |
| The MCP Gateway agent has no tools, or fails while the Direct agent works | The gateway started before the server was ready, or still uses sessions to an old server container | `docker compose restart mcp-gateway` |
| **Friendly error**: the lab model endpoint is not reachable | LiteLLM is not running | `docker compose up -d litellm` |
| **Friendly error**: LiteLLM could not serve the `lab-chat` model | No working model provider key, or the provider failed | Check the key in `.env`, then `docker compose logs litellm` |
| The hosted chat URL asks for a password | HTTP Basic authentication on the chat trigger | Sign in with the lab admin email and password |

---

*Related:* [The MCP Gateway, Explained](MCP_Gateway_Explained.md) · [Direct and MCP Gateway agents lab](MCP_Gateway_Agent_Guide.md) · [Management](Quantum_Management_MCP_Agent_Guide.md) · [PolicyPilot](PolicyPilot_Gateway_Sidecar_Guide.md)
