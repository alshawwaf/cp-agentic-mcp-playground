#!/usr/bin/env python3
"""In-network checks for scripts/doctor.sh. Stdlib only (Python 3.8+).

doctor.sh runs this file in ONE throwaway container (python:3.12-alpine, capped CPU and
memory) and reads its stdout. It is never run on the host. Secrets arrive as environment
variables that doctor.sh passes by NAME (docker run -e NAME), so no value appears on a
command line, and no value is ever printed: results name variables and services only.

Usage: python - <mode>   (the script itself comes on stdin)
  keys   validate each supplied model key with a harmless list-models call
  tcp    TCP reachability of the Check Point hosts in PROBE_TCP (no TLS, no login)
  post   post-start checks inside the lab network (services listed in PROBE_SERVICES)
  health post-start gateway and direct-path checks only
  security-lab  the MCP Security Lab server (run on the <project>_security-lab network)

Output: one line per result, "R|<check>|<status>|<detail>", status ok|fail|warn|skip|info.
"""
import base64
import gzip
import json
import os
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
import uuid

TIMEOUT = float(os.environ.get("PROBE_TIMEOUT", "15") or "15")


def env(name, default=""):
    value = os.environ.get(name, "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1].strip()
    return value or default


def report(check, status, detail):
    detail = " ".join(str(detail).split())  # one line
    print("R|{0}|{1}|{2}".format(check, status, detail), flush=True)


def http(method, url, body=None, headers=None, timeout=TIMEOUT, form=False):
    """(status, headers, text). Status 0 = no HTTP answer. TLS is always verified."""
    data = None
    hdrs = dict(headers or {})
    if body is not None:
        if form:
            data = "&".join("{0}={1}".format(urllib.request.quote(k), urllib.request.quote(v))
                            for k, v in body.items()).encode()
            hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
        else:
            data = json.dumps(body).encode()
            hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            raw = resp.read()
            if raw[:2] == b"\x1f\x8b":  # Langflow gzips some list endpoints regardless of Accept-Encoding
                raw = gzip.decompress(raw)
            return resp.status, resp.headers, raw.decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        try:
            text = exc.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 - body is optional
            text = ""
        return exc.code, exc.headers, text
    except (urllib.error.URLError, socket.timeout, ConnectionError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        return 0, {}, str(reason)


def as_json(text):
    try:
        return json.loads(text)
    except ValueError:
        return None


def cookies_from(headers):
    jar = []
    for raw in (headers.get_all("Set-Cookie") or []) if hasattr(headers, "get_all") else []:
        pair = raw.split(";", 1)[0].strip()
        if "=" in pair:
            jar.append(pair)
    return "; ".join(jar)


# --------------------------------------------------------------------------- MCP
def mcp_messages(text):
    """JSON-RPC messages from a JSON or SSE reply."""
    text = text or ""
    if "data:" in text:
        out = []
        for line in text.splitlines():
            if line.startswith("data:"):
                msg = as_json(line[5:].strip())
                if msg is not None:
                    out.append(msg)
        return out
    msg = as_json(text)
    if isinstance(msg, list):
        return msg
    return [msg] if msg is not None else []


class MCPSession:
    def __init__(self, url, token=None):
        self.url = url
        self.headers = {"Accept": "application/json, text/event-stream"}
        if token is not None:
            self.headers["Authorization"] = "Bearer " + token
        self.sid = None

    def post(self, payload, timeout=TIMEOUT):
        hdrs = dict(self.headers)
        if self.sid:
            hdrs["mcp-session-id"] = self.sid
        return http("POST", self.url, payload, hdrs, timeout=timeout)

    def initialize(self):
        status, headers, text = self.post({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                       "clientInfo": {"name": "lab-doctor", "version": "1"}}})
        if status == 200:
            self.sid = headers.get("mcp-session-id") or headers.get("Mcp-Session-Id")
            self.post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return status, text

    def tools(self):
        names, cursor, rid = [], None, 2
        for _ in range(50):
            params = {"cursor": cursor} if cursor else {}
            status, _, text = self.post({"jsonrpc": "2.0", "id": rid, "method": "tools/list", "params": params},
                                        timeout=max(TIMEOUT, 30))
            if status != 200:
                return None, "tools/list HTTP {0}".format(status)
            msg = next((m for m in mcp_messages(text) if isinstance(m, dict) and m.get("id") == rid), None)
            if not msg or "result" not in msg:
                return None, "tools/list returned no result"
            names += [t.get("name", "") for t in msg["result"].get("tools", []) if isinstance(t, dict)]
            cursor = msg["result"].get("nextCursor")
            rid += 1
            if not cursor:
                break
        return names, ""

    def close(self):
        if self.sid:
            hdrs = dict(self.headers)
            hdrs["mcp-session-id"] = self.sid
            http("DELETE", self.url, None, hdrs, timeout=5)


def check_gateway():
    url = env("PROBE_GATEWAY_URL", "http://mcp-gateway:8080/mcp")
    token = env("MCP_GATEWAY_TOKEN")
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                       "clientInfo": {"name": "lab-doctor", "version": "1"}}}
    accept = {"Accept": "application/json, text/event-stream"}
    status, _, reason = http("POST", url, init, accept)
    if status == 0:
        report("gateway.reach", "fail", "no answer from the MCP gateway ({0})".format(reason))
        return
    report("gateway.no_token", "ok" if status == 401 else "fail",
           "request without a token: HTTP {0} (expected 401)".format(status))
    wrong = dict(accept, Authorization="Bearer wrong-" + uuid.uuid4().hex)
    status, _, _ = http("POST", url, init, wrong)
    report("gateway.wrong_token", "ok" if status == 401 else "fail",
           "request with a wrong token: HTTP {0} (expected 401)".format(status))
    if not token:
        report("gateway.tools", "fail", "MCP_GATEWAY_TOKEN is not set")
        return
    session = MCPSession(url, token)
    status, _ = session.initialize()
    if status != 200:
        hint = " (the token in .env does not match the running gateway: docker compose up -d mcp-gateway)" \
            if status == 401 else ""
        report("gateway.tools", "fail", "initialize with MCP_GATEWAY_TOKEN: HTTP {0}{1}".format(status, hint))
        return
    names, err = session.tools()
    session.close()
    if names is None:
        report("gateway.tools", "fail", err)
    elif not names:
        report("gateway.tools", "fail", "the gateway lists 0 tools (it started before its servers: "
                                        "docker compose restart mcp-gateway)")
    else:
        report("gateway.tools", "ok", "{0} tools with MCP_GATEWAY_TOKEN".format(len(names)))
        print("N|gateway.tools|{0}".format(len(names)), flush=True)


def check_direct():
    """Each catalog server answers initialize + tools/list directly (no gateway)."""
    spec = env("PROBE_DIRECT")
    if not spec:
        return
    total, empty = 0, []
    for item in spec.split(","):
        if "=" not in item:
            continue
        name, url = item.split("=", 1)
        session = MCPSession(url)
        status, _ = session.initialize()
        if status != 200:
            report("direct." + name, "fail", "{0}: initialize HTTP {1}".format(url, status or "no answer"))
            continue
        names, err = session.tools()
        session.close()
        if names is None:
            report("direct." + name, "fail", "{0}: {1}".format(url, err))
            continue
        total += len(names)
        if not names:
            empty.append(name)
        report("direct." + name, "ok" if names else "warn", "{0} tools at {1}".format(len(names), url))
    print("N|direct.tools|{0}".format(total), flush=True)


# --------------------------------------------------------------------------- builders
def check_n8n(expected):
    base = "http://n8n:5678"
    email, password = env("N8N_ADMIN_EMAIL"), env("N8N_ADMIN_PASSWORD")
    status, headers, _ = http("POST", base + "/rest/login",
                              {"emailOrLdapLoginId": email, "password": password})
    if status != 200:
        report("seed.n8n", "fail", "n8n sign-in with N8N_ADMIN_EMAIL / N8N_ADMIN_PASSWORD: HTTP {0}".format(status))
        return
    cookie = cookies_from(headers)
    status, _, text = http("GET", base + "/rest/workflows", headers={"Cookie": cookie}, timeout=60)
    data = as_json(text)
    rows = data.get("data") if isinstance(data, dict) else None
    if status != 200 or not isinstance(rows, list):
        report("seed.n8n", "fail", "cannot list n8n workflows (HTTP {0})".format(status))
        return
    active = sum(1 for r in rows if isinstance(r, dict) and r.get("active"))
    state = "ok" if len(rows) >= expected else "fail"
    report("seed.n8n", state, "{0} of {1} lab workflows in n8n, {2} published".format(len(rows), expected, active))


def check_flowise(expected):
    base = "http://flowise:{0}".format(env("FLOWISE_PORT", "3020"))
    status, headers, _ = http("POST", base + "/api/v1/auth/login",
                              {"email": env("N8N_ADMIN_EMAIL"), "password": env("N8N_ADMIN_PASSWORD")})
    cookie = cookies_from(headers) if status == 200 else ""
    if not cookie:
        report("seed.flowise", "fail", "Flowise sign-in with the lab admin: HTTP {0}".format(status))
        return
    hdrs = {"Cookie": cookie, "x-request-from": "internal"}
    seen = {}
    for query in ("", "?type=AGENTFLOW"):
        status, _, text = http("GET", base + "/api/v1/chatflows" + query, headers=hdrs, timeout=60)
        rows = as_json(text)
        if isinstance(rows, dict):
            rows = rows.get("data")
        if status == 200 and isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict) and row.get("id"):
                    seen[row["id"]] = row
    if not seen:
        report("seed.flowise", "fail", "cannot list Flowise flows")
        return
    seeded = sum(1 for r in seen.values() if '"labSeed"' in str(r.get("flowData") or ""))
    state = "ok" if seeded >= expected else "fail"
    report("seed.flowise", state, "{0} of {1} lab agents in Flowise ({2} flows in total)".format(
        seeded, expected, len(seen)))


def check_langflow(expected):
    base = "http://langflow:7860"
    status, _, text = http("POST", base + "/api/v1/login",
                           {"username": env("N8N_ADMIN_EMAIL"), "password": env("N8N_ADMIN_PASSWORD")}, form=True)
    token = (as_json(text) or {}).get("access_token") if status == 200 else None
    if not token:
        report("seed.langflow", "fail", "Langflow sign-in with the lab admin: HTTP {0}".format(status))
        return
    status, _, text = http("GET", base + "/api/v1/flows/?get_all=true&remove_example_flows=true",
                           headers={"Authorization": "Bearer " + token}, timeout=90)
    rows = as_json(text)
    if isinstance(rows, dict):
        rows = rows.get("items") or rows.get("flows")
    if status != 200 or not isinstance(rows, list):
        report("seed.langflow", "fail", "cannot list Langflow flows (HTTP {0})".format(status))
        return
    seeded = sum(1 for r in rows if isinstance(r, dict) and isinstance(r.get("data"), dict)
                 and "labSeed" in r["data"])
    state = "ok" if seeded >= expected else "fail"
    report("seed.langflow", state, "{0} of {1} lab agents in Langflow ({2} flows in total)".format(
        seeded, expected, len(rows)))


# --------------------------------------------------------------------------- model + tracing
def check_litellm(chat):
    base = "http://litellm:4000"
    key = env("LITELLM_MASTER_KEY")
    status, _, _ = http("GET", base + "/v1/models", headers={"Authorization": "Bearer wrong-" + uuid.uuid4().hex})
    report("litellm.auth", "ok" if status == 401 else "fail",
           "LiteLLM with a wrong key: HTTP {0} (expected 401)".format(status))
    status, _, text = http("GET", base + "/v1/models", headers={"Authorization": "Bearer " + key})
    ids = [m.get("id") for m in ((as_json(text) or {}).get("data") or []) if isinstance(m, dict)]
    if status != 200 or "lab-chat" not in ids:
        report("litellm.models", "fail", "LiteLLM model list with LITELLM_MASTER_KEY: HTTP {0}, lab-chat {1}".format(
            status, "present" if "lab-chat" in ids else "missing"))
        return None
    report("litellm.models", "ok", "LiteLLM serves lab-chat")
    if not chat:
        report("litellm.chat", "skip", "chat check skipped (--skip-chat)")
        return None
    trace_id = "lab-doctor-" + uuid.uuid4().hex
    body = {"model": "lab-chat", "max_tokens": 200,
            "messages": [{"role": "user", "content": "Reply with the single word OK."}],
            "metadata": {"trace_id": trace_id, "trace_name": "lab-doctor-check",
                         "generation_name": "lab-doctor-check", "tags": ["lab-doctor"]}}
    started = time.time()
    status, _, text = http("POST", base + "/v1/chat/completions", body,
                           {"Authorization": "Bearer " + key}, timeout=float(env("PROBE_CHAT_TIMEOUT", "180")))
    took = time.time() - started
    reply = as_json(text) or {}
    if status == 200 and reply.get("choices"):
        report("litellm.chat", "ok", "lab-chat answered in {0:.0f} s".format(took))
        return trace_id
    err = (reply.get("error") or {}) if isinstance(reply, dict) else {}
    msg = err.get("message") if isinstance(err, dict) else ""
    report("litellm.chat", "fail", "lab-chat did not answer: HTTP {0} {1}".format(
        status or "no answer", (msg or "")[:200]))
    return None


def check_langfuse(trace_id):
    base = "http://langfuse:3000"
    status, _, _ = http("GET", base + "/api/public/health")
    if status != 200:
        report("langfuse.trace", "fail", "Langfuse health: HTTP {0}".format(status or "no answer"))
        return
    pk, sk = env("LANGFUSE_PUBLIC_KEY"), env("LANGFUSE_SECRET_KEY")
    if not (pk and sk):
        report("langfuse.trace", "fail", "LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY are not set")
        return
    auth = {"Authorization": "Basic " + base64.b64encode("{0}:{1}".format(pk, sk).encode()).decode()}
    status, _, _ = http("GET", base + "/api/public/projects", headers=auth)
    if status != 200:
        report("langfuse.trace", "fail", "Langfuse refused the project keys: HTTP {0}".format(status))
        return
    if not trace_id:
        report("langfuse.trace", "skip", "Langfuse accepts the project keys; no chat trace to look for")
        return
    deadline = time.time() + float(env("PROBE_TRACE_WAIT", "60"))
    while time.time() < deadline:
        status, _, _ = http("GET", base + "/api/public/traces/" + trace_id, headers=auth)
        if status == 200:
            report("langfuse.trace", "ok", "Langfuse received the lab-chat trace")
            return
        time.sleep(3)
    report("langfuse.trace", "fail", "no Langfuse trace for the lab-chat call after {0} s "
                                     "(is tracing on? docker compose logs litellm | grep lab-litellm)".format(
                                         env("PROBE_TRACE_WAIT", "60")))


def check_misc(services):
    if "qdrant" in services:
        hdrs = {"api-key": env("QDRANT_API_KEY")} if env("QDRANT_API_KEY") else {}
        status, _, text = http("GET", "http://qdrant:6333/collections/cp_docs", headers=hdrs)
        points = ((as_json(text) or {}).get("result") or {}).get("points_count") if status == 200 else None
        if points:
            report("rag.qdrant", "ok", "RAG collection cp_docs holds {0} points".format(points))
        else:
            report("rag.qdrant", "fail", "RAG collection cp_docs is missing or empty (HTTP {0}); "
                                         "run: docker compose up rag-ingest".format(status))
    if "open-webui" in services:
        status, _, _ = http("GET", "http://open-webui:8080/health")
        report("openwebui.health", "ok" if status == 200 else "fail", "Open WebUI health: HTTP {0}".format(status))


def check_security_lab():
    """The simulated vulnerable server answers on its own network: health and tool list only.
    No tool is called, so the rug pull stays disarmed."""
    base = env("PROBE_SECURITY_LAB_URL", "http://vuln-mcp:3099")
    status, _, _ = http("GET", base + "/health")
    if status != 200:
        report("securitylab.mcp", "fail", "MCP Security Lab server health on the security-lab network: HTTP {0} "
               "(docker compose logs vuln-mcp)".format(status or "no answer"))
        return
    session = MCPSession(base + "/mcp")
    status, _ = session.initialize()
    if status != 200:
        report("securitylab.mcp", "fail", "MCP Security Lab server: initialize HTTP {0}".format(status or "no answer"))
        return
    names, err = session.tools()
    session.close()
    if names is None:
        report("securitylab.mcp", "fail", "MCP Security Lab server: {0}".format(err))
        return
    report("securitylab.mcp", "ok" if names else "warn",
           "MCP Security Lab server answers on its isolated network ({0} simulated tools)".format(len(names)))


# --------------------------------------------------------------------------- preflight helpers
def check_keys():
    """Harmless list-models calls, one per supplied provider (no inference)."""
    providers = [p for p in env("PROBE_PROVIDERS").split(",") if p]
    for name in providers:
        if name == "azure":
            endpoint = env("AZURE_OPENAI_ENDPOINT").rstrip("/")
            version = env("AZURE_OPENAI_API_VERSION", "v1")
            if endpoint.endswith("/openai/v1") or endpoint.endswith("/openai"):
                endpoint = endpoint.rsplit("/openai", 1)[0]
            if version == "v1":
                url = endpoint + "/openai/v1/models"
            else:
                url = endpoint + "/openai/models?api-version=" + urllib.request.quote(version)
            status, _, reason = http("GET", url, headers={"api-key": env("AZURE_OPENAI_API_KEY")})
        elif name == "openai":
            base = env("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
            status, _, reason = http("GET", base + "/models",
                                     headers={"Authorization": "Bearer " + env("OPENAI_API_KEY")})
        elif name == "anthropic":
            status, _, reason = http("GET", "https://api.anthropic.com/v1/models",
                                     headers={"x-api-key": env("ANTHROPIC_API_KEY"),
                                              "anthropic-version": "2023-06-01"})
        elif name == "gemini":
            status, _, reason = http("GET", "https://generativelanguage.googleapis.com/v1beta/models",
                                     headers={"x-goog-api-key": env("GEMINI_API_KEY")})
        else:
            continue
        if status == 200:
            report("key." + name, "ok", "key accepted (model list)")
        elif status in (401, 403):
            report("key." + name, "fail", "key rejected: HTTP {0}".format(status))
        elif status == 404:
            report("key." + name, "fail", "HTTP 404: check the endpoint / base URL")
        elif status == 0:
            report("key." + name, "warn", "no answer from the provider ({0})".format(str(reason)[:120]))
        else:
            report("key." + name, "warn", "unexpected HTTP {0}".format(status))


def check_tcp():
    """Is a TLS server answering at host:port? A bare TCP connect is not enough: Docker Desktop's
    network proxy accepts every connect. Certificate verification stays ON; a certificate this check
    does not trust (normal for a management server's own certificate) still proves the host answers."""
    for item in [i for i in env("PROBE_TCP").split(",") if i]:
        label, target = item.split("=", 1)
        host, _, port = target.rpartition(":")
        try:
            raw = socket.create_connection((host, int(port)), timeout=6)
        except (OSError, ValueError) as exc:
            report("tcp." + label, "fail", "{0} port {1} not reachable from Docker ({2})".format(
                label, port, exc.__class__.__name__))
            continue
        raw.settimeout(8)
        ctx = ssl.create_default_context()
        try:
            with ctx.wrap_socket(raw, server_hostname=host):
                report("tcp." + label, "ok", "{0} port {1} answers (TLS)".format(label, port))
        except ssl.SSLCertVerificationError:
            report("tcp." + label, "ok", "{0} port {1} answers (TLS; its certificate is not publicly "
                                         "trusted, as usual for a management server)".format(label, port))
        except (ssl.SSLError, OSError) as exc:
            report("tcp." + label, "fail", "{0} port {1}: no TLS answer from Docker ({2})".format(
                label, port, exc.__class__.__name__))
        finally:
            raw.close()


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "post"
    services = set(s for s in env("PROBE_SERVICES").split(",") if s)
    if mode == "keys":
        check_keys()
    elif mode == "tcp":
        check_tcp()
    elif mode == "security-lab":
        check_security_lab()
    elif mode in ("post", "health"):
        if "mcp-gateway" in services:
            check_gateway()
        check_direct()
        if mode == "health":
            return 0
        expect = {k: int(env("PROBE_EXPECT_" + k.upper(), "0") or 0) for k in ("n8n", "flowise", "langflow")}
        if "n8n" in services:
            check_n8n(expect["n8n"])
        if "flowise" in services:
            check_flowise(expect["flowise"])
        if "langflow" in services:
            check_langflow(expect["langflow"])
        trace_id = check_litellm(env("PROBE_CHAT", "1") == "1") if "litellm" in services else None
        if "langfuse" in services:
            check_langfuse(trace_id)
        check_misc(services)
    else:
        report("probe", "fail", "unknown mode " + mode)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
