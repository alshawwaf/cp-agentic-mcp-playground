# Local patches to the Check Point MCP servers (and why)

The sources under `docker/n8n/mcp-src/` are vendored from Check Point's official
MCP servers monorepo — [CheckPointSW/mcp-servers](https://github.com/CheckPointSW/mcp-servers)
(MIT License, © 2025 Check Point Software Technologies Ltd.) — with **two local
patches** applied. This file documents exactly what changed, why it was
necessary, and which servers are safe to put behind an MCP gateway.

---

## Patch 1 — HTTP transport support for the sidecar deployment

Commit `b82c4ca`. Wrapper/launch adjustments so every server runs as a
long-lived **Streamable HTTP** sidecar (`--transport http --transport-port N`)
on the internal Docker network, instead of stdio-per-client.

## Patch 2 — one MCP server instance **per HTTP session** (the gateway fix)

Commit `56b7b02`. **This is the patch that makes gateway fronting possible.**

**The bug:** the upstream packages build a single module-level `McpServer` and
the launcher called `server.connect(transport)` for every new HTTP session. The
MCP SDK forbids connecting one server instance to more than one transport — the
second concurrent session throws `Already connected to a transport`. Effectively
the servers were **single-client**:

* Direct n8n use mostly worked (sessions are short-lived and sequential).
* A **gateway cannot work**: it holds its own long-lived session (for tool
  enumeration) *and* opens additional upstream sessions for clients —
  guaranteed concurrency → the second session breaks → the client sees a hang /
  `MCP error -32001`.

**The fix (mirrors upstream's own architecture):**

* `packages/mcp-utils/src/launcher.ts` — accepts an optional per-session
  `createServer()` factory on the server module. When present, every new
  Streamable HTTP session gets a **fresh server instance** (torn down on session
  close). Without it, the old shared-singleton behavior remains (backward
  compatible).
* `packages/management/src/index.ts` and
  `packages/documentation-tool/src/index.ts` — export that factory by wrapping
  their tool/prompt registration so it can run per session.

**Upstream status (checked 2026-07-03):** upstream's launcher has since added
the same mechanism (named `createServerInstance`, with a warned single-instance
fallback) — but the product packages (e.g. `management`) **still do not provide
the factory**, so stock packages remain single-client over HTTP and still fail
behind a concurrent gateway. Until Check Point wires the factory into each
package, a patch like this one is required. The right long-term move is an
upstream PR contributing the per-package factories (and renaming our hook from
`createServer` to upstream's `createServerInstance` for a clean diff).

---

## Gateway-capability matrix

| Package (npm-style name) | Runs direct (n8n → sidecar) | Behind MCP gateway |
|---|---|---|
| `quantum-management-mcp` | ✅ | ✅ **patched** (per-session factory) |
| `documentation-mcp` | ✅ | ✅ **patched** (per-session factory) |
| `https-inspection-mcp` | ✅ | ⚠️ single-client — not gateway-safe yet |
| `management-logs-mcp` | ✅ | ⚠️ single-client — not gateway-safe yet |
| `threat-emulation-mcp` | ✅ | ⚠️ single-client — not gateway-safe yet |
| `threat-prevention-mcp` | ✅ | ⚠️ single-client — not gateway-safe yet |
| `reputation-service-mcp` | ✅ | ⚠️ single-client — not gateway-safe yet |
| `spark-management-mcp` | ✅ | ⚠️ single-client — not gateway-safe yet |
| `harmony-sase-mcp` | ✅ | ⚠️ single-client — not gateway-safe yet |
| `quantum-gw-cli-mcp` | ✅ | ⚠️ single-client — not gateway-safe yet |
| `quantum-gw-connection-analysis-mcp` | ✅ | ⚠️ single-client — not gateway-safe yet |
| `quantum-gaia-mcp` | ✅ | ⚠️ single-client — not gateway-safe yet |
| `cpinfo-analysis-mcp` | ✅ | ⚠️ single-client — not gateway-safe yet |
| `mcp-utils`, `quantum-infra`, `harmony-infra`, `quantum-gw-cli-base` | — support libraries, not servers | — |

“Not gateway-safe yet” = the package still uses the shared singleton; behind a
gateway its second concurrent session throws `Already connected to a transport`.
That's also **the exercise**: making one of these gateway-ready is a ~30-line
change following the `management` package as the example.

## How to make another package gateway-ready

1. In `packages/<pkg>/src/index.ts`, move tool/prompt registration into a
   function that takes a server instance (see `packages/management/src/index.ts`
   for the pattern).
2. Export `createServer` on the server module passed to `launchMCPServer` — it
   must return a fresh, fully-registered server instance.
3. Rebuild the image (`docker compose build n8n`), recreate the sidecar, add the
   server to `mcp-gateway/catalog.yaml` + the gateway's `--servers=` list, give
   it a healthcheck + `depends_on: service_healthy`, and redeploy.

## Grabbing the built packages (for students)

Every build of the sidecar image packs all packages as npm tarballs. They're
published on the repo's **GitHub Releases** page (asset set
`gateway-ready-mcp-servers`), or extract them from any built image yourself:

```bash
docker create --name x custom-mcp-n8n:custom
docker cp x:/opt/artifacts ./mcp-tarballs && docker rm x
# install e.g. the gateway-ready management server:
npm install ./mcp-tarballs/chkp-quantum-management-mcp-1.0.1.tgz
```

MIT-licensed — keep the Check Point copyright notice when redistributing.
