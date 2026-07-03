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

                        ┌────────────────────────┐      ┌─ mcp-documentation :3000
   n8n agent  ── HTTP ─▶│  GATEWAY path          │──────┤
   (Bearer token)       │  mcp-gateway :8080/mcp │      └─ mcp-quantum-management :3002 ──▶ SMS
                        │  catalog.yaml          │
                        └────────────────────────┘
```

* **Direct credential:** `CP Management MCP Client Docker`
  → `http://mcp-quantum-management:3002`, no headers.
* **Gateway credential:** `CP MCP Gateway Docker`
  → `http://mcp-gateway:8080/mcp`, header
  `Authorization=Bearer <MCP_GATEWAY_TOKEN>`.

Both live only on the internal `demo` Docker network — the gateway has **no**
published port.

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
3. **Add a server to the gateway.** Add another sidecar (e.g.
   `mcp-management-logs`) to `mcp-gateway/catalog.yaml` and the `--servers=`
   list, add a healthcheck + `depends_on: service_healthy` for it, redeploy, and
   watch its tools appear in the gateway's list with no client change.
4. **Security discussion.** With the gateway as the single choke point, where
   would you enforce tool-level allow/deny, DLP on arguments, or scanning of
   tool descriptions? (This is the "MCP security gateway" idea.)

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Gateway lists **0 tools** | Gateway started before a sidecar was listening; it enumerates once at startup | Fixed by healthchecks + `depends_on: service_healthy`. To recover a running stack: `docker restart mcp-gateway` |
| n8n gets **401 Unauthorized** from the gateway | Missing/wrong Bearer token, or the gateway generated a new random token on restart | Pin `MCP_GATEWAY_TOKEN` in `.env` (done by default) and set the same value in the `CP MCP Gateway Docker` credential header |
| **`-32001 Request timed out`** on management tools | SMS unreachable or asymmetric return routing | See **Lab connectivity** above |
| Gateway logs `connection refused` to a sidecar | Sidecar not healthy yet | Confirm the sidecar's healthcheck passes (`docker ps` shows `healthy`) |

---

## How it's wired (reference)

* `docker-compose.yml` → `mcp-gateway` service: `--catalog=checkpoint-mcp.yaml`,
  `--servers=documentation,quantum-management`, `MCP_GATEWAY_AUTH_TOKEN`,
  `depends_on: { <sidecar>: { condition: service_healthy } }`.
* `mcp-gateway/catalog.yaml` → the remote MCP servers the gateway fronts.
* Provisioned n8n artifacts:
  * credential `CP MCP Gateway Docker`
    (`n8n/backup/credentials_public/CP-MCP-Gateway-Docker.json`)
  * workflow `quantum-management-via-gateway`
    (`n8n/backup/workflows/quantum-management-via-gateway.json`)
