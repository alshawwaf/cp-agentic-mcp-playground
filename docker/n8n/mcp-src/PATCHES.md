# Local patches to the Check Point MCP servers (and why)

The sources under `docker/n8n/mcp-src/` are vendored from Check Point's official MCP servers
monorepo, [CheckPointSW/mcp-servers](https://github.com/CheckPointSW/mcp-servers) (MIT License,
© 2025 Check Point Software Technologies Ltd.). The snapshot was imported in commit `2adc5b8`
(2025-11-28). `packages/policy-insights` was added later from a newer upstream commit (see
section 11). Every lab change is marked `LAB PATCH` in the code and listed below.

The lab trains Check Point staff at scale, so these servers must be safe by default: certificates
are always verified, credentials go only where the operator configured them, and nothing secret is
logged.

| # | Patch | Defects | Files |
|---|---|---|---|
| 1 | One MCP server instance per HTTP session | (gateway fix) | `mcp-utils/src/launcher.ts`, every server's `src/index.ts` |
| 2 | Gaia headless credentials (hardened by 4) | | `gaia/src/gaia-auth.ts` |
| 3 | TLS certificate verification always on | D053, D066, D096 | `infra/src/api-client.ts`, `gaia/src/gaia-api-client.ts` |
| 4 | Gaia credentials only for configured gateways; input validation | D043, D096 | `gaia/src/gaia-auth.ts` |
| 5 | Header credentials and env credentials never mix | D044, D128 | `mcp-utils/src/launcher.ts`, `mcp-utils/src/settings-manager.ts`, the `Settings.fromHeaders()` of infra, spark-management, harmony-infra, reputation-service, threat-emulation; `reputation-service/src/lib/reputation-client.ts` |
| 6 | Session lifecycle: idle timeout, session cap, clean close (no crash on DELETE) | D127, D128 | `mcp-utils/src/launcher.ts` |
| 7 | No secrets in logs | D044 | `mcp-utils/src/redact.ts`, `mcp-utils/src/settings-manager.ts`, `infra/src/api-client.ts`, `harmony-infra/src/utils.ts`, `gaia/src/settings.ts` |
| 8 | CPInfo files only from allowed directories; no file-existence oracle | D097 | `cpinfo-analysis/src/file-access.ts` (new), `cpinfo-analysis/src/cpinfo-reader.ts` |
| 9 | Spark Management starts without credentials | D031 | `spark-management/src/settings.ts`, `spark-management/src/api-manager.ts` |
| 10 | Region names are case-insensitive | D201 | `infra/src/settings.ts` |
| 11 | Policy Insights vendored, so patches 1 and 3-7 apply to it | (npm package unpatched) | `packages/policy-insights/` (new), `package-lock.json`, `tsconfig.json`, `docker/n8n/Dockerfile` |
| 12 | Threat Emulation files only from allowed directories | (TE file_path) | `threat-emulation/src/lib/file-access.ts` (new), `threat-emulation/src/index.ts` |

Upstream status below was checked on 2026-10-01 against upstream `main`.

---

## 1. One MCP server instance per HTTP session

Commits `fd24bba` (launcher, management, documentation) and `55ade08` (the other 11 servers).

**Problem.** Each package built one module-level `McpServer`, and the launcher connected it to every
new Streamable HTTP session. The MCP SDK allows one transport per server, so the second concurrent
session failed with `Already connected to a transport`. A gateway always holds concurrent sessions.

**Fix.** The launcher accepts an optional `createServer()` factory on the server module and builds a
fresh server for each session (closed with the session). All 13 vendored servers export it.

**Upstream.** The launcher now has the same hook, named `createServerInstance`. Product packages
still do not provide it. An upstream PR with the per-package factories (renamed to
`createServerInstance`) would remove this patch.

## 2. Gaia headless credentials

Commit `ee1af1b`. Upstream Gaia asks for host, user and password in a browser dialog on localhost,
which nobody can answer in a container. The server reads `GAIA_GATEWAY_IP`, `GAIA_GATEWAY_PORT`,
`GAIA_USERNAME` and `GAIA_PASSWORD` instead. The first version paired those credentials with any
`gateway_ip` from a tool call; patch 4 fixes that.

**Upstream.** Not present (upstream is dialog-only).

## 3. TLS certificate verification always on (D053, D066, D096)

**Problem.** `OnPremAPIClient` (used by Management, Management Logs, Threat Prevention, HTTPS
Inspection, Gateway CLI, Connection Analysis and Gaia) created an `https.Agent` with certificate
verification turned off for every request, including the login that carries the Management API key or
the Gaia password. Anyone on the path could capture them. This breaks the
organization rule never to disable TLS verification.

**Fix.** On-prem clients always use an agent with `rejectUnauthorized: true` (this also overrides
`NODE_TLS_REJECT_UNAUTHORIZED`). There is deliberately no switch to turn verification off. To trust a
self-signed or private-CA certificate, use one of:

| Setting | Scope | Effect |
|---|---|---|
| `NODE_EXTRA_CA_CERTS=/path/ca.pem` | every HTTPS client in the process | Node adds the PEM file to its trust store (read once at start-up). |
| `MANAGEMENT_CA_CERT=/path/ca.pem` | management-backed servers | Adds the PEM file to Node's default trust store for these clients. It never replaces the default store. |
| `GAIA_CA_CERT=/path/ca.pem` | Gaia server | Same, for Gaia gateways. |
| `MANAGEMENT_TLS_SERVERNAME=<name>` | the configured `MANAGEMENT_HOST` only | The certificate must be valid for this DNS name instead of the address you dial (for NAT or IP access). The chain is still verified. |
| `GAIA_TLS_SERVERNAME=<name>` | the configured `GAIA_GATEWAY_IP` only | Same, for Gaia. |

The PEM file can be the server's own self-signed certificate or the CA that issued it. A missing or
unreadable file gives a clear error and nothing is sent. A failed verification returns a message that
names these settings. The server-name override never applies to a host that a session supplied in
headers, so it cannot help a foreign server pass verification.

**Upstream.** Still disables verification (`packages/infra/src/api-client.ts`). Worth an upstream PR.

### Trusting a self-signed Security Management Server or gateway (lab how-to)

1. Get the server certificate in PEM form, for example from a trusted network:
   `openssl s_client -connect <host>:443 -servername <host> </dev/null | openssl x509 > sms.pem`.
   Before you trust it, compare its SHA-256 fingerprint (`openssl x509 -in sms.pem -noout -fingerprint -sha256`)
   with the certificate the server's administrator shows you. Trusting whatever the network
   returned defeats the purpose.
2. Put the file next to the lab (for example `./certs/sms.pem`), mount it read-only into the MCP
   server containers, and set `NODE_EXTRA_CA_CERTS` (or `MANAGEMENT_CA_CERT` / `GAIA_CA_CERT`) to its
   path inside the container.
3. If the certificate does not name the address in `MANAGEMENT_HOST` (common for NAT and IP access),
   set `MANAGEMENT_TLS_SERVERNAME` to a DNS name it does contain (`openssl x509 -in sms.pem -noout -ext subjectAltName`,
   or the subject CN if there is no SAN).
4. Recreate the server containers. `TLS certificate verification failed ...` in a tool result means the
   file or the name does not match yet.

## 4. Gaia credentials only for configured gateways (D043, D096)

**Problem.** The model fills `gateway_ip` on all 42 Gaia tools. With patch 2, one prompt such as "check
the routes on 203.0.113.5", or a prompt injection in a tool result or RAG document, made the server log
in to that host with the lab's gateway admin password.

**Fix** (`gaia/src/gaia-auth.ts`):

* `gateway_ip` must be an IPv4 or IPv6 address or a DNS name (no scheme, port, path, user info or
  numeric look-alikes such as `010.0.0.1`). `port` must be 1-65535.
* Headless mode means any of `GAIA_GATEWAY_IP`, `GAIA_USERNAME`, `GAIA_PASSWORD` is defined (the
  container always defines them, maybe empty). The browser dialog is then never used, so an
  unconfigured server answers "Gaia is not configured" at once instead of waiting 5 minutes.
* The env credentials are used only for `GAIA_GATEWAY_IP` and the hosts in the optional
  `GAIA_ALLOWED_GATEWAYS` (comma or space separated, same credentials). Any other `gateway_ip` is
  refused and nothing is sent to it.
* Without any `GAIA_*` variable (desktop use) the upstream dialog flow is unchanged: the user types the
  credentials for that specific gateway.

**Upstream.** Not applicable (upstream has no env credentials). The input validation is worth upstreaming.

## 5. Header credentials and env credentials never mix (D044, D128)

**Problem.** For every request, the launcher turned the HTTP headers into per-session settings, and
each `Settings` constructor filled any missing field from `process.env`. A caller that sent only
`management-host: <their host>` got a session that logged in there with the operator's
`MANAGEMENT_API_KEY`. Headers were re-applied on every request, so anyone who learned a session ID
could re-bind that session. A failed configuration was reported as JSON-RPC `-32700 Parse error` and
left a zombie session.

**Fix.**

* The credential source is chosen once, at `initialize`, per session:
  * **Header mode** when the request carries any non-empty header for one of the server's
    credential or target options (`api-key`, `username`, `password`, `management-host`,
    `management-port`, `s1c-url`, `cloud-infra-token`, `client-id`, `secret-key`, `region`,
    `infinity-portal-url`, ...; not `origin`, which clashes with the standard HTTP header). The
    settings then come from the headers only. Each `fromHeaders()` passes `''` for missing values so
    no `process.env` default applies.
  * **Env mode** otherwise: the operator's start-up settings. Request headers are ignored.
* The settings are built and validated before the session exists. An invalid configuration returns
  HTTP 400 with `Invalid session configuration: ...` and leaves nothing behind.
* Later requests cannot change a session's settings.
* A `debug` header no longer switches on global debug logging. Debug follows `--debug` / `DEBUG` only.
* Env values win over `server-config.json` defaults at start-up (upstream fixed the same ordering), so
  `MANAGEMENT_PORT` is honored.
* Reputation Service caches one token per API key instead of one for the whole process.
* Harmony SASE `fromHeaders()` read `MANAGEMENT_HOST` (underscore), which never matched; it reads
  `MANAGEMENT-HOST` now.

**Behavior change.** Non-credential headers such as `cpinfo-encoding` or `verbose` are no longer
applied in env mode. No lab client sends them.

**Upstream.** Still mixes headers with env (`launcher.ts` and each `Settings`).

## 6. Session lifecycle (D127, D128)

**Problem.** Every Streamable HTTP session kept a full server instance (about 1-2 MB) until the
client sent `DELETE`. n8n's MCP Client Tool, Flowise, the gateway and the health check never do, so a
sidecar with `mem_limit: 128m` ran out of memory after a few dozen abandoned sessions. `/health`
listed every session ID, so anyone on the network could close or reuse other clients' sessions.

**Fix** (`mcp-utils/src/launcher.ts`):

| Setting | Default | Meaning |
|---|---|---|
| `MCP_SESSION_IDLE_TIMEOUT_SECONDS` | `1800` | Close a session after this long without a request. `0` disables. |
| `MCP_MAX_SESSIONS` | `32` | At most this many open sessions. When full, the least recently used idle session is closed; if none is idle, `initialize` gets HTTP 503. `0` disables. |

* A session with a request or SSE stream in progress is never closed by these limits.
* Closing goes through `transport.close()`, which releases the per-session server, settings, API
  manager and session data exactly once. The old `onclose` handler re-entered itself through
  `server.close()` until the stack overflowed, which killed the whole server process (every session)
  on each `DELETE` (reproduced on Node 24 and 26).
* An unknown or expired session ID gets HTTP 404 (`-32001 Session not found`), which tells MCP clients
  to start a new session. A `GET` without a valid session is rejected instead of building an unusable
  server instance.
* `/health` reports `activeSessions`, `sessionIdleTimeoutSeconds` and `maxSessions` only.
* One log line per cleanup sweep, only when something was closed.

Measured with 20-30 sessions: about 0.9-1.7 MB per session. 32 sessions fit the 128 MB limit (an idle
sidecar uses about 35-70 MB). Raise `MCP_MAX_SESSIONS` and `mem_limit` together for a shared instance.

**Upstream.** No idle timeout or session cap. `/health` reports a count only (as patched here).

## 7. No secrets in logs

* `redactSecrets()` (`mcp-utils/src/redact.ts`) masks values under keys that look like credentials
  (password, secret, token, API key, Authorization, Cookie, `X-chkp-sid`, ...). It is used for every
  debug dump of headers, CLI options, settings and request data.
* The API client no longer rethrows the raw axios error. That object carries the request config (the
  login body with the API key or password) and was logged in full on any connection or TLS error. It
  now throws a plain error with the code and message.
* Harmony SASE no longer prints its settings object (it holds the API key). Gaia no longer prints all
  CLI options for every session.

**Upstream.** Same issues upstream.

## 8. CPInfo files only from allowed directories (D097)

**Problem.** `file_path` comes from the model and was opened as given, so any file the server user can
read (for example `/proc/self/environ`) could be opened.

**Fix** (`cpinfo-analysis/src/file-access.ts`, called by `CpInfoReader.loadFile()`). Only regular files
under `CPINFO_ALLOWED_DIRS` are opened (`:` or `,` separated, default `/data/cpinfo`, where the lab
mounts `./n8n/shared`). Symlinks and `..` are resolved by the file system before the check, and the
resolved path is the one opened. A relative path is resolved against the first directory. Directories,
FIFOs, sockets and devices are refused. Paths outside get the same "Access denied" answer whether or not
they exist; "File not found" is reported only when the closest existing parent is inside an allowed
directory. The helper is a copy of the Threat Emulation one (patch 12; same logic, CPInfo names and
messages), not a shared package, so neither server depends on the other: change both together.

Follow-up (2026-10-01): the first version applied `..` lexically before resolving symlinks, so a
symlinked directory planted inside `/data/cpinfo` plus `..` still revealed whether a path outside
exists (`/data/cpinfo/link/../x` answered "Access denied" when `x` existed next to the link target and
"File not found" when it did not). Reproduced and fixed: 3 of 7 probe pairs leaked before, none after;
allowed files (relative, absolute, through an inside symlink or a symlinked allowed directory) still
open and a missing file inside still says "File not found" (Node 24.21.0 and the n8n image's Node 26.7.0).

**Upstream.** Opens any path.

## 9. Spark Management starts without credentials (D031)

The Spark `Settings` constructor threw when `CLIENT_ID`, `SECRET_KEY` or `INFINITY_PORTAL_URL` was
missing, so the process exited at start-up (a crash loop under `restart: unless-stopped`) unlike every
other server. Validation moved to `SMPAPIManager.create()`, so the server starts and the tool call
reports what is missing. The compose file still stops the service cleanly while it is unconfigured.

## 10. Region names are case-insensitive (D201)

`Settings` stored `REGION` as given, but `getCloudInfraGateway()` matches upper-case names only, so
`us` gave an empty gateway URL. The region is now stored upper-cased.

## 11. Policy Insights vendored (the npm package was unpatched)

**Problem.** `policy-insights-mcp` was installed from npm (`@chkp/policy-insights-mcp@0.3.5`). That
package bundles its own copy of upstream `@chkp/quantum-infra` and `@chkp/mcp-utils`, so patches 1
and 3-7 did not apply to a server that receives the lab's `MANAGEMENT_API_KEY` and sits behind the
gateway. Reproduced with the published package:

* TLS verification off: a self-signed management server received the API key.
* A `management-host` header alone made the server send the operator's API key to that host, and
  headers on a later request re-bound an existing session.
* Every tool call logged its full context (including the caller's `api-key` header), its
  arguments, the response (customer policy data) and raw error objects.
* Idle sessions were never closed.
* Every tool call posted usage telemetry to `metrics.security.ai.checkpoint.com` unless
  `TELEMETRY_DISABLED=true` (compose sets it).

**Source and license.**

| | |
|---|---|
| Source | [CheckPointSW/mcp-servers](https://github.com/CheckPointSW/mcp-servers) `packages/policy-insights` at commit `ef34749b63b0e37db8b0080e1805ba3f38cd24dd` (2026-06-17, "Sync from internal repo (#268)"), the only upstream commit with version 0.3.5. Upstream has no tags for this package. |
| Same as npm 0.3.5 | The npm metadata `gitHead` of `@chkp/policy-insights-mcp@0.3.5` is that commit (tarball `sha512-FU/ozuT0lkdTRgw0hGmBoJpSh4POy0lRzuIv0R41u3VXByd0pLA6t/iM4diZC/j+PMtPNsqMenv/0kkp8q2/Tw==`). Its README and CHANGELOG are byte-identical and its bundle contains the same `src/` code. The npm package ships only the built bundle; the TypeScript sources come from git. |
| License | MIT, © 2025 Check Point Software Technologies Ltd. (`"license": "MIT"`; the upstream `LICENSE` at that commit is identical to `mcp-src/LICENSE`). |
| Imported | 2026-10-01, sparse git fetch of that commit. |

Copied unchanged: `src/toolDefinitionMap.ts`, `src/zodSchemas.ts`, `src/serverInfo.ts`,
`src/server-config.json`, `CHANGELOG.md`, `.eslintrc.json`, `.gitignore`, `.npmignore`, `.prettierrc`.
Changed:

* `src/index.ts` (`LAB PATCH`): a per-session `createServer` factory (patch 1) instead of upstream's
  `createMcpServer()`, which the vendored `mcp-utils` does not have and which wraps every tool in the
  telemetry call. No dump of the tool context, arguments, responses or error objects: with `--debug` /
  `DEBUG=true` it logs the tool, the API path and the arguments through `redactSecrets()`. The
  model-supplied `domain` must be at most 255 characters without control characters. With
  `domains_to_process` the call is not routed to `domain` (it must run from the System Domain; newer
  upstream `quantum-infra` does the same, the vendored one does not). Tool names, descriptions and
  input schemas are unchanged: `tools/list` returns the same answer as npm 0.3.5.
* `package.json`: `@chkp/mcp-utils` and `@chkp/quantum-infra` are devDependencies (bundled, like in the
  other packages). Removed: `prepack`/`postpack` (they call `scripts/strip-bundled-deps.js`, not
  vendored), `node-machine-id` (used only by upstream telemetry) and the lint devDependencies.
  `@modelcontextprotocol/sdk` `^1.26.0` became `^1.22.0`, `axios` `^1.13.5` became `^1.13.2` and
  `@types/node` `^22.15.2` became `^20.0.0`: the versions already in the workspace lock, so no new
  package enters it. The image installs all tarballs with one `npm install`, which picks the newest
  matching releases anyway (on 2026-10-01: SDK 1.31.0, axios 1.20.0), as for every other server.
* `tsconfig.json`: the workspace layout (extends the root config, `src` to `dist`). The root
  `tsconfig.json` references the package.
* `README.md`: a lab note at the top.

No new third-party dependency. `package-lock.json` gains exactly two entries, `packages/policy-insights`
and its `node_modules/@chkp/policy-insights-mcp` workspace link; no other entry changed.
`docker/n8n/Dockerfile` installs the locally built tarball; the CLI name stays `policy-insights-mcp`.

**Behavior changes.** No telemetry, so `TELEMETRY_DISABLED` no longer matters and `--no-telemetry` /
`--telemetry-url` are rejected as unknown options (compose passes neither). The upstream-only headers
`x-chkp-sid`, `cloud-connected` and `gateway-url` are ignored (the vendored `quantum-infra` has no such
settings). As for the other management servers, a credential-less `management-host` header gets HTTP
400 (patch 5) and a self-signed management server needs `MANAGEMENT_CA_CERT` (patch 3).

**Upstream.** `main` (0.4.1) still logs the tool context and responses, sends telemetry by default
and disables TLS verification in `quantum-infra`. Worth an upstream PR together with patches 3 and 7.

## 12. Threat Emulation files only from allowed directories

**Problem.** `file_path` of `upload_file`, `scan_file` and `query_file` comes from the model and was
opened as given. Any file the server user could read was uploaded to the Threat Emulation cloud (a
prompt injection in a document could ask for it), `/proc/self/environ` was opened and its MD5 sent, and
"File not found" versus any other answer revealed whether a path exists.

**Fix** (`threat-emulation/src/lib/file-access.ts`), the same approach as patch 8: only regular files
under `TE_ALLOWED_DIRS` are read (`:` or `,` separated, default `/data/shared`, where the lab mounts
`./n8n/shared`). Symlinks and `..` are resolved by the file system before the check, and the resolved
path is the one read. A relative path is resolved against the first directory. Directories, FIFOs,
sockets and devices are refused. Paths outside get the same "Access denied" answer whether or not they
exist; "File not found" is reported only when the closest existing parent is inside an allowed
directory. `query_file` never hashes a file outside; for a missing file inside it still queries
without the MD5, as upstream does.

**Upstream.** `main` still opens any path.

---

## Gateway-capability matrix

| Package (npm name) | Per-session factory | Patches 3-7 |
|---|---|---|
| `quantum-management-mcp`, `management-logs-mcp`, `threat-prevention-mcp`, `https-inspection-mcp`, `quantum-gw-cli-mcp`, `quantum-gw-connection-analysis-mcp` | Yes (patch 1) | Yes |
| `quantum-gaia-mcp` | Yes | Yes, plus patch 4 |
| `documentation-mcp`, `reputation-service-mcp`, `spark-management-mcp`, `harmony-sase-mcp` | Yes | Yes (no on-prem TLS) |
| `threat-emulation-mcp` | Yes | Yes (no on-prem TLS), plus patch 12 |
| `cpinfo-analysis-mcp` | Yes | Yes, plus patch 8 |
| `policy-insights-mcp` | Yes (patch 11) | Yes (vendored since patch 11) |
| `mcp-utils`, `quantum-infra`, `harmony-infra`, `quantum-gw-cli-base` | Support libraries | |

All 14 vendored servers are gateway-ready; there is no "make a server gateway-ready" exercise left in
this tree. A new vendored package needs the same `createServer` factory (see
`packages/management/src/index.ts`).

## Building

The lab image (`docker/n8n/Dockerfile`) builds these packages and also packs them as npm tarballs in
`/opt/artifacts`. To get them from a built image:

```bash
docker create --name mcp-artifacts ghcr.io/alshawwaf/cp-agentic-n8n:latest
docker cp mcp-artifacts:/opt/artifacts ./mcp-tarballs && docker rm mcp-artifacts
```

There are no GitHub Releases for these tarballs. MIT-licensed: keep the Check Point copyright notice
when you redistribute them.

Build note (2026-10-01): with the builder's npm 11.19.0, `npm ci` stops with
`lock file's rimraf@6.1.2 does not satisfy rimraf@6.1.3` (the `rimraf` override in `package.json` is
re-resolved to the newest 6.x). npm 10 accepts the same lock file.
