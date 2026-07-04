# PolicyPilot behind the MCP Gateway (opt-in)

The Check Point MCP servers in this lab are **read-mostly**. [PolicyPilot](https://github.com/alshawwaf/PolicyPilot)
adds the missing lesson: an agent that makes a **guarded write** to firewall
policy — plain-language access request → correct, first-match-safe change with
**preview → approve → rollback**. It ships its own MCP server (~30 tools), so it
plugs into the Docker MCP Gateway as another sidecar.

This is **opt-in** (compose profile `policypilot`) and has real prerequisites —
it does not start with the default stack or CI.

## Why prerequisites (read first)

PolicyPilot's MCP server is a Streamable-HTTP FastMCP server built `stateless_http`
with the DNS-rebinding host check disabled — exactly the right shape behind a
proxy. But unlike the zero-dependency exercise server, it needs:

1. **The `mcp` Python SDK in its image.** PolicyPilot pins `mcp==1.28.0`; in this
   org that installs via **Artifactory** (plain PyPI is blocked). So the image
   must be built with pip pointed at Artifactory.
2. **A database + encryption key.** PolicyPilot's tools open the DB per call and
   **decrypt saved SMS/gateway credentials** with `PILOT_ENCRYPTION_KEY`. To do
   anything against a real SMS the sidecar needs a DB the **portal** populated
   (share its `/data` volume) *and the same encryption key*. An empty DB yields
   tools that list nothing.
3. **`PILOT_MCP_TOKEN`.** The standalone server exits without it; it's the single
   full-access bearer in sidecar mode (the DB-backed API-key store /
   `mcp_allow_publish` gating is bypassed out-of-portal — so gate writes at the
   gateway/network layer).

## Enable it

1. **Build the PolicyPilot image** (from the PolicyPilot repo, pip → Artifactory):
   ```bash
   docker build -t policypilot:custom /path/to/PolicyPilot
   ```
   (or set `POLICYPILOT_IMAGE` to a registry image you've built).
2. **Set env** in `.env`:
   ```env
   POLICYPILOT_IMAGE=policypilot:custom
   PILOT_MCP_TOKEN=<a strong bearer>
   PILOT_ENCRYPTION_KEY=<the SAME key the PolicyPilot portal uses>
   PILOT_DATABASE_URL=sqlite:////data/policypilot.db   # or point at the portal's DB
   ```
   For live SMS work, mount the portal's populated DB into `policypilot_data`
   (or change `PILOT_DATABASE_URL` to a shared Postgres).
3. **Start the sidecar:**
   ```bash
   docker compose --profile policypilot up -d policypilot-mcp
   ```
4. **Register it in the gateway:**
   - `mcp-gateway/catalog.yaml`:
     ```yaml
       policypilot:
         description: "PolicyPilot — guarded policy changes (preview/approve/rollback)"
         title: "PolicyPilot"
         type: "remote"
         remote:
           url: "http://policypilot-mcp:3020/"
           transport_type: "streamable"
     ```
   - In `docker-compose.yml`, add `policypilot` to the gateway's `--servers=` list
     and a `depends_on: { policypilot-mcp: { condition: service_healthy } }`.
   - **Auth:** the sidecar enforces `Authorization: Bearer <PILOT_MCP_TOKEN>`. The
     Docker MCP Gateway must forward Authorization to the remote (or terminate at
     the gateway and rely on the private `demo` network). POST the trailing-slash
     root (`/`).
5. **Verify:**
   ```bash
   ./scripts/health-check.sh --profile cpu     # gateway tool count jumps by PolicyPilot's tools
   ```

## The lesson

An n8n agent (see the [MCP Gateway guide](MCP_Gateway_Agent_Guide.md)) can now
*preview* and *apply* a change: *"Allow the DMZ web server to reach 8.8.8.8 on
53 — show me the change first."* PolicyPilot returns the proposed rule; on
approval it publishes; and it can **roll back**. That's the guarded-write
counterpart to the read-only Quantum Management tools, and the network-access
twin of the [SCIM identity](Identity_Provisioning_SCIM_Agent_Guide.md) lesson.

> **Status:** the compose wiring ships ready but **inactive** and is validated by
> `docker compose config`; a full runtime check needs the two prerequisites above
> (Artifactory build + a populated PolicyPilot DB/key), which live outside this
> repo.
