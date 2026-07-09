# The Check Point Docker MCP Gateway, Explained

*Grab a coffee. This is the "what is that `mcp-gateway` box actually doing"
walkthrough — plain-English, but with the real names and knobs from this repo so
you can trust it.*

---

## 1. What it is

The MCP gateway is an **aggregator / reverse proxy for MCP servers**. This
playground runs a dozen small Check Point MCP servers (one per product area), and
each of them speaks the [Model Context Protocol](https://modelcontextprotocol.io)
so an AI agent can call its tools. Wiring an agent to ten of those separately is
tedious. The gateway collapses them into **one endpoint behind one credential**:

```
http://mcp-gateway:8080/mcp        (Streamable-HTTP, Bearer auth)
```

A client connects once, and the gateway fans the request out to whichever backing
server owns the tool. It is the exact same idea as an API gateway (Kong, Apigee,
an ALB) — one front door, many services behind it — applied to agent tools
instead of REST routes. The image is Docker's own `docker/mcp-gateway`
(pinned by digest in `docker-compose.yml`).

---

## 2. Why use it — direct vs. gateway

Both patterns ship in this repo and both work. They teach different things, so
pick per use case rather than declaring a winner.

| | **Direct** (agent → one sidecar) | **Gateway** (agent → `mcp-gateway`) |
|---|---|---|
| Endpoint | `http://mcp-<server>:<port>` | `http://mcp-gateway:8080/mcp` |
| Auth | none (internal `demo` network) | Bearer token, every request |
| Tool surface | just that one server's tools | **all ≈180 tools** from all 10 servers |
| Per-agent setup | simplest | one credential, reused everywhere |
| Single choke point for audit / rate-limit / allow-deny | no | **yes** |
| Token / latency cost | low (small tool list) | higher — the LLM sees the whole catalog |
| Tool-selection accuracy | easy (scoped) | can confuse the model (many similar names) |

**Rule of thumb.** Use **direct** when an agent only ever needs one domain (a
Gaia-only agent, a reputation-lookup agent) — the model stays focused and there's
nothing to authenticate internally. Use the **gateway** when you want one place to
enforce policy, or when an agent legitimately roams across products and you'd
rather manage one connection than ten. The cost of the gateway is the big flat
tool list; section 7 covers trimming it.

---

## 3. How it's configured

Two files, kept in sync by hand. A custom catalog does **not** auto-enable its
servers — you must also name them in `--servers=`.

**`mcp-gateway/catalog.yaml`** — declares each backing server as a `remote`
streamable MCP URL. The 10 fronted servers:

| Catalog key          | Sidecar URL                          |
|----------------------|--------------------------------------|
| `documentation`      | `http://mcp-documentation:3000`      |
| `quantum-management` | `http://mcp-quantum-management:3002` |
| `cpinfo-analysis`    | `http://cpinfo-analysis-mcp:3012`    |
| `https-inspection`   | `http://mcp-https-inspection:3001`   |
| `management-logs`    | `http://mcp-management-logs:3003`    |
| `gaia`               | `http://quantum-gaia-mcp:3011/mcp`   |
| `gw-cli`             | `http://quantum-gw-cli-mcp:3009`     |
| `reputation-service` | `http://reputation-service-mcp:3007` |
| `threat-emulation`   | `http://threat-emulation-mcp:3004`   |
| `threat-prevention`  | `http://threat-prevention-mcp:3005`  |

**`docker-compose.yml` → the `mcp-gateway` service** (the load-bearing bits):

```yaml
mcp-gateway:
  image: docker/mcp-gateway@sha256:97ec61...
  command:
    - "--transport=streaming"
    - "--port=8080"
    - "--catalog=checkpoint-mcp.yaml"
    - "--servers=documentation,quantum-management,cpinfo-analysis,https-inspection,management-logs,gaia,gw-cli,reputation-service,threat-emulation,threat-prevention"
  environment:
    - MCP_GATEWAY_AUTH_TOKEN=${MCP_GATEWAY_TOKEN:-cp-mcp-gateway-training-token}
    - DOCKER_MCP_ALLOW_INSECURE_REMOTE_URLS=1   # sidecars are plain HTTP on the private net
  depends_on:
    mcp-documentation:      { condition: service_healthy }
    mcp-quantum-management: { condition: service_healthy }
  healthcheck:
    test: [ "CMD", "nc", "-z", "127.0.0.1", "8080" ]
```

Three things worth internalizing:

- **The token is pinned.** `MCP_GATEWAY_TOKEN` (from `.env`, default
  `cp-mcp-gateway-training-token`) is mapped into the container as
  `MCP_GATEWAY_AUTH_TOKEN`. This matters: *without* pinning, the gateway mints a
  **new random Bearer token on every restart** and silently 401s every client
  that still has the old one. Pinning it makes restarts boring.
- **Boot ordering is deliberate.** The gateway enumerates each server's tools
  **once, at startup**. If a sidecar isn't listening yet, the gateway comes up
  with *zero* of that server's tools and won't retry on its own. So the sidecars
  have healthchecks and the gateway `depends_on` their `service_healthy`
  condition (gated on `mcp-documentation` + `mcp-quantum-management` as the
  representative pair). If you ever see an empty tool list on a running stack, the
  cure is a one-liner: `docker restart mcp-gateway`.
- **No published port.** The gateway lives only on the internal `demo` network.
  You reach it from other containers, not from your laptop's browser.

---

## 4. How it works at runtime

MCP is a stateful, session-based protocol. A client cannot just ask for tools
cold — there's a handshake first, and skipping it is the single most common
"why do I get zero tools?" gotcha.

```
1. initialize                 → server returns capabilities + an Mcp-Session-Id header
2. notifications/initialized  → client confirms it's ready (no response expected)
3. tools/list                 → NOW you get the real catalog (≈180 tools)
4. tools/call                 → invoke a specific tool
```

> A bare `tools/list` **before** the handshake returns **0 tools**. That's
> expected MCP behavior, not a bug. n8n's MCP client does the handshake for you;
> a hand-rolled curl must do it explicitly.

Responses come back as **Server-Sent Events**, so each reply is an
`event: message\ndata: {json}` frame rather than a plain JSON body — set
`Accept: text/event-stream`. Every request carries the Bearer header.

A minimal curl (run it *inside the network* — e.g. `docker compose exec n8n sh` —
since the gateway has no host port):

```bash
curl -sN http://mcp-gateway:8080/mcp \
  -H "Authorization: Bearer ${MCP_GATEWAY_TOKEN}" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize",
       "params":{"protocolVersion":"2025-06-18",
                 "capabilities":{},
                 "clientInfo":{"name":"curl","version":"0"}}}'
# grab the Mcp-Session-Id from the response headers, echo it back on the next
# calls (notifications/initialized, then tools/list) via -H "Mcp-Session-Id: ..."
```

Wrong or missing token → **401 Unauthorized**. Right token, no handshake → an
empty tool list.

---

## 5. How the n8n agents use it

Every agent in this playground reaches MCP through the **native**
`@n8n/n8n-nodes-langchain.mcpClientTool` node (v1.2) — no custom code. Each
Check Point server ships as a **twin pair** of workflows:

- **Direct** (e.g. `quantum-management-mcp`): the node points at
  `http://mcp-quantum-management:3002`, `authentication: None`, no credential. It
  exposes that one server's whole tool set.
- **Via-gateway** (e.g. `quantum-management-via-gateway`): the node points at
  `http://mcp-gateway:8080/mcp`, `authentication: bearerAuth`, using the
  credential **`CP MCP Gateway Bearer`** (type `httpBearerAuth`; its token is
  filled from `MCP_GATEWAY_TOKEN` at import time). All ten `*-via-gateway` twins
  are committed and re-imported on every redeploy.

One nuance the node makes easy: it *can* auto-discover every tool an endpoint
exposes, and the direct workflows let it (their tool list = the one server). But
the gateway exposes all ≈180 tools at once, so the shipped `*-via-gateway` twins
use the node's built-in filter — `include: selected` with a curated
`includeTools` list — to scope each twin back down to just its own server's tools
(quantum-management picks 51, gaia 30, gw-cli 37, and so on). That keeps the
gateway twin an honest apples-to-apples comparison against its direct counterpart,
and it's a live demonstration of the tool-trimming trick in section 7.

---

## 6. Model-agnostic — including AWS Bedrock

Here's the part people mix up: **MCP governs the *tools*, not the *LLM*.** The
gateway has no idea which model is driving the agent, and it doesn't care. Swap
the brain and every tool call is byte-for-byte identical.

In n8n that's literally the "Chat Model" input port on the agent node. Each
workflow here ships **five** model nodes, and only one is wired at a time:

| Chat Model node (n8n)          | Provider                    | Status in repo |
|--------------------------------|-----------------------------|----------------|
| `lmChatAzureOpenAi`            | Azure OpenAI                | **wired default** |
| `lmChatOpenAi`                | OpenAI                      | present, unwired |
| `lmChatAnthropic`             | Anthropic (direct API)      | present, unwired |
| `lmChatGoogleGemini`          | Google Gemini               | present, unwired |
| `lmChatOllama`                | local Ollama (e.g. qwen)    | present, unwired |

To change models you drag a different model node onto the agent's Chat Model
port. Nothing about the MCP gateway, the credential, or the tools changes.

**AWS Bedrock fits right in.** n8n has an **AWS Bedrock Chat Model** node — so you
can run **Anthropic Claude on Bedrock** (or any Bedrock-hosted model) as the
agent's brain by connecting that node to the same port. It isn't pre-wired in this
repo, but adding it is the same drag-and-drop. Bedrock specifics live entirely on
the *model* node — an **AWS region**, a **model id** (e.g. an
`anthropic.claude-…` Bedrock id), and **IAM credentials**. The gateway endpoint,
the Bearer token, and all ≈180 tools stay exactly as they are.

And this isn't n8n-specific. MCP is Anthropic's open standard, and this gateway
implements the **Streamable-HTTP** transport, so *any* MCP-capable client —
Claude Desktop, Cursor, a custom script — can point at
`http://mcp-gateway:8080/mcp` with the Bearer token and get the same catalog.

---

## 7. Other options / extending it

- **Add a server to the gateway.** Declare it in `mcp-gateway/catalog.yaml`
  *and* append its key to `--servers=` in `docker-compose.yml` (both, or it won't
  enable). Give the sidecar a healthcheck and add it to the gateway's
  `depends_on` so the boot race doesn't bite. Its tools then appear through the
  gateway with **no client-side change**. Caveat: only *gateway-ready* (multi-
  session) servers survive the gateway's concurrent connections — see
  `docker/n8n/mcp-src/PATCHES.md` for the recipe.
- **Three sidecars aren't fronted yet.** `spark-management`, `harmony-sase`, and
  `quantum-gw-connection-analysis` run in the stack but are absent from
  `catalog.yaml` / `--servers=`, and have no agent workflow. They're the natural
  "make one gateway-ready and wire it up" exercise.
- **Trim the ≈180-tool surface.** The flat catalog is the gateway's main downside
  for the LLM. Filter it — either client-side (the `include: selected` list the
  via-gateway twins already use) or, for real deployments, at the gateway itself
  as the single choke point for tool allow/deny, argument DLP, and description
  scanning. That's the "MCP security gateway" idea, and this single front door is
  exactly where it belongs.

---

*Related: `docs/guides/MCP_Gateway_Agent_Guide.md` for the hands-on direct-vs-
gateway lab walkthrough and troubleshooting table.*
