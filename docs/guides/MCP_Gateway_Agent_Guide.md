# MCP Gateway Agent Guide (Direct vs. Gateway)

This guide teaches two ways an AI agent can reach the Check Point MCP servers in
this playground, so learners can compare them hands-on:

1. **Direct** — the n8n agent connects to one MCP sidecar (e.g.
   `mcp-quantum-management`) over HTTP. One connection per server, no auth.
2. **Gateway** — the n8n agent connects to a single **Docker MCP Gateway**
   endpoint that fronts *many* MCP servers, aggregates their tools, and enforces
   a Bearer token.

Both are provisioned and ready. The point is to *see the difference*, not to pick
a winner — each teaches something.

---

## Why a gateway? (the concept)

A "reverse proxy for MCP." Instead of every client wiring up N connections (one
per MCP server), the client makes **one** connection to the gateway, and the
gateway:

* **aggregates** tools from every registered server into one `tools/list`,
* **namespaces / de-collides** tools when two servers expose the same name,
* **centralizes auth** (a single Bearer token at the edge),
* gives one place for **discovery, logging, and policy**.

This is the same idea as an API gateway, applied to agent tools. In this
playground the gateway is Docker's `docker/mcp-gateway`, driven by
`mcp-gateway/catalog.yaml`.

---

## Architecture in this lab

```
                        ┌────────────────────────┐
   n8n agent  ── HTTP ─▶│  DIRECT path           │
   (no auth)            │  mcp-quantum-management │──▶ Check Point SMS
                        │      :3002 /mcp         │
                        └────────────────────────┘

                        ┌────────────────────────┐      ┌─ documentation
   n8n agent  ── HTTP ─▶│  GATEWAY path          │──────┤  quantum-management ──▶ SMS
   (Bearer token)       │  mcp-gateway :8080/mcp │      │  gaia, gw-cli, logs…
                        │  catalog.yaml          │      └─ (all 10 CP servers)
                        └────────────────────────┘
```

* **Direct credential:** `CP Management MCP Client Docker`
  → `http://mcp-quantum-management:3002`, no headers.
* **Gateway credential:** `CP MCP Gateway Docker`
  → `http://mcp-gateway:8080/mcp`, header
  `Authorization=Bearer <MCP_GATEWAY_TOKEN>`.

Both live only on the internal `demo` Docker network — the gateway has **no**
published port.

### The gateway fronts *all* the Check Point MCP servers

The gateway aggregates **10** Check Point MCP servers into one endpoint. The set
is defined in `mcp-gateway/catalog.yaml` and enabled via the `--servers=` list on
the `mcp-gateway` service in `docker-compose.yml` (a custom catalog does not
auto-enable its servers — they must be listed explicitly, and the two must stay
in sync):

| Catalog key         | Sidecar URL                                | Purpose                          |
|---------------------|--------------------------------------------|----------------------------------|
| `documentation`     | `http://mcp-documentation:3000`            | Product documentation (cloud)    |
| `quantum-management`| `http://mcp-quantum-management:3002`       | Quantum Management (SMS)         |
| `cpinfo-analysis`   | `http://cpinfo-analysis-mcp:3012`          | CPInfo bundle analysis           |
| `https-inspection`  | `http://mcp-https-inspection:3001`         | HTTPS Inspection                 |
| `management-logs`   | `http://mcp-management-logs:3003`          | Management / log queries         |
| `gaia`              | `http://quantum-gaia-mcp:3011/mcp`         | Gaia OS                          |
| `gw-cli`            | `http://quantum-gw-cli-mcp:3009`           | Gateway CLI (clish/expert)       |
| `reputation-service`| `http://reputation-service-mcp:3007`       | Reputation / IoC lookup          |
| `threat-emulation`  | `http://threat-emulation-mcp:3004`         | Threat Emulation (sandbox)       |
| `threat-prevention` | `http://threat-prevention-mcp:3005`        | Threat Prevention                |

Together these expose on the order of ~180 aggregated tools through the single
gateway endpoint (the exact count is reported by the live gateway's `tools/list`,
not stored in the repo). The `spark-management`, `harmony-sase`, and
`quantum-gw-connection-analysis` sidecars run in the stack but are **not** behind
the gateway (not in `catalog.yaml` / `--servers=`).

---

## Prerequisites

* n8n reachable at `http://<host_ip>:5678`.
* `.env` set (copied from `.env-example`). Relevant keys:
  * `MANAGEMENT_HOST` / `MANAGEMENT_API_KEY` — the Check Point SMS.
  * `MCP_GATEWAY_TOKEN` — the pinned gateway Bearer token (defaults to
    `cp-mcp-gateway-training-token`; the provisioned n8n credential uses that
    default, so change **both** together if you customize it).

---

## Lab connectivity (READ THIS if management calls time out)

Management tools call `https://$MANAGEMENT_HOST/web_api/...`. Two lab-specific
gotchas cause a hang that surfaces in n8n as **`MCP error -32001: Request timed
out`** after ~60s:

1. **`MANAGEMENT_HOST` must be reachable from the Docker host.** In a CloudShare
   lab the internal `10.1.1.x` SMS IP is *not* routable from outside the
   environment — set `MANAGEMENT_HOST` to the SMS adapter's CloudShare **Public
   IP** (Networks → SMS adapter → Inbound Access: Public IP).

2. **The SMS must return replies out the same path.** If the SMS default route
   points at the training gateway (`10.1.1.111`), replies to an external client
   are sent into the *simulated* internet (`203.0.113.0/24`, TEST-NET — routes
   nowhere) and the connection half-opens. Keep the training default route, and
   add a **specific** route for your external client subnet out the CloudShare
   gateway. Example (Gaia clish on the SMS, for a client at `203.0.113.0/24`):

   ```
   set static-route <your-client-subnet>/24 nexthop gateway address 10.1.1.1 on
   save config
   ```

   Then snapshot the CloudShare blueprint so a revert keeps the route.

The **documentation** server does not touch the SMS (it calls Check Point's
cloud), so the gateway's doc tools work even while the SMS is unreachable — handy
for demoing the gateway itself in isolation.

### When the CloudShare address changes

CloudShare VM hostnames (`*.vm.cld.sr`) change when the environment is
recreated/re-provisioned. `MANAGEMENT_HOST` is the **single place** to update —
n8n credentials and workflows never change (they point at the sidecars/gateway,
not the SMS).

1. Get the new address: CloudShare → your environment → **Networks** → SMS
   adapter → **Inbound Access: Public IP** (hostname or IP).
2. Update the env — pick the one that matches how the stack is run:
   * **Dokploy:** project → **Environment** → set
     `MANAGEMENT_HOST=<new-address>` → **Redeploy**. (This is the durable copy —
     Dokploy rewrites `.env` from it on redeploy.)
   * **Plain compose host:** edit `.env`, then `docker compose up -d`
     (recreates only the services whose env changed).
3. That's it. Sanity-check with the chat: *"show me the gateways and servers"*.
   If it times out, re-verify the two connectivity items above (public IP
   reachable + the SMS return route — a CloudShare **revert** to an old snapshot
   can silently remove the static route).

---

## Walkthrough A — Direct

1. Open the **quantum-management-mcp** workflow.
2. Click **Test Workflow → Chat**.
3. Ask: *"Is there a rule in the DNS_Layer to allow traffic to 8.8.8.8?"*
4. The agent calls `List-CP-MCP-Tools` (one server's tools), then
   `CP-MCP-Client` to run the chosen tool against the SMS.

Observe: the tool list is only the management server's tools; there is no auth.

## Walkthrough B — Gateway

1. Open the **quantum-management-via-gateway** workflow.
2. Click **Test Workflow → Chat**.
3. Ask the same question, then ask a **documentation** question (e.g. *"What is
   an access rule?"*).
4. The agent calls `List-Gateway-MCP-Tools` and gets the **combined** catalog
   from *both* servers through one endpoint; `Gateway-MCP-Client` runs the tool.

Observe: one connection, one Bearer token, tools from multiple servers.

---

## Exercises

1. **Compare tool lists.** Run `List-CP-MCP-Tools` (direct) and
   `List-Gateway-MCP-Tools` (gateway). How many tools does each return, and
   where do the extra gateway tools come from?
2. **Break auth on purpose.** Edit the `CP MCP Gateway Docker` credential and
   change the Bearer token. Re-run — you'll get `401 Unauthorized`. Restore it.
   (This is exactly what happens silently if the gateway token is *not* pinned
   and the gateway restarts — see below.)
3. **Add a server to the gateway (advanced).** Only *gateway-ready* servers can
   sit behind the gateway — the stock Check Point packages are single-client
   over HTTP and break on the gateway's concurrent sessions. All **10** servers
   in the catalog above are locally patched and gateway-ready. See
   `docker/n8n/mcp-src/PATCHES.md` for the why, the full capability matrix, and
   the ~30-line recipe — then make one of the not-yet-fronted sidecars (e.g.
   `spark-management`) gateway-ready yourself, add it to
   `mcp-gateway/catalog.yaml` + the `--servers=` list (healthcheck +
   `depends_on: service_healthy` too), redeploy, and watch its tools appear with
   **no client change**.
4. **Security discussion.** With the gateway as the single choke point, where
   would you enforce tool-level allow/deny, DLP on arguments, or scanning of
   tool descriptions? (This is the "MCP security gateway" idea.)

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Gateway lists **0 tools** | Gateway started before a sidecar was listening; it enumerates once at startup | Fixed by healthchecks + `depends_on: service_healthy`. To recover a running stack: `docker restart mcp-gateway` |
| A raw `tools/list` returns **0 tools** (curl / manual client) | No MCP `initialize` handshake was performed first | **Expected MCP protocol behavior, not a bug.** A session must send `initialize` (and the `notifications/initialized` follow-up) before `tools/list`. n8n's MCP client does this for you; a hand-rolled curl must do it explicitly. Send `Authorization: Bearer <MCP_GATEWAY_TOKEN>` on every request |
| n8n gets **401 Unauthorized** from the gateway | Missing/wrong Bearer token, or the gateway generated a new random token on restart | Pin `MCP_GATEWAY_TOKEN` in `.env` (done by default) and set the same value in the `CP MCP Gateway Docker` credential header |
| **`-32001 Request timed out`** on management tools | SMS unreachable or asymmetric return routing | See **Lab connectivity** above |
| Gateway logs `connection refused` to a sidecar | Sidecar not healthy yet | Confirm the sidecar's healthcheck passes (`docker ps` shows `healthy`) |
| Management tools return **`429 err_too_many_requests`** | SMS login-rate throttle: every new MCP session performs its own `/web_api/login`, and bursts of sessions (a classroom, or parallel test runs) trip it | Wait ~1–2 minutes; it clears on its own. Keep one chat session per exercise rather than re-opening workflows rapidly |

---

## Direct + gateway twins ship together

Every direct-connection agent workflow has a matching `*-via-gateway.json` twin
whose only difference is the MCP credential: the direct workflow points at one
sidecar (no auth), and the twin points at the `CP MCP Gateway Docker` credential
(`http://mcp-gateway:8080/mcp` + Bearer token). Both variants are committed under
`n8n/backup/workflows/` and are re-imported on every redeploy, so the direct-vs-
gateway comparison is always available. The 10 twins:

`cpinfo-analysis-via-gateway`, `documentation-via-gateway`,
`https-inspection-via-gateway`, `management-logs-via-gateway`,
`quantum-gaia-via-gateway`, `quantum-gw-cli-via-gateway`,
`quantum-management-via-gateway`, `reputation-service-via-gateway`,
`threat-emulation-via-gateway`, `threat-prevention-via-gateway`.

## The import job substitutes secrets and resolves the domain

The `n8n-import` service (in `docker-compose.yml`) does not import the committed
files verbatim. It copies `./n8n/backup` to a writable temp dir and, before
importing:

* substitutes real secrets from `.env` into the credential templates —
  `__POSTGRES_PASSWORD__`, `__PILOT_MCP_TOKEN__` (PolicyPilot bearer), and
  `__DEVHUB_MCP_TOKEN__` (DevHub bearer). If a token env is **empty**, the import
  *drops* that credential file rather than importing a broken placeholder
  credential (and logs a warning).
* resolves the `{{DOMAIN}}` placeholder in the `policypilot-management-agent`,
  `policypilot-dynamic-layer-agent`, and `devhub-agent` workflows to the real
  deployment domain (from `DOMAIN`, falling back to `N8N_HOST` with the `n8n.`
  prefix stripped), so each MCP endpoint URL (`policypilot.<domain>/mcp`,
  `hub.<domain>/api/mcp`) is correct on a fresh deploy.

It then runs:

```bash
n8n import:credentials --separate --input=/tmp/import/credentials_public
n8n import:workflow    --separate --input=/tmp/import/workflows
```

**Gotcha — tags are stripped.** `n8n import:workflow` fails on duplicate tag
names, so every committed workflow JSON carries an empty `"tags": []` array.
Keep it that way when re-exporting a workflow, or the import step will error on
re-deploy.

## How it's wired (reference)

* `docker-compose.yml` → `mcp-gateway` service: `--catalog=checkpoint-mcp.yaml`,
  `--servers=documentation,quantum-management,cpinfo-analysis,https-inspection,management-logs,gaia,gw-cli,reputation-service,threat-emulation,threat-prevention`,
  and the pinned Bearer token
  `MCP_GATEWAY_AUTH_TOKEN=${MCP_GATEWAY_TOKEN:-cp-mcp-gateway-training-token}`,
  `depends_on: { <sidecar>: { condition: service_healthy } }`.
* `mcp-gateway/catalog.yaml` → the remote MCP servers the gateway fronts.
* Provisioned n8n artifacts:
  * credential `CP MCP Gateway Docker`
    (`n8n/backup/credentials_public/CP-MCP-Gateway-Docker.json`)
  * the 10 `*-via-gateway` workflows in `n8n/backup/workflows/`.
