#!/usr/bin/env python3
"""Acceptance tests for the Check Point AI agent lab: every promise the lab makes, as a check.

tests/acceptance/run.sh starts this file in ONE throwaway python:3.12-alpine container on the
lab network. It is never run on the host. Standard library only (Python 3.8+).

Inputs, prepared by run.sh:
  environment  the secrets and settings the checks use, from .env through a mode-600 env file
               that run.sh deletes as soon as the container is created. LAB_SET_<NAME>=1 says a
               setting is set without passing its value (provider keys, product credentials).
  /lab         the repository, read-only: the expected tool lists (scripts/flows/langflow_fix.py),
               the agent catalog (integrations/builders_agents.json), workflows and flows.
  /facts       facts only the host can see, read-only: the services of the active profiles and
               their state (docker inspect), the environment variable NAMES of the builder
               containers, and the result of the provider mock (run.sh --mock-provider).
  /out         result.json (run.sh --json FILE copies it).

Output: one line per check (PASS, FAIL or SKIP) with a fix hint under each FAIL. Exit status 1
when a check fails, 0 otherwise, 2 when the suite cannot run. Secret values are never printed:
every output line is redacted against the secrets this process holds.

  python acceptance.py                      the checks (run.sh does this)
  python acceptance.py --list               the check ids
  python acceptance.py --phase provider-mock
                                            the provider half of KEYS, run by run.sh inside the
                                            mock provider container (next to a throwaway LiteLLM)
"""
import argparse
import base64
import glob
import gzip
import hashlib
import json
import os
import re
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.client import HTTPException

LAB = os.environ.get("LAB_REPO", "/lab")
FACTS = os.environ.get("LAB_FACTS", "/facts")
OUT = os.environ.get("LAB_OUT", "/out")

GATEWAY_URL = "http://mcp-gateway:8080/mcp"
LITELLM = "http://litellm:4000"
N8N = "http://n8n:5678"
LANGFLOW = "http://langflow:7860"
LANGFUSE = "http://langfuse:3000"
QDRANT = "http://qdrant:6333"
OLLAMA = "http://ollama-cpu:11434"
OPEN_WEBUI = "http://open-webui:8080"
VULN_MCP = "http://vuln-mcp:3099"
AIG = "http://aig-webserver:8088"
IPS_CVE = "http://ips-cve-mcp:3013/mcp"
EMBED_MODEL = "nomic-embed-text"
EMBED_DIM = 768                       # nomic-embed-text vector size
EXPECTED_GATEWAY_TOOLS = 190          # the lab's promise (also the sum of SERVER_TOOLS)
LAB_KEY_NAME = "Lab Agents API"       # Flowise API key that guards /api/v1/prediction
MODEL_CREDENTIAL = "Lab Model (LiteLLM)"
DOCS_PROMPT = ("Use the ask-checkpoint-docs tool once to look up what Check Point Gaia is, "
               "then answer in one short sentence.")
RAG_PROMPT = "How do I enable Identity Awareness? Cite the source document."

# Credential-gated MCP servers: they stop with "not configured" while one of these is blank.
GATED = {
    "spark-management-mcp": (("SPARK_MGMT_CLIENT_ID", "SPARK_MGMT_SECRET_KEY"), "http://spark-management-mcp:3006"),
    "harmony-sase-mcp": (("HARMONY_SASE_API_KEY", "HARMONY_SASE_MANAGEMENT_HOST", "HARMONY_SASE_ORIGIN"),
                         "http://harmony-sase-mcp:3008"),
}
DIRECT_ONLY = {"quantum-gw-connection-analysis-mcp": "http://quantum-gw-connection-analysis-mcp:3010"}
BUILDERS = ("n8n", "n8n-import", "flowise", "langflow", "builders-import")

# id, title, the service that must be part of the active profiles (None = the default lab), timeout s
CHECKS = [
    ("STACK", "Every service of the active profiles is healthy", None, 30),
    ("GW-AUTH", "MCP gateway requires its Bearer token", None, 60),
    ("GW-TOOLS", "MCP gateway lists every server's tools", None, 90),
    ("DIRECT", "Check Point MCP servers answer directly", None, 180),
    ("LITELLM", "lab-chat on LiteLLM", None, 420),
    ("KEYS", "Provider keys reach only LiteLLM", None, 30),
    ("N8N-SEED", "n8n agents seeded and published", None, 240),
    ("N8N-RUN", "n8n agents answer with MCP tools", None, 600),
    ("FLOWISE-SEED", "Flowise agents seeded", None, 420),
    ("FLOWISE-RUN", "Flowise agents answer with MCP tools", None, 600),
    ("LANGFLOW-SEED", "Langflow agents seeded", "langflow", 600),
    ("LANGFLOW-RUN", "Langflow agents answer with MCP tools", "langflow", 600),
    ("RAG", "Documentation RAG retrieval", None, 420),
    ("CODE-AGENT", "Code-first agent", None, 420),
    ("LANGFUSE", "Langfuse receives the traces", None, 150),
    ("OPENWEBUI", "Open WebUI", "open-webui", 90),
    ("SECLAB", "MCP Security Lab", "vuln-mcp", 90),
    ("AIG", "AI-Infra-Guard", "aig-webserver", 60),
    ("EXERCISES", "Build Your Own MCP exercise", "ips-cve-mcp", 60),
    ("EVALS", "Evals scorecard", "evals-run", 30),
]
PROFILE_OF = {"langflow": "langflow (or complete)", "open-webui": "local-chat (or complete)",
              "vuln-mcp": "security-lab", "aig-webserver": "ai-red-team", "ips-cve-mcp": "exercises",
              "evals-run": "evals"}


# =========================================================================== settings + redaction
def env(name, default=""):
    value = os.environ.get(name, "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1].strip()
    if value.startswith("op://"):        # never resolved: treat as unset
        return default
    return value or default


def is_set(name):
    return bool(env(name)) or os.environ.get("LAB_SET_" + name) == "1"


SECRET_NAME = re.compile(r"(KEY|TOKEN|PASSWORD|SECRET|SALT|COOKIE)", re.I)
_SECRETS = set()


def add_secret(value):
    if isinstance(value, str) and len(value.strip()) >= 6:
        _SECRETS.add(value.strip())


def init_secrets():
    for name, value in os.environ.items():
        if SECRET_NAME.search(name) and not name.startswith(("LAB_SET_", "LAB_OPREF_")):
            add_secret(value)
    email, password = env("N8N_ADMIN_EMAIL"), env("N8N_ADMIN_PASSWORD")
    if email and password:
        add_secret(base64.b64encode("{0}:{1}".format(email, password).encode()).decode())


def redact(text):
    text = str(text)
    for secret in sorted(_SECRETS, key=len, reverse=True):
        if secret in text:
            text = text.replace(secret, "[redacted]")
    return text


def one_line(text, width=0):
    text = " ".join(str(text).split())
    if width and len(text) > width:
        text = text[:width - 3].rstrip() + "..."
    return text


class Skip(Exception):
    def __init__(self, reason, hint=""):
        Exception.__init__(self, reason)
        self.reason, self.hint = reason, hint


class Fail(Exception):
    def __init__(self, reason, hint=""):
        Exception.__init__(self, reason)
        self.reason, self.hint = reason, hint


# =========================================================================== HTTP + MCP
_TLS = ssl.create_default_context()      # TLS is always verified


class Resp(object):
    __slots__ = ("status", "headers", "text", "error")

    def __init__(self, status, headers, text, error=""):
        self.status, self.headers, self.text, self.error = status, headers, text, error

    def json(self):
        try:
            return json.loads(self.text)
        except ValueError:
            return None

    def why(self):
        return "HTTP {0}".format(self.status) if self.status else "no answer ({0})".format(one_line(self.error, 80))


def http(method, url, body=None, headers=None, timeout=20.0, form=False):
    hdrs = dict(headers or {})
    payload = None
    if form:
        payload = urllib.parse.urlencode(body or {}).encode()
        hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
    elif body is not None:
        payload = json.dumps(body).encode()
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=payload, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_TLS) as resp:
            raw, status, rh = resp.read(), resp.status, resp.headers
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read()
        except Exception:  # noqa: BLE001 - the body is optional
            raw = b""
        status, rh = exc.code, exc.headers
    except (urllib.error.URLError, socket.timeout, ConnectionError, OSError, HTTPException) as exc:
        return Resp(0, {}, "", str(getattr(exc, "reason", exc)))
    if raw[:2] == b"\x1f\x8b":
        try:
            raw = gzip.decompress(raw)
        except OSError:
            pass
    return Resp(status, rh, raw.decode("utf-8", "replace"))


def cookies(resp):
    jar = []
    getter = getattr(resp.headers, "get_all", None)
    for raw in (getter("Set-Cookie") or []) if getter else []:
        pair = raw.split(";", 1)[0].strip()
        if "=" in pair:
            jar.append(pair)
            add_secret(pair.split("=", 1)[1])
    return "; ".join(jar)


def basic_auth(user, password):
    token = base64.b64encode("{0}:{1}".format(user, password).encode()).decode()
    add_secret(token)
    return {"Authorization": "Basic " + token}


def mcp_messages(text):
    text = text or ""
    if "data:" in text and not text.lstrip().startswith(("{", "[")):
        out, data = [], []
        for line in text.splitlines() + [""]:
            if line == "":
                if data:
                    try:
                        out.append(json.loads("\n".join(data)))
                    except ValueError:
                        pass
                    data = []
            elif line.startswith("data:"):
                data.append(line[5:].strip())
        return out
    try:
        msg = json.loads(text)
    except ValueError:
        return []
    return msg if isinstance(msg, list) else [msg]


class MCP(object):
    """One Streamable HTTP session: initialize, tools/list, DELETE."""

    def __init__(self, url, token=None, timeout=30.0, headers=None):
        self.url, self.timeout, self.sid, self.proto = url, timeout, None, None
        self.headers = {"Accept": "application/json, text/event-stream"}
        self.headers.update(dict((str(k), str(v)) for k, v in (headers or {}).items()))
        if token is not None:
            self.headers["Authorization"] = "Bearer " + token

    def _post(self, payload, timeout=None):
        hdrs = dict(self.headers)
        if self.sid:
            hdrs["Mcp-Session-Id"] = self.sid
        if self.proto:
            hdrs["MCP-Protocol-Version"] = self.proto
        return http("POST", self.url, payload, hdrs, timeout=timeout or self.timeout)

    def initialize(self):
        resp = self._post({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                           "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                                      "clientInfo": {"name": "lab-acceptance", "version": "1"}}})
        if resp.status == 200:
            self.sid = resp.headers.get("mcp-session-id") or resp.headers.get("Mcp-Session-Id")
            result = next((m.get("result") for m in mcp_messages(resp.text)
                           if isinstance(m, dict) and m.get("id") == 1), None) or {}
            self.proto = result.get("protocolVersion")
            self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return resp

    def tools(self):
        """Every tool dict (follows nextCursor). Raises Fail with a one-line reason."""
        tools, cursor, rid = [], None, 2
        for _ in range(50):
            params = {"cursor": cursor} if cursor else {}
            resp = self._post({"jsonrpc": "2.0", "id": rid, "method": "tools/list", "params": params},
                              timeout=max(self.timeout, 45))
            if resp.status != 200:
                raise Fail("tools/list: {0}".format(resp.why()))
            msg = next((m for m in mcp_messages(resp.text) if isinstance(m, dict) and m.get("id") == rid), None)
            if not msg:
                raise Fail("tools/list returned no reply")
            if "error" in msg:
                raise Fail("tools/list error: {0}".format(one_line((msg["error"] or {}).get("message"), 100)))
            result = msg.get("result") or {}
            tools += [t for t in result.get("tools") or [] if isinstance(t, dict)]
            cursor = result.get("nextCursor")
            rid += 1
            if not cursor:
                break
        return tools

    def close(self):
        if self.sid:
            hdrs = dict(self.headers)
            hdrs["Mcp-Session-Id"] = self.sid
            http("DELETE", self.url, None, hdrs, timeout=5)


def mcp_tool_names(url, token=None, timeout=30.0, headers=None):
    session = MCP(url, token, timeout, headers)
    resp = session.initialize()
    if resp.status != 200:
        raise Fail("initialize: {0}".format(resp.why()))
    try:
        return [t.get("name", "") for t in session.tools()]
    finally:
        session.close()


def decode_list(value):
    for _ in range(4):
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                return []
        else:
            break
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def decode_obj(value):
    for _ in range(4):
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                return None
        else:
            break
    return value


def walk(obj):
    """Every dict inside obj (depth first)."""
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            yield cur
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)


def listing(items, limit=4):
    items = list(items)
    text = ", ".join(str(i) for i in items[:limit])
    return text + (" (+{0} more)".format(len(items) - limit) if len(items) > limit else "")


# =========================================================================== context
class Ctx(object):
    def __init__(self):
        self.started = time.time()
        self.model = env("LAB_MODEL_CALLS") == "on"
        self.model_reason = env("LAB_MODEL_REASON", "model calls are off")
        self.profile_aware = env("LAB_PROFILE_AWARE", "1") == "1"
        self.expected = self._lines("expected.txt")
        self.containers = {}
        for line in self._lines("containers.txt"):
            parts = line.split("|")
            if len(parts) < 9:
                continue
            svc, status, health, code, restarts, policy, name, nets, oom = parts[:9]
            self.containers.setdefault(svc, {
                "status": status, "health": health, "exit": int(code or 0) if code.lstrip("-").isdigit() else 0,
                "restarts": int(restarts) if restarts.isdigit() else 0, "policy": policy, "name": name.lstrip("/"),
                "networks": [n for n in nets.split(",") if n], "oom": oom == "true"})
        # restart policy each service is configured with (docker compose config); "no" = one-shot job
        self.restart = dict(line.split("|", 1) for line in self._lines("restart.txt") if "|" in line)
        self.flags = set(tuple(line.split("|", 1)) for line in self._lines("logflags.txt") if "|" in line)
        self.builder_env = [line.split("|") for line in self._lines("builder_env.txt")]
        self.trace_ids = []
        self.builder_runs = 0
        self.data = {}
        self._lf = None
        self._catalog = None
        self._sessions = {}

    @staticmethod
    def _lines(name):
        try:
            with open(os.path.join(FACTS, name), encoding="utf-8") as fh:
                return [l.rstrip("\n") for l in fh if l.strip()]
        except OSError:
            return []

    def fact_json(self, name):
        try:
            with open(os.path.join(FACTS, name), encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None

    # services --------------------------------------------------------------------------------
    def svc(self, name):
        return self.containers.get(name)

    def running(self, name):
        c = self.containers.get(name)
        return bool(c) and c["status"] == "running" and c["health"] != "unhealthy"

    def state(self, name):
        c = self.containers.get(name)
        if not c:
            return "never started" if name in self.expected or not self.expected else "not in this setup"
        if c["status"] == "restarting":
            return "crash loop, {0} restarts".format(c["restarts"])
        if c["status"] == "running":
            return "running ({0})".format(c["health"]) if c["health"] else "running"
        if c["status"] == "exited":
            return "stopped, exit {0}{1}".format(c["exit"], ", out of memory" if c["oom"] else "")
        return c["status"]

    def require(self, name):
        if self.running(name):
            return
        hint = "docker compose up -d {0}".format(name)
        if self.expected and name not in self.expected:
            hint = "{0} is in an opt-in profile ({1}): add it to COMPOSE_PROFILES in .env, then docker compose up -d".format(
                name, PROFILE_OF.get(name, "see docker-compose.yml"))
        elif name == "mcp-gateway" and not self.running("docker-socket-proxy"):
            hint = ("the gateway reads Docker through docker-socket-proxy ({0}): "
                    "docker compose up -d docker-socket-proxy mcp-gateway".format(self.state("docker-socket-proxy")))
        elif self.containers.get(name, {}).get("status") == "restarting":
            hint = "docker compose logs {0}".format(name)
        raise Fail("{0} is not running ({1})".format(name, self.state(name)), hint)

    def domain(self):
        domain = env("DOMAIN")
        host = env("N8N_HOST")
        if not domain and host.startswith("n8n.") and len(host) > 4:
            domain = host[4:]
        return domain

    # repository data -------------------------------------------------------------------------
    def lf(self):
        """scripts/flows/langflow_fix.py: SERVER_TOOLS, SIDECAR_URL, scope tables (stdlib module)."""
        if self._lf is None:
            sys.path.insert(0, os.path.join(LAB, "scripts", "flows"))
            try:
                import langflow_fix  # noqa: E402 - repository module, imported at run time
            except Exception as exc:  # noqa: BLE001
                raise Fail("cannot load SERVER_TOOLS from scripts/flows/langflow_fix.py ({0})".format(exc),
                           "mount the repository at /lab (run tests/acceptance/run.sh)")
            self._lf = langflow_fix
        return self._lf

    def catalog(self):
        """integrations/builders_agents.json as {slug: entry}."""
        if self._catalog is None:
            try:
                with open(os.path.join(LAB, "integrations", "builders_agents.json"), encoding="utf-8") as fh:
                    data = json.load(fh)
            except (OSError, ValueError) as exc:
                raise Fail("cannot read integrations/builders_agents.json ({0})".format(exc))
            self._catalog = {a["slug"]: a for a in data.get("agents", []) if isinstance(a, dict) and a.get("slug")}
        return self._catalog

    def current_slug(self, slug):
        """A labSeed marker slug mapped to the agent's current slug (catalog "former_slugs"): a flow kept
        after a rename still carries the slug it was seeded under."""
        for current, entry in self.catalog().items():
            if slug in (entry.get("former_slugs") or []):
                return current
        return slug

    def expected_tools(self, slug):
        """How many tools the agent <slug> binds, from the catalog (None = not an MCP agent)."""
        entry = self.catalog().get(slug) or {}
        if entry.get("kind") in ("gateway", "direct") and isinstance(entry.get("tools"), int):
            return entry["tools"]
        return None

    # sessions --------------------------------------------------------------------------------
    def n8n(self):
        if "n8n" in self._sessions:
            return self._sessions["n8n"]
        self.require("n8n")
        email, password = env("N8N_ADMIN_EMAIL"), env("N8N_ADMIN_PASSWORD")
        if not (email and password):
            raise Fail("N8N_ADMIN_EMAIL or N8N_ADMIN_PASSWORD is not set", "./setup.sh")
        resp = http("POST", N8N + "/rest/login", {"emailOrLdapLoginId": email, "password": password}, timeout=30)
        cookie = cookies(resp) if resp.status == 200 else ""
        if not cookie:
            raise Fail("n8n sign-in with N8N_ADMIN_EMAIL / N8N_ADMIN_PASSWORD: {0}".format(resp.why()),
                       "the n8n owner comes from n8n-provision: docker compose logs n8n-provision")
        self._sessions["n8n"] = {"Cookie": cookie}
        return self._sessions["n8n"]

    def flowise_base(self):
        return "http://flowise:{0}".format(env("FLOWISE_PORT", "3020"))

    def flowise(self):
        if "flowise" in self._sessions:
            return self._sessions["flowise"]
        self.require("flowise")
        if env("FLOWISE_API_KEY"):
            headers = {"Authorization": "Bearer " + env("FLOWISE_API_KEY")}
        else:
            resp = http("POST", self.flowise_base() + "/api/v1/auth/login",
                        {"email": env("N8N_ADMIN_EMAIL"), "password": env("N8N_ADMIN_PASSWORD")}, timeout=30)
            cookie = cookies(resp) if resp.status == 200 else ""
            if not cookie:
                raise Fail("Flowise sign-in with the lab admin (N8N_ADMIN_EMAIL): {0}".format(resp.why()),
                           "builders-import registers the admin on a new Flowise: docker compose run --rm builders-import")
            headers = {"Cookie": cookie, "x-request-from": "internal"}
        self._sessions["flowise"] = headers
        return headers

    def langflow(self):
        if "langflow" in self._sessions:
            return self._sessions["langflow"]
        self.require("langflow")
        if env("LANGFLOW_API_KEY"):
            headers = {"x-api-key": env("LANGFLOW_API_KEY")}
        else:
            resp = http("POST", LANGFLOW + "/api/v1/login",
                        {"username": env("N8N_ADMIN_EMAIL"), "password": env("N8N_ADMIN_PASSWORD")},
                        timeout=30, form=True)
            token = (resp.json() or {}).get("access_token") if resp.status == 200 else None
            if not token:
                raise Fail("Langflow sign-in with the lab admin (N8N_ADMIN_EMAIL): {0}".format(resp.why()),
                           "the superuser is set from N8N_ADMIN_* when Langflow first starts")
            add_secret(token)
            headers = {"Authorization": "Bearer " + token}
        self._sessions["langflow"] = headers
        return headers


def need_model(ctx, what):
    if not ctx.model:
        raise Skip("{0}: {1}".format(what, ctx.model_reason), "run with --with-model to make the model calls")


# =========================================================================== repository helpers
def repo_workflows():
    out = []
    for path in sorted(glob.glob(os.path.join(LAB, "n8n", "backup", "workflows", "*.json"))):
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        wf = json.loads(text)
        nodes = wf.get("nodes") or []
        trig = next((n for n in nodes if str(n.get("type", "")).endswith("chatTrigger")), None)
        params = (trig or {}).get("parameters") or {}
        out.append({
            "file": os.path.basename(path), "id": wf.get("id"), "name": wf.get("name"),
            "requires": (wf.get("meta") or {}).get("labRequires") or "",
            "domain_left": "{{DOMAIN}}" in text,
            "webhook": (trig or {}).get("webhookId"), "public": bool(params.get("public")),
            "basic": params.get("authentication") == "basicAuth",
            "mcp": [n.get("name") for n in nodes if str(n.get("type", "")).endswith("mcpClientTool")],
        })
    return out


def repo_credentials():
    out = []
    for path in sorted(glob.glob(os.path.join(LAB, "n8n", "backup", "credentials_public", "*.json"))):
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        for c in data if isinstance(data, list) else [data]:
            if isinstance(c, dict):
                out.append({"id": c.get("id"), "name": c.get("name")})
    return out


def repo_slugs(builder):
    pattern = {"flowise": "*.flowdata.json", "langflow": "*.flow.json"}[builder]
    suffix = pattern[1:]
    return sorted(os.path.basename(p)[:-len(suffix)]
                  for p in glob.glob(os.path.join(LAB, "integrations", builder, pattern)))


def n8n_unmet(ctx, wf):
    """Mirrors scripts/n8n-provision.sh: what keeps a workflow from being published."""
    need = []
    for req in wf["requires"].split():
        if req.startswith("service:"):
            host = req.split(":")[1]
            if not ctx.running(host):
                need.append("{0} running".format(host))
        elif "=" in req:
            name, want = req.split("=", 1)
            if env(name) != want:
                need.append(req)
        elif req == "DOMAIN":
            if not ctx.domain():
                need.append("DOMAIN")
        elif not is_set(req):
            need.append(req)
    if wf["domain_left"] and not ctx.domain() and "DOMAIN" not in need:
        need.insert(0, "DOMAIN")
    return need


def n8n_live_workflows(ctx):
    hdrs = ctx.n8n()
    resp = http("GET", N8N + "/rest/workflows", headers=hdrs, timeout=90)
    data = resp.json()
    rows = data.get("data") if isinstance(data, dict) else data
    if isinstance(rows, dict):
        rows = rows.get("results") or rows.get("data")
    if resp.status != 200 or not isinstance(rows, list):
        raise Fail("cannot list the n8n workflows ({0})".format(resp.why()))
    return {r.get("id"): r for r in rows if isinstance(r, dict)}


def n8n_active(row):
    return bool(row.get("active")) or bool(row.get("activeVersionId"))


# =========================================================================== checks
def check_stack(ctx):
    if not ctx.expected:
        raise Fail("cannot read the services of this lab (docker compose config --services failed)",
                   "docker compose config --quiet shows the error")
    bad, notes, healthy = [], [], 0
    for svc in ctx.expected:
        c = ctx.svc(svc)
        if not c:
            bad.append((5, svc, "never started"))
            continue
        oneshot = ctx.restart.get(svc, c["policy"]).strip("'\"") in ("no", "")
        status, health, code = c["status"], c["health"], c["exit"]
        if status == "restarting":
            bad.append((0, svc, "crash loop ({0} restarts)".format(c["restarts"])))
        elif status == "running":
            if oneshot and not health:
                bad.append((3, svc, "one-shot job still running"))
            elif health == "unhealthy":
                bad.append((1, svc, "unhealthy"))
            elif health == "starting":
                bad.append((2, svc, "still starting"))
            elif c["restarts"] > 5:
                bad.append((1, svc, "restarted {0} times".format(c["restarts"])))
            else:
                healthy += 1
        elif status == "exited":
            missing = [v for v in GATED.get(svc, ((), ""))[0] if not is_set(v)]
            if oneshot and code == 0:
                if (svc == "builders-import" and (svc, "langflow-skipped") in ctx.flags
                        and "langflow" in ctx.expected):
                    bad.append((3, svc, "ran while Langflow was down (Langflow agents not seeded)"))
                else:
                    healthy += 1
            elif oneshot:
                bad.append((2, svc, "one-shot job failed (exit {0})".format(code)))
            elif missing and code == 0:
                healthy += 1
                notes.append(svc)
            elif missing and code in (137, 143):
                bad.append((4, svc, "stopped (exit {0}, not configured)".format(code)))
            elif missing:
                bad.append((1, svc, "exited {0} while not configured (expected a clean stop, exit 0)".format(code)))
            else:
                bad.append((4, svc, "stopped (exit {0}{1})".format(code, ", out of memory" if c["oom"] else "")))
        else:
            bad.append((4, svc, status))
    ctx.data["STACK"] = {"healthy": healthy, "services": len(ctx.expected),
                         "problems": [{"service": s, "state": t} for _, s, t in sorted(bad)]}
    if bad:
        bad.sort()
        start = [s for _, s, t in bad if t.startswith(("stopped", "never started", "one-shot job failed"))]
        loops = [s for r, s, _ in bad if r <= 1]
        hints = []
        if "mcp-gateway" in loops + start and "docker-socket-proxy" in start:
            hints.append("the gateway needs docker-socket-proxy first")
        if start:
            hints.append("docker compose up -d " + " ".join(start[:6]) + (" ..." if len(start) > 6 else ""))
        if loops:
            hints.append("docker compose logs " + loops[0])
        raise Fail("{0} of {1} services are not healthy: {2}".format(
            len(bad), len(ctx.expected), "; ".join("{0} {1}".format(s, t) for _, s, t in bad[:4])
            + (" (+{0} more)".format(len(bad) - 4) if len(bad) > 4 else "")), "; ".join(hints))
    detail = "{0} of {0} services healthy (one-shot jobs exited 0)".format(len(ctx.expected))
    if notes:
        detail += "; {0} stopped cleanly as not configured".format(", ".join(notes))
    return detail


def check_gw_auth(ctx):
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                       "clientInfo": {"name": "lab-acceptance", "version": "1"}}}
    accept = {"Accept": "application/json, text/event-stream"}
    none = http("POST", GATEWAY_URL, init, accept)
    if none.status == 0:
        ctx.require("mcp-gateway")
        raise Fail("no answer from {0} ({1})".format(GATEWAY_URL, none.why()), "docker compose logs mcp-gateway")
    wrong = http("POST", GATEWAY_URL, init, dict(accept, Authorization="Bearer wrong-" + uuid.uuid4().hex))
    token = env("MCP_GATEWAY_TOKEN")
    if not token:
        raise Fail("MCP_GATEWAY_TOKEN is not set in .env", "./setup.sh generates it")
    session = MCP(GATEWAY_URL, token)
    right = session.initialize()
    session.close()
    problems = []
    if none.status != 401:
        problems.append("without a token: HTTP {0} (expected 401)".format(none.status))
    if wrong.status != 401:
        problems.append("with a wrong token: HTTP {0} (expected 401)".format(wrong.status))
    if right.status != 200:
        problems.append("with MCP_GATEWAY_TOKEN: {0} (expected 200)".format(right.why()))
    if problems:
        hint = ("the token in .env does not match the running gateway: docker compose up -d mcp-gateway"
                if right.status == 401 else "the gateway must run with MCP_GATEWAY_AUTH_TOKEN (docker-compose.yml)")
        raise Fail("; ".join(problems), hint)
    return "HTTP 401 without a token and with a wrong token, 200 with MCP_GATEWAY_TOKEN"


def check_gw_tools(ctx):
    lf = ctx.lf()
    token = env("MCP_GATEWAY_TOKEN")
    if not token:
        raise Fail("MCP_GATEWAY_TOKEN is not set in .env", "./setup.sh generates it")
    try:
        names = mcp_tool_names(GATEWAY_URL, token, timeout=45)
    except Fail as exc:
        if not ctx.running("mcp-gateway"):
            ctx.require("mcp-gateway")
        raise Fail("gateway {0}".format(exc.reason), "docker compose logs mcp-gateway")
    per = dict((s, 0) for s in lf.SERVER_TOOLS)
    unknown = []
    for name in names:
        owner = lf.TOOL_OWNER.get(name)
        if owner:
            per[owner] += 1
        else:
            unknown.append(name)
    promised = sum(len(v) for v in lf.SERVER_TOOLS.values())
    ctx.data["GW-TOOLS"] = {"total": len(names), "per_server": per, "unknown": unknown}
    problems = ["{0} {1}/{2}".format(s, per[s], len(t)) for s, t in lf.SERVER_TOOLS.items() if per[s] != len(t)]
    if unknown:
        problems.append("{0} tools not in SERVER_TOOLS ({1})".format(len(unknown), listing(unknown, 3)))
    if len(names) != len(set(names)):
        problems.append("{0} duplicate tool names".format(len(names) - len(set(names))))
    if promised != EXPECTED_GATEWAY_TOOLS:
        problems.append("SERVER_TOOLS lists {0} tools, the lab promises {1}".format(promised, EXPECTED_GATEWAY_TOOLS))
    if problems:
        empty = [s for s in per if per[s] == 0]
        hint = ("a server was not ready when the gateway started: docker compose restart mcp-gateway" if empty
                else "a server's tools changed: update SERVER_TOOLS in scripts/flows/langflow_fix.py, then snapshot + apply")
        raise Fail("{0} tools; per server: {1}".format(len(names), "; ".join(problems)), hint)
    return "{0} tools; all {1} servers match SERVER_TOOLS".format(len(names), len(per))


def check_direct(ctx):
    lf = ctx.lf()
    fails, ok, total, notes = [], 0, 0, []
    for server, url in lf.SIDECAR_URL.items():
        host = urllib.parse.urlsplit(url).hostname
        if not ctx.running(host):
            fails.append("{0} ({1})".format(host, ctx.state(host)))
            continue
        try:
            names = mcp_tool_names(url, timeout=30)
        except Fail as exc:
            fails.append("{0}: {1}".format(host, exc.reason))
            continue
        want = set(lf.SERVER_TOOLS[server])
        if set(names) != want:
            fails.append("{0}: {1} tools, SERVER_TOOLS has {2} (missing {3}; new {4})".format(
                host, len(names), len(want), listing(sorted(want - set(names)), 2) or "none",
                listing(sorted(set(names) - want), 2) or "none"))
            continue
        ok += 1
        total += len(names)
    for svc, (variables, url) in sorted(GATED.items()):
        missing = [v for v in variables if not is_set(v)]
        if missing:
            notes.append("{0} not configured".format(svc))
            continue
        if not ctx.running(svc):
            fails.append("{0} is configured but {1}".format(svc, ctx.state(svc)))
            continue
        try:
            names = mcp_tool_names(url, timeout=30)
        except Fail:
            try:
                names = mcp_tool_names(url + "/mcp", timeout=30)
            except Fail as exc:
                fails.append("{0}: {1}".format(svc, exc.reason))
                continue
        if not names:
            fails.append("{0}: 0 tools".format(svc))
        else:
            notes.append("{0} {1} tools".format(svc, len(names)))
    for svc, url in DIRECT_ONLY.items():
        if ctx.running(svc):
            try:
                notes.append("{0} {1} tools".format(svc, len(mcp_tool_names(url, timeout=30))))
            except Fail as exc:
                fails.append("{0}: {1}".format(svc, exc.reason))
    ctx.data["DIRECT"] = {"servers_ok": ok, "tools": total, "notes": notes, "problems": fails}
    if fails:
        raise Fail("{0} of {1} direct servers fail: {2}".format(len(fails), len(lf.SIDECAR_URL), "; ".join(fails[:4])
                                                               + (" (+{0} more)".format(len(fails) - 4) if len(fails) > 4 else "")),
                   "docker compose up -d <server>; docker compose logs <server>")
    return "{0} servers answer tools/list with their SERVER_TOOLS ({1} tools){2}".format(
        ok, total, "; " + ", ".join(notes) if notes else "")


def litellm_chat(key, body, timeout):
    return http("POST", LITELLM + "/v1/chat/completions", body, {"Authorization": "Bearer " + key}, timeout=timeout)


def model_error(resp):
    data = resp.json() or {}
    err = data.get("error") if isinstance(data, dict) else None
    msg = err.get("message") if isinstance(err, dict) else (err or "")
    return "{0} {1}".format(resp.why(), one_line(msg, 160)).strip()


def check_litellm(ctx):
    ctx.require("litellm")
    key = env("LITELLM_MASTER_KEY")
    if not key:
        raise Fail("LITELLM_MASTER_KEY is not set in .env", "./setup.sh generates it")
    no_key = http("GET", LITELLM + "/v1/models")
    wrong = http("GET", LITELLM + "/v1/models", headers={"Authorization": "Bearer sk-wrong-" + uuid.uuid4().hex})
    right = http("GET", LITELLM + "/v1/models", headers={"Authorization": "Bearer " + key})
    ids = [m.get("id") for m in ((right.json() or {}).get("data") or []) if isinstance(m, dict)]
    problems = []
    if no_key.status != 401:
        problems.append("without a key: {0} (expected 401)".format(no_key.why()))
    if wrong.status != 401:
        problems.append("with a wrong key: {0} (expected 401)".format(wrong.why()))
    if right.status != 200:
        problems.append("with LITELLM_MASTER_KEY: {0}".format(right.why()))
    elif "lab-chat" not in ids:
        problems.append("lab-chat is not listed (models: {0})".format(listing(ids, 3) or "none"))
    if problems:
        raise Fail("; ".join(problems), "docker compose logs litellm | grep lab-litellm")
    base = "lab-chat listed; HTTP 401 without a key and with a wrong key"
    if not ctx.model:
        return base + "; completion and tool call not run ({0})".format(ctx.model_reason)
    timeout = float(env("LAB_MODEL_TIMEOUT", "180"))
    trace = "lab-acceptance-" + uuid.uuid4().hex
    meta = {"trace_id": trace, "trace_name": "lab-acceptance", "tags": ["lab-acceptance"]}
    started = time.time()
    resp = litellm_chat(key, {"model": "lab-chat", "max_tokens": 300, "metadata": dict(meta, generation_name="chat"),
                              "messages": [{"role": "user", "content": "Reply with the single word OK."}]}, timeout)
    reply = resp.json() or {}
    if resp.status != 200 or not reply.get("choices"):
        raise Fail("lab-chat did not answer: {0}".format(model_error(resp)),
                   "check the provider key in .env, then docker compose up -d litellm")
    took = time.time() - started
    tool = {"type": "function", "function": {
        "name": "get_lab_status", "description": "Returns the status of the Check Point lab.",
        "parameters": {"type": "object", "properties": {"detail": {"type": "string"}}, "required": []}}}
    messages = [{"role": "system", "content": "You are a test agent. Call get_lab_status before you answer."},
                {"role": "user", "content": "What is the lab status? Use the tool."}]
    first = None
    for choice in ("required", "auto"):
        first = litellm_chat(key, {"model": "lab-chat", "messages": messages, "tools": [tool], "tool_choice": choice,
                                   "metadata": dict(meta, generation_name="tool-call")}, timeout)
        if first.status == 200:
            break
    msg = (((first.json() or {}).get("choices") or [{}])[0].get("message") or {}) if first else {}
    calls = msg.get("tool_calls") or []
    if first is None or first.status != 200 or not calls:
        raise Fail("lab-chat answered, but no tool call came back ({0})".format(
            model_error(first) if first is not None and first.status != 200 else "the model answered in text"),
            "agents need a model with tool calling: check LAB_MODEL_PROVIDER and the model name in .env")
    call = calls[0]
    messages.append({"role": "assistant", "content": msg.get("content"), "tool_calls": [{
        "id": call.get("id") or "call_1", "type": "function",
        "function": {"name": (call.get("function") or {}).get("name", "get_lab_status"),
                     "arguments": (call.get("function") or {}).get("arguments") or "{}"}}]})
    messages.append({"role": "tool", "tool_call_id": call.get("id") or "call_1", "content": '{"status": "ok"}'})
    second = litellm_chat(key, {"model": "lab-chat", "messages": messages, "tools": [tool],
                                "metadata": dict(meta, generation_name="tool-result")}, timeout)
    final = (((second.json() or {}).get("choices") or [{}])[0].get("message") or {}).get("content")
    if second.status != 200 or not final:
        raise Fail("tool round trip: the answer after the tool result failed ({0})".format(model_error(second)),
                   "docker compose logs litellm")
    ctx.trace_ids.append(trace)
    return base + "; completion in {0:.0f} s; tool call round trip OK".format(took)


PROVIDER_NAME = re.compile(r"^(OPENAI|AZURE_OPENAI|AZURE|ANTHROPIC|CLAUDE|GEMINI|GOOGLE|GOOGLE_GENERATIVE_AI|VERTEX|"
                           r"MISTRAL|COHERE|GROQ|DEEPSEEK|XAI|OPENROUTER|TOGETHER|HUGGINGFACE|HF|PERPLEXITY|FIREWORKS)"
                           r"(_[A-Z0-9]+)*_(API_KEY|KEY|TOKEN|SECRET)$")


def check_keys(ctx):
    inspected, offenders = [], []
    for parts in ctx.builder_env:
        if len(parts) < 4:
            continue
        svc, _cname, names, values = parts[:4]
        inspected.append(svc)
        named = [n for n in names.split(",") if n and PROVIDER_NAME.match(n)]
        valued = [n for n in values.split(",") if n]
        found = sorted(set(named) | set(valued))
        if found:
            offenders.append("{0} ({1})".format(svc, ", ".join(found)))
    mock = ctx.fact_json("provider-mock.json")
    mock_log = ctx._lines("provider-mock.log")
    ctx.data["KEYS"] = {"inspected": inspected, "offenders": offenders, "provider_mock": mock}
    if offenders:
        raise Fail("provider key variables in builder containers: {0}".format("; ".join(offenders)),
                   "only litellm may receive provider keys: remove them from those services in docker-compose.yml")
    if mock and mock.get("status") == "fail":
        detail = mock.get("detail") or "provider mock failed"
        if mock_log:
            detail += " (LiteLLM: {0})".format(one_line(mock_log[-1], 120))
        raise Fail("provider plumbing: " + detail, mock.get("hint") or "docker compose run --rm --no-deps litellm --check")
    if not inspected and not mock:
        raise Skip("no builder container to inspect yet and no provider mock run",
                   "start the lab, or run with --mock-provider")
    parts = []
    if inspected:
        parts.append("no provider key in {0}".format(", ".join(inspected)))
    missing = [b for b in BUILDERS if b in ctx.expected and b not in inspected]
    if missing:
        parts.append("not created yet: {0}".format(", ".join(missing)))
    if mock and mock.get("status") == "pass":
        parts.append("provider mock: " + (mock.get("detail") or "key reached the provider"))
    elif mock and mock.get("status") == "skip":
        parts.append("provider mock skipped: " + (mock.get("detail") or ""))
    else:
        parts.append("provider plumbing not tested (add --mock-provider)")
    return "; ".join(parts)


def check_n8n_seed(ctx):
    wfs = repo_workflows()
    creds = repo_credentials()
    live = n8n_live_workflows(ctx)
    hdrs = ctx.n8n()
    resp = http("GET", N8N + "/rest/credentials", headers=hdrs, timeout=60)
    data = resp.json()
    rows = data.get("data") if isinstance(data, dict) else data
    live_creds = {r.get("id"): r for r in rows if isinstance(r, dict)} if isinstance(rows, list) else {}
    problems = []
    missing = [w["name"] for w in wfs if w["id"] not in live]
    if missing:
        problems.append("{0} workflows missing ({1})".format(len(missing), listing(missing, 2)))
    if resp.status != 200:
        problems.append("cannot list credentials ({0})".format(resp.why()))
    else:
        cmiss = [c["name"] for c in creds if c["id"] not in live_creds]
        if cmiss:
            problems.append("{0} credentials missing ({1})".format(len(cmiss), listing(cmiss, 2)))
    wrong_on, wrong_off, expected_on = [], [], 0
    for wf in wfs:
        row = live.get(wf["id"])
        if not row:
            continue
        need = n8n_unmet(ctx, wf)
        if need:
            if n8n_active(row):
                wrong_on.append("{0} (needs {1})".format(wf["name"], ", ".join(need)))
        else:
            expected_on += 1
            if not n8n_active(row):
                wrong_off.append(wf["name"])
    if wrong_off:
        problems.append("{0} not published ({1})".format(len(wrong_off), listing(wrong_off, 2)))
    if wrong_on:
        problems.append("{0} published without their prerequisites ({1})".format(len(wrong_on), listing(wrong_on, 2)))
    trig_bad, trig_ok = [], 0
    admin = basic_auth(env("N8N_ADMIN_EMAIL"), env("N8N_ADMIN_PASSWORD"))
    for wf in wfs:
        row = live.get(wf["id"])
        if not (wf["webhook"] and wf["public"] and row and n8n_active(row)):
            continue
        url = "{0}/webhook/{1}/chat".format(N8N, wf["webhook"])
        anon = http("GET", url, timeout=20)
        auth = http("GET", url, headers=admin, timeout=20)
        if anon.status != 401 or auth.status != 200:
            trig_bad.append("{0} (no auth {1}, lab admin {2})".format(wf["name"], anon.status, auth.status))
        else:
            trig_ok += 1
    if trig_bad:
        problems.append("{0} chat triggers wrong ({1})".format(len(trig_bad), listing(trig_bad, 2)))
    ctx.data["N8N-SEED"] = {"workflows": len(live), "expected_workflows": len(wfs), "credentials": len(live_creds),
                            "expected_credentials": len(creds), "expected_published": expected_on,
                            "triggers_ok": trig_ok, "problems": problems}
    if problems:
        raise Fail("; ".join(problems), "docker compose run --rm n8n-import (edited workflows are kept unless "
                                        "N8N_SEED_OVERWRITE=1); docker compose logs n8n-import")
    return ("{0}/{0} workflows, {1}/{1} credentials, {2} published as expected, {3} chat triggers "
            "401 without auth / 200 with the lab admin").format(len(wfs), len(creds), expected_on, trig_ok)


def flatted_run_nodes(value):
    """Node names that ran, from n8n execution data (flatted string or plain object)."""
    if isinstance(value, dict):
        run = ((value.get("resultData") or {}).get("runData")) or {}
        return dict((k, len(v) if isinstance(v, list) else 1) for k, v in run.items())
    arr = json.loads(value)

    def deref(v):
        return arr[int(v)] if isinstance(v, str) and v.isdigit() and int(v) < len(arr) else v
    root = deref(arr[0]) if isinstance(arr, list) and arr else {}
    result = deref(root.get("resultData")) if isinstance(root, dict) else {}
    run = deref(result.get("runData")) if isinstance(result, dict) else {}
    out = {}
    for k, v in (run.items() if isinstance(run, dict) else []):
        runs = deref(v)
        out[k] = len(runs) if isinstance(runs, list) else 1
    return out


def n8n_executions(ctx, wf_id):
    query = urllib.parse.urlencode({"filter": json.dumps({"workflowId": wf_id}), "limit": "10"})
    resp = http("GET", N8N + "/rest/executions?" + query, headers=ctx.n8n(), timeout=30)
    data = resp.json() or {}
    data = data.get("data", data) if isinstance(data, dict) else data
    rows = data.get("results") if isinstance(data, dict) else data
    return [r for r in rows or [] if isinstance(r, dict)]


def check_n8n_run(ctx):
    need_model(ctx, "model calls are off")
    live = n8n_live_workflows(ctx)
    by_file = dict((w["file"], w) for w in repo_workflows())
    admin = basic_auth(env("N8N_ADMIN_EMAIL"), env("N8N_ADMIN_PASSWORD"))
    timeout = float(env("LAB_MODEL_TIMEOUT", "180")) + 60
    done, problems = [], []
    for kind, fname in (("gateway", "documentation-via-gateway.json"), ("direct", "documentation-mcp-agent.json")):
        wf = by_file.get(fname)
        row = live.get(wf["id"]) if wf else None
        if not wf or not row or not n8n_active(row):
            problems.append("{0} agent {1} is not published".format(kind, wf["name"] if wf else fname))
            continue
        before = max([int(r["id"]) for r in n8n_executions(ctx, wf["id"]) if str(r.get("id", "")).isdigit()] or [0])
        resp = http("POST", "{0}/webhook/{1}/chat".format(N8N, wf["webhook"]),
                    {"action": "sendMessage", "sessionId": "lab-acceptance-" + uuid.uuid4().hex, "chatInput": DOCS_PROMPT},
                    admin, timeout=timeout)
        answer = (resp.json() or {}).get("output") if isinstance(resp.json(), dict) else None
        if resp.status != 200 or not answer:
            problems.append("{0}: {1}".format(wf["name"], resp.why() if resp.status != 200 else "empty answer"))
            continue
        ran = {}
        for _ in range(15):
            newer = [r for r in n8n_executions(ctx, wf["id"]) if str(r.get("id", "")).isdigit() and int(r["id"]) > before]
            if newer:
                eid = max(int(r["id"]) for r in newer)
                detail = http("GET", "{0}/rest/executions/{1}".format(N8N, eid), headers=ctx.n8n(), timeout=30).json() or {}
                body = detail.get("data", detail) if isinstance(detail, dict) else {}
                try:
                    ran = flatted_run_nodes(body.get("data") if isinstance(body, dict) else {})
                except (ValueError, TypeError, KeyError, IndexError):
                    ran = {}
                if ran:
                    break
            time.sleep(2)
        used = [n for n in wf["mcp"] if ran.get(n)]
        if not used:
            problems.append("{0} answered without an MCP tool call in its execution".format(wf["name"]))
            continue
        done.append("{0} ({1} ran)".format(kind, used[0]))
        ctx.builder_runs += 1
    if problems:
        raise Fail("; ".join(problems), "open the execution in n8n > Executions; docker compose logs litellm")
    return "Documentation agents answered; MCP tool calls in the executions: " + ", ".join(done)


def flowise_flows(ctx):
    hdrs = ctx.flowise()
    seen = {}
    for query in ("", "?type=AGENTFLOW"):
        resp = http("GET", ctx.flowise_base() + "/api/v1/chatflows" + query, headers=hdrs, timeout=90)
        rows = resp.json()
        if isinstance(rows, dict):
            rows = rows.get("data")
        if resp.status == 200 and isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict) and row.get("id"):
                    seen[row["id"]] = row
        elif not query:
            raise Fail("cannot list the Flowise flows ({0})".format(resp.why()))
    seeded = {}
    for row in seen.values():
        graph = decode_obj(row.get("flowData")) or {}
        mark = graph.get("labSeed") if isinstance(graph, dict) else None
        if isinstance(mark, dict) and mark.get("slug"):
            seeded[ctx.current_slug(mark["slug"])] = (row, graph)
    return seen, seeded


def flowise_mcp_configs(graph):
    """(mcpServerConfig text, selected actions) for every Custom MCP tool in a Flowise graph."""
    out = []
    for node in graph.get("nodes") or []:
        data = node.get("data") or {}
        inputs = data.get("inputs") or {}
        if data.get("name") == "customMCP":
            out.append((inputs.get("mcpServerConfig"), decode_list(inputs.get("mcpActions"))))
        tools = decode_obj(inputs.get("agentTools"))
        for tool in tools if isinstance(tools, list) else []:
            if isinstance(tool, dict) and tool.get("agentSelectedTool") == "customMCP":
                cfg = tool.get("agentSelectedToolConfig") or {}
                out.append((cfg.get("mcpServerConfig"), decode_list(cfg.get("mcpActions"))))
    return out


def check_flowise_seed(ctx):
    expected = repo_slugs("flowise")
    _, seeded = flowise_flows(ctx)
    base, hdrs = ctx.flowise_base(), ctx.flowise()
    problems = []
    missing = [s for s in expected if s not in seeded]
    if missing:
        problems.append("{0} of {1} agents not seeded ({2})".format(len(missing), len(expected), listing(missing, 3)))
    resp = http("GET", base + "/api/v1/credentials", headers=hdrs, timeout=30)
    names = [r.get("name") for r in resp.json() or [] if isinstance(r, dict)] if isinstance(resp.json(), list) else []
    if MODEL_CREDENTIAL not in names:
        problems.append("credential '{0}' missing ({1})".format(MODEL_CREDENTIAL, resp.why() if resp.status != 200 else "not created"))
    resp = http("GET", base + "/api/v1/variables", headers=hdrs, timeout=30)
    variables = {}
    for row in resp.json() or [] if isinstance(resp.json(), list) else []:
        if isinstance(row, dict) and row.get("name"):
            variables[row["name"]] = row.get("value") or ""
            add_secret(row.get("value") or "")
    checked, skipped = 0, []
    for slug in sorted(seeded):
        entry = ctx.catalog().get(slug) or {}
        want = ctx.expected_tools(slug)
        if want is None:
            continue
        if entry.get("profile") == "security-lab" and not ctx.running("vuln-mcp"):
            skipped.append(slug)
            continue
        selected, live, failed = set(), set(), None
        for cfg_text, actions in flowise_mcp_configs(seeded[slug][1]):
            if not isinstance(cfg_text, str):
                continue
            cfg_text = re.sub(r"\{\{\s*\$vars\.([A-Za-z0-9_]+)\s*\}\}", lambda m: variables.get(m.group(1), ""), cfg_text)
            body = {"loadMethod": "listActions", "name": "customMCP", "inputs": {"mcpServerConfig": cfg_text},
                    "inputParams": []}
            resp = http("POST", base + "/api/v1/node-load-method/customMCP", body, hdrs, timeout=60)
            rows = resp.json()
            if resp.status != 200 or not isinstance(rows, list):
                failed = "listActions {0}".format(resp.why())
                break
            live |= set(r.get("name") for r in rows if isinstance(r, dict) and r.get("name") != "error")
            selected |= set(actions)
        checked += 1
        if failed:
            problems.append("{0}: {1}".format(slug, failed))
        elif not live:
            problems.append("{0}: its MCP server lists 0 tools".format(slug))
        elif len(selected) != want or not selected <= live:
            problems.append("{0}: {1} tools selected, catalog {2}{3}".format(
                slug, len(selected), want, ", {0} not on the server".format(len(selected - live)) if selected - live else ""))
    ctx.data["FLOWISE-SEED"] = {"seeded": sorted(seeded), "expected": expected, "tool_checks": checked,
                                "skipped": skipped, "problems": problems}
    if problems:
        raise Fail("; ".join(problems[:4]) + (" (+{0} more)".format(len(problems) - 4) if len(problems) > 4 else ""),
                   "docker compose run --rm builders-import (changed agents are kept unless SEED_OVERWRITE=1)")
    return "{0}/{0} agents seeded, credential '{1}' present, tool counts match the catalog for {2} MCP agents{3}".format(
        len(expected), MODEL_CREDENTIAL, checked, " ({0} skipped: profile off)".format(len(skipped)) if skipped else "")


def check_flowise_run(ctx):
    need_model(ctx, "model calls are off")
    _, seeded = flowise_flows(ctx)
    base, hdrs = ctx.flowise_base(), ctx.flowise()
    resp = http("GET", base + "/api/v1/apikey", headers=hdrs, timeout=30)
    rows = resp.json()
    rows = rows.get("data") if isinstance(rows, dict) else rows
    key = next((r.get("apiKey") for r in rows or [] if isinstance(r, dict) and r.get("keyName") == LAB_KEY_NAME), None)
    if not key or "*" in key:
        raise Fail("Flowise API key '{0}' not found or not readable ({1})".format(LAB_KEY_NAME, resp.why()),
                   "docker compose run --rm builders-import creates it")
    add_secret(key)
    done, problems = [], []
    for kind, slug in (("gateway", "documentation"), ("direct", "direct-documentation")):
        if slug not in seeded:
            problems.append("{0} agent {1} is not seeded".format(kind, slug))
            continue
        flow_id = seeded[slug][0]["id"]
        url = "{0}/api/v1/prediction/{1}".format(base, flow_id)
        body = {"question": DOCS_PROMPT, "overrideConfig": {"sessionId": "lab-acceptance-" + uuid.uuid4().hex}}
        anon = http("POST", url, body, timeout=60)
        if anon.status == 200:
            problems.append("{0}: the prediction API answers without the '{1}' key".format(slug, LAB_KEY_NAME))
        resp = http("POST", url, body, {"Authorization": "Bearer " + key},
                    timeout=float(env("LAB_MODEL_TIMEOUT", "180")) + 60)
        data = resp.json()
        if resp.status != 200 or not isinstance(data, dict):
            problems.append("{0}: {1}".format(slug, model_error(resp)))
            continue
        used = [d for d in walk(data) if isinstance(d.get("usedTools"), list) and d["usedTools"]]
        if not used:
            problems.append("{0} answered without using a tool (usedTools empty)".format(slug))
            continue
        tool = (used[0]["usedTools"][0] or {}).get("tool") if isinstance(used[0]["usedTools"][0], dict) else None
        done.append("{0} ({1})".format(kind, tool or "tool used"))
        ctx.builder_runs += 1
    if problems:
        raise Fail("; ".join(problems), "open the agent in Flowise and send the prompt; docker compose logs flowise")
    return "prediction API refused without the '{0}' key; Documentation agents used tools: {1}".format(
        LAB_KEY_NAME, ", ".join(done))


def langflow_flows(ctx, full=True):
    hdrs = ctx.langflow()
    resp = http("GET", LANGFLOW + "/api/v1/flows/?get_all=true&remove_example_flows=true", headers=hdrs, timeout=180)
    rows = resp.json()
    if isinstance(rows, dict):
        rows = rows.get("items") or rows.get("flows")
    if resp.status != 200 or not isinstance(rows, list):
        raise Fail("cannot list the Langflow flows ({0})".format(resp.why()))
    seeded = {}
    for row in rows:
        data = row.get("data") if isinstance(row, dict) else None
        mark = data.get("labSeed") if isinstance(data, dict) else None
        if isinstance(mark, dict) and mark.get("slug"):
            seeded[ctx.current_slug(mark["slug"])] = row
    return rows, seeded


def langflow_mcp_nodes(flow):
    for node in ((flow.get("data") or {}).get("nodes") or []):
        data = node.get("data") or {}
        if data.get("type") == "MCPTools":
            yield node.get("id"), data.get("node") or {}


def check_langflow_seed(ctx):
    expected = repo_slugs("langflow")
    _, seeded = langflow_flows(ctx)
    hdrs = ctx.langflow()
    problems = []
    missing = [s for s in expected if s not in seeded]
    if missing:
        problems.append("{0} of {1} agents not seeded ({2})".format(len(missing), len(expected), listing(missing, 3)))
    resp = http("GET", LANGFLOW + "/api/v1/variables/", headers=hdrs, timeout=30)
    names = [r.get("name") for r in resp.json() or [] if isinstance(r, dict)] if isinstance(resp.json(), list) else []
    if "LITELLM_MASTER_KEY" not in names:
        problems.append("global variable LITELLM_MASTER_KEY missing ({0})".format(
            resp.why() if resp.status != 200 else "not created"))
    checked, skipped = 0, []
    for slug in sorted(seeded):
        entry = ctx.catalog().get(slug) or {}
        want = ctx.expected_tools(slug)
        if want is None:
            continue
        if entry.get("profile") == "security-lab" and not ctx.running("vuln-mcp"):
            skipped.append(slug)
            continue
        enabled, live, failed = set(), set(), None
        for _nid, node in langflow_mcp_nodes(seeded[slug]):
            template = node.get("template") or {}
            # Langflow renders the node in tool mode: it connects to the node's MCP server and lists
            # the tools the node binds (POST /api/v1/custom_component/update, as langflow_fix.py does).
            body = {"code": (template.get("code") or {}).get("value"), "template": template, "field": "tool_mode",
                    "field_value": True, "tool_mode": True}
            resp = http("POST", LANGFLOW + "/api/v1/custom_component/update", body, hdrs, timeout=120)
            rendered = resp.json()
            if resp.status != 200 or not isinstance(rendered, dict):
                failed = "MCPTools render {0}".format(resp.why())
                break
            meta = (rendered.get("template") or {}).get("tools_metadata")
            rows = meta.get("value") if isinstance(meta, dict) else None
            if isinstance(rows, list) and rows:
                names_live = set(r.get("name") for r in rows if isinstance(r, dict) and r.get("name"))
            else:
                # No tool list in the rendered node: list the node's own MCP server (same URL and headers).
                server = ((template.get("mcp_server") or {}).get("value") or {}).get("config") or {}
                try:
                    names_live = set(mcp_tool_names(server.get("url") or "", timeout=45,
                                                    headers=server.get("headers") or {}))
                except Fail as exc:
                    failed = "its MCP server ({0}): {1}".format(server.get("url"), exc.reason)
                    break
            live |= names_live
            own = (template.get("tools_metadata") or {}).get("value")
            if isinstance(own, list) and own:
                enabled |= set(r.get("name") for r in own if isinstance(r, dict) and r.get("status"))
            else:
                enabled |= names_live
        checked += 1
        if failed:
            problems.append("{0}: {1}".format(slug, failed))
        elif not live:
            problems.append("{0}: its MCP server lists 0 tools".format(slug))
        elif len(enabled & live) != want:
            problems.append("{0}: {1} tools bound, catalog {2}{3}".format(
                slug, len(enabled & live), want,
                ", {0} not on the server".format(len(enabled - live)) if enabled - live else ""))
    ctx.data["LANGFLOW-SEED"] = {"seeded": sorted(seeded), "expected": expected, "tool_checks": checked,
                                 "skipped": skipped, "problems": problems}
    if problems:
        raise Fail("; ".join(problems[:4]) + (" (+{0} more)".format(len(problems) - 4) if len(problems) > 4 else ""),
                   "docker compose run --rm builders-import (changed flows are kept unless SEED_OVERWRITE=1)")
    return "{0}/{0} agents seeded, variable LITELLM_MASTER_KEY present, MCPTools tool counts match the catalog " \
           "for {1} MCP agents{2}".format(len(expected), checked,
                                          " ({0} skipped: profile off)".format(len(skipped)) if skipped else "")


def langflow_api_key(ctx):
    """Headers for /api/v1/run, which takes an API key only: LANGFLOW_API_KEY, else a temporary key
    that check_langflow_run deletes again. Returns (headers, temporary key id or None)."""
    if env("LANGFLOW_API_KEY"):
        return {"x-api-key": env("LANGFLOW_API_KEY")}, None
    resp = http("POST", LANGFLOW + "/api/v1/api_key/", {"name": "lab-acceptance-" + uuid.uuid4().hex[:8]},
                ctx.langflow(), timeout=30)
    data = resp.json() if isinstance(resp.json(), dict) else {}
    if resp.status not in (200, 201) or not data.get("api_key"):
        raise Fail("cannot create a temporary Langflow API key for /api/v1/run ({0})".format(resp.why()),
                   "set LANGFLOW_API_KEY in .env (Langflow > Settings > API Keys)")
    add_secret(data["api_key"])
    return {"x-api-key": data["api_key"]}, data.get("id")


def check_langflow_run(ctx):
    need_model(ctx, "model calls are off")
    _, seeded = langflow_flows(ctx)
    hdrs, temp_key = langflow_api_key(ctx)
    try:
        return langflow_run_agents(ctx, seeded, hdrs)
    finally:
        if temp_key:
            http("DELETE", "{0}/api/v1/api_key/{1}".format(LANGFLOW, temp_key), headers=ctx.langflow(), timeout=30)


def langflow_run_agents(ctx, seeded, hdrs):
    done, problems = [], []
    for kind, slug in (("gateway", "documentation"), ("direct", "direct-documentation")):
        row = seeded.get(slug)
        if not row:
            problems.append("{0} agent {1} is not seeded".format(kind, slug))
            continue
        body = {"input_value": DOCS_PROMPT, "input_type": "chat", "output_type": "chat",
                "session_id": "lab-acceptance-" + uuid.uuid4().hex}
        resp = http("POST", "{0}/api/v1/run/{1}?stream=false".format(LANGFLOW, row["id"]), body, hdrs,
                    timeout=float(env("LAB_MODEL_TIMEOUT", "180")) + 60)
        data = resp.json()
        if resp.status != 200 or not isinstance(data, dict):
            problems.append("{0}: {1}".format(slug, model_error(resp)))
            continue
        uses = [d for d in walk(data) if d.get("type") == "tool_use"]
        if not uses:
            problems.append("{0} answered without a tool call".format(slug))
            continue
        done.append("{0} ({1})".format(kind, uses[0].get("name") or "tool used"))
        ctx.builder_runs += 1
    if problems:
        raise Fail("; ".join(problems), "open the flow in Langflow > Playground; docker compose logs langflow")
    return "Documentation flows answered with tool calls: " + ", ".join(done)


def check_rag(ctx):
    ctx.require("qdrant")
    hdrs = {"api-key": env("QDRANT_API_KEY")} if env("QDRANT_API_KEY") else {}
    resp = http("GET", QDRANT + "/collections/cp_docs", headers=hdrs)
    result = (resp.json() or {}).get("result") or {} if resp.status == 200 else {}
    points = result.get("points_count") or 0
    vectors = ((result.get("config") or {}).get("params") or {}).get("vectors") or {}
    size = vectors.get("size") if isinstance(vectors, dict) else None
    if resp.status in (401, 403):
        raise Fail("Qdrant refused the request (HTTP {0}): QDRANT_API_KEY differs from the running Qdrant".format(resp.status),
                   "docker compose up -d qdrant")
    if not points:
        raise Fail("collection cp_docs is missing or empty ({0})".format(resp.why()), "docker compose up rag-ingest")
    if size != EMBED_DIM:
        raise Fail("cp_docs vectors have size {0}, {1} has {2}".format(size, EMBED_MODEL, EMBED_DIM),
                   "re-ingest with EMBED_MODEL=nomic-embed-text: docker compose up rag-ingest")
    ctx.require("ollama-cpu")
    emb = http("POST", OLLAMA + "/api/embeddings", {"model": EMBED_MODEL, "prompt": "Identity Awareness"}, timeout=120)
    vector = (emb.json() or {}).get("embedding") if emb.status == 200 else None
    if not vector or len(vector) != size:
        raise Fail("{0} embedding: {1}".format(EMBED_MODEL, emb.why() if emb.status != 200 else
                                                 "{0} values, the collection has {1}".format(len(vector or []), size)),
                   "docker compose up -d ollama-pull-models-cpu (pulls nomic-embed-text)")
    hits = http("POST", QDRANT + "/collections/cp_docs/points/search",
                {"vector": vector, "limit": 4, "with_payload": True, "score_threshold": 0.5}, hdrs)
    rows = (hits.json() or {}).get("result") or [] if hits.status == 200 else []
    sources = [((r.get("payload") or {}).get("source")) for r in rows if isinstance(r, dict)]
    sources = [s for s in sources if s]
    if not sources:
        raise Fail("search on cp_docs returned no hits with a source ({0})".format(hits.why()),
                   "docker compose up rag-ingest")
    base = "cp_docs: {0} points, {1}-dim ({2}); retriever returns {3} hits with sources ({4})".format(
        points, size, EMBED_MODEL, len(rows), listing(sorted(set(sources)), 2))
    if not ctx.model:
        return base + "; RAG agent not run ({0})".format(ctx.model_reason)
    wf = next((w for w in repo_workflows() if w["file"] == "rag-cp-docs-agent.json"), None)
    if not wf:
        raise Fail("n8n/backup/workflows/rag-cp-docs-agent.json not found")
    live = n8n_live_workflows(ctx)
    if not n8n_active(live.get(wf["id"]) or {}):
        raise Fail(base + "; but the n8n RAG agent is not published", "docker compose run --rm n8n-import")
    resp = http("POST", "{0}/webhook/{1}/chat".format(N8N, wf["webhook"]),
                {"action": "sendMessage", "sessionId": "lab-acceptance-" + uuid.uuid4().hex, "chatInput": RAG_PROMPT},
                basic_auth(env("N8N_ADMIN_EMAIL"), env("N8N_ADMIN_PASSWORD")),
                timeout=float(env("LAB_MODEL_TIMEOUT", "180")) + 60)
    answer = str((resp.json() or {}).get("output") or "") if isinstance(resp.json(), dict) else ""
    corpus = [os.path.basename(p) for p in glob.glob(os.path.join(LAB, "integrations", "rag-cp-docs", "corpus", "*.md"))]
    cited = [c for c in corpus if c in answer or c[:-3] in answer]
    if resp.status != 200 or not answer:
        raise Fail(base + "; RAG agent: {0}".format(resp.why() if resp.status != 200 else "empty answer"),
                   "docker compose logs n8n")
    if not cited:
        raise Fail(base + "; the RAG agent answered without citing a corpus document",
                   "the agent must call search_cp_docs (RAG retriever sub-workflow published?)")
    ctx.builder_runs += 1
    return base + "; RAG agent cites {0}".format(listing(cited, 2))


def check_code_agent(ctx):
    token = env("MCP_GATEWAY_TOKEN")
    if not token:
        raise Fail("MCP_GATEWAY_TOKEN is not set in .env", "./setup.sh generates it")
    folder = os.path.join(LAB, "integrations", "code-agent")
    sys.path.insert(0, folder)
    try:
        import mcp_gateway_client as mgc  # noqa: E402 - the trainee-facing client under test
    except Exception as exc:  # noqa: BLE001
        raise Fail("cannot import integrations/code-agent/mcp_gateway_client.py ({0})".format(exc))
    try:
        client = mgc.MCPGatewayClient(url=GATEWAY_URL, token=token, timeout=45)
        client.initialize()
        tools = client.list_tools()
        client.close()
    except (mgc.MCPError, SystemExit) as exc:
        if not ctx.running("mcp-gateway"):
            ctx.require("mcp-gateway")
        raise Fail("mcp_gateway_client.py: {0}".format(one_line(exc, 160)), "docker compose logs mcp-gateway")
    base = "mcp_gateway_client.py: handshake + tools/list OK ({0} tools)".format(len(tools))
    if not ctx.model:
        return base + "; agent_loop.py not run ({0})".format(ctx.model_reason)
    ctx.require("litellm")
    child = dict(os.environ)
    child.update({"GATEWAY_URL": GATEWAY_URL, "MCP_TOOLS": "reputation_ip", "MAX_TURNS": "4",
                  "LLM_TIMEOUT": env("LAB_MODEL_TIMEOUT", "180"), "PYTHONDONTWRITEBYTECODE": "1"})
    try:
        proc = subprocess.run([sys.executable, os.path.join(folder, "agent_loop.py")], env=child, cwd="/tmp",
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              timeout=float(env("LAB_MODEL_TIMEOUT", "180")) * 2)
    except subprocess.TimeoutExpired:
        raise Fail(base + "; agent_loop.py timed out", "docker compose logs litellm")
    out = proc.stdout.decode("utf-8", "replace")
    calls = len(re.findall(r"^-> model called ", out, re.M))
    if proc.returncode != 0 or "=== ANSWER ===" not in out:
        last = [l for l in out.splitlines() if l.strip()][-1:] or ["no output"]
        raise Fail(base + "; agent_loop.py exit {0}: {1}".format(proc.returncode, one_line(last[0], 120)),
                   "run it by hand: integrations/code-agent/README.md")
    if not calls:
        raise Fail(base + "; agent_loop.py answered without a tool call", "check that lab-chat supports tool calling")
    ctx.builder_runs += 1
    return base + "; agent_loop.py: {0} tool call(s), then an answer".format(calls)


def check_langfuse(ctx):
    ctx.require("langfuse")
    health = http("GET", LANGFUSE + "/api/public/health")
    if health.status != 200:
        raise Fail("Langfuse health: {0}".format(health.why()), "docker compose logs langfuse")
    pk, sk = env("LANGFUSE_PUBLIC_KEY"), env("LANGFUSE_SECRET_KEY")
    if not (pk and sk):
        raise Fail("LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY are not set in .env", "./setup.sh generates them")
    auth = basic_auth(pk, sk)
    projects = http("GET", LANGFUSE + "/api/public/projects", headers=auth)
    if projects.status != 200:
        raise Fail("Langfuse refused the project keys ({0})".format(projects.why()),
                   "the keys are set on the first Langfuse start (LANGFUSE_INIT_PROJECT_*)")
    if not ctx.model:
        raise Skip("Langfuse is up and accepts the project keys; no traces to look for ({0})".format(ctx.model_reason),
                   "run with --with-model")
    if not ctx.trace_ids and not ctx.builder_runs:
        raise Skip("Langfuse is up and accepts the project keys; no model call succeeded in this run",
                   "fix the failing model checks first")
    deadline = time.time() + float(env("LAB_TRACE_WAIT", "90"))
    found, total = [], 0
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ctx.started - 5))
    while time.time() < deadline:
        found = [t for t in ctx.trace_ids if http("GET", LANGFUSE + "/api/public/traces/" + t, headers=auth).status == 200]
        listed = http("GET", LANGFUSE + "/api/public/traces?" + urllib.parse.urlencode(
            {"fromTimestamp": since, "limit": "100"}), headers=auth)
        meta = (listed.json() or {}).get("meta") or {}
        total = meta.get("totalItems") or len((listed.json() or {}).get("data") or [])
        if len(found) == len(ctx.trace_ids) and total >= len(ctx.trace_ids) + ctx.builder_runs:
            break
        time.sleep(5)
    want = len(ctx.trace_ids) + ctx.builder_runs
    if len(found) < len(ctx.trace_ids) or total < want:
        raise Fail("{0} traces since the run started, expected at least {1} (LITELLM trace {2})".format(
            total, want, "found" if found else "missing"),
            "tracing needs LANGFUSE_PUBLIC_KEY/SECRET_KEY on litellm: docker compose logs litellm | grep lab-litellm")
    return "{0} traces since the run started (LITELLM check trace found; {1} builder runs traced)".format(
        total, ctx.builder_runs)


def check_openwebui(ctx):
    ctx.require("open-webui")
    cfg = http("GET", OPEN_WEBUI + "/api/config")
    features = (cfg.json() or {}).get("features") or {} if cfg.status == 200 else {}
    problems = []
    if cfg.status != 200:
        problems.append("config {0}".format(cfg.why()))
    elif features.get("enable_signup") is not False:
        problems.append("sign-up is open (enable_signup={0})".format(features.get("enable_signup")))
    email = env("OPEN_WEBUI_ADMIN_EMAIL") or env("N8N_ADMIN_EMAIL")
    password = env("OPEN_WEBUI_ADMIN_PASSWORD") or env("N8N_ADMIN_PASSWORD")
    signin = http("POST", OPEN_WEBUI + "/api/v1/auths/signin", {"email": email, "password": password}, timeout=30)
    data = signin.json() or {}
    token = data.get("token") if signin.status == 200 else None
    if not token:
        raise Fail("; ".join(problems + ["admin sign-in: {0}".format(signin.why())]),
                   "openwebui-provision creates the admin: docker compose logs openwebui-provision")
    add_secret(token)
    if data.get("role") != "admin":
        problems.append("the lab admin has role {0}".format(data.get("role")))
    model = env("OPEN_WEBUI_DEFAULT_MODELS", "qwen3.5:4b").split(",")[0].strip()
    models = http("GET", OPEN_WEBUI + "/api/models", headers={"Authorization": "Bearer " + token}, timeout=60)
    ids = [m.get("id") for m in ((models.json() or {}).get("data") or []) if isinstance(m, dict)]
    if model not in ids:
        problems.append("default model {0} not listed ({1})".format(model, listing(ids, 3) or "no models"))
    if problems:
        raise Fail("; ".join(problems), "docker compose up -d ollama-pull-chat-models open-webui")
    return "admin sign-in OK, sign-up closed, default model {0} listed".format(model)


def check_seclab(ctx):
    c = ctx.svc("vuln-mcp")
    ctx.require("vuln-mcp")
    problems = []
    if c["health"] != "healthy":
        problems.append("vuln-mcp health is '{0}'".format(c["health"] or "none"))
    others = [n for n in c["networks"] if not n.endswith("security-lab")]
    if others:
        problems.append("vuln-mcp is also on {0} (must be on the isolated security-lab network only)".format(", ".join(others)))
    try:                             # run.sh joins this container to the security-lab network
        socket.getaddrinfo("vuln-mcp", 3099)
    except OSError:
        raise Fail("; ".join(problems + ["the test container is not on the security-lab network ({0})".format(
            env("LAB_SECLAB_NETWORK", "not found"))]), "docker network ls | grep security-lab")
    health = http("GET", VULN_MCP + "/health")
    if health.status != 200:
        raise Fail("; ".join(problems + ["vuln-mcp /health {0}".format(health.why())]), "docker compose logs vuln-mcp")
    rug = (health.json() or {}).get("rug_pull")
    session = MCP(VULN_MCP + "/mcp")
    init = session.initialize()
    if init.status != 200:
        raise Fail("vuln-mcp initialize {0}".format(init.why()), "docker compose logs vuln-mcp")
    tools = session.tools()          # tools/list only: no tool is called, so the rug pull stays disarmed
    session.close()
    poisoned = [t.get("name") for t in tools if "<IMPORTANT>" in str(t.get("description"))
                and "SIMULATED" in str(t.get("description"))]
    if len(tools) < 4:
        problems.append("{0} tools listed (expected 4)".format(len(tools)))
    if not poisoned:
        problems.append("no simulated tool-poisoning payload in the tool descriptions")
    try:
        wf = next((w for w in repo_workflows() if w["file"] == "mcp-security-lab-agent.json"), None)
        live = n8n_live_workflows(ctx)
        if not wf or not n8n_active(live.get(wf["id"]) or {}):
            problems.append("the n8n Security Lab agent is not published")
    except Fail as exc:
        problems.append("cannot confirm the n8n Security Lab agent: {0}".format(exc.reason))
    if problems:
        raise Fail("; ".join(problems), "docker compose run --rm n8n-import (publishes it once vuln-mcp answers)")
    return "vuln-mcp healthy on the isolated security-lab network, {0} tools, simulated payload in {1}, " \
           "rug pull {2}, n8n Security Lab agent published".format(len(tools), listing(poisoned, 1), rug or "unknown")


def check_aig(ctx):
    ctx.require("aig-webserver")
    problems = []
    web = ctx.svc("aig-webserver")
    if web["health"] not in ("healthy", ""):
        problems.append("aig-webserver health '{0}'".format(web["health"]))
    page = http("GET", AIG + "/", timeout=20)
    if page.status != 200:
        problems.append("web UI {0}".format(page.why()))
    if not ctx.running("aig-agent"):
        problems.append("aig-agent is not running ({0})".format(ctx.state("aig-agent")))
    elif ctx.svc("aig-agent")["restarts"] > 3:
        problems.append("aig-agent restarted {0} times".format(ctx.svc("aig-agent")["restarts"]))
    if problems:
        raise Fail("; ".join(problems), "docker compose logs aig-webserver aig-agent")
    seen = ("aig-agent", "connected") in ctx.flags
    detail = "web UI answers, aig-agent running ({0})".format(
        "its log shows the server connection" if seen else "registration not visible in its log")
    if env("LAB_WITH_SCAN") == "1":
        detail += "; scan not started: the scan test is owner-supervised (docs/guides/MCP_Security_Lab.md)"
    return detail


def check_exercises(ctx):
    ctx.require("ips-cve-mcp")
    names = mcp_tool_names(IPS_CVE, timeout=30)
    if not names:
        raise Fail("ips-cve-mcp lists 0 tools", "docker compose logs ips-cve-mcp")
    return "ips-cve-mcp answers tools/list ({0} tools: {1})".format(len(names), listing(names, 3))


def check_evals(ctx):
    c = ctx.svc("evals-run")
    report = ctx.fact_json("evals_report.json")
    if not report:
        state = ctx.state("evals-run")
        raise Fail("no evals scorecard in n8n/shared/evals_report.json (evals-run: {0})".format(state),
                   "docker compose --profile evals run --rm evals-run")
    results = [r for r in report.get("results") or [] if isinstance(r, dict)]
    wiring = re.compile(r"(HTTP \d{3}|connection failed|unexpected:|timed out|timeout)", re.I)
    broken = [r.get("name") for r in results if not r.get("passed")
              and any(wiring.search(str(x)) for x in r.get("reasons") or [])]
    quality = [r.get("name") for r in results if not r.get("passed") and r.get("name") not in broken]
    detail = "scorecard {0}: {1}/{2} passed".format(report.get("generated_at", "?")[:19], report.get("passed", 0),
                                                     report.get("total", len(results)))
    if c and c["status"] == "exited" and c["exit"] not in (0, 1):
        broken.append("evals-run exit {0}".format(c["exit"]))
    if broken:
        raise Fail(detail + "; wiring failures: {0}".format(listing(broken, 3)),
                   "the agents must be published and reachable with the lab admin sign-in: docker compose logs evals-run")
    if quality:
        detail += "; model-quality misses (not a failure): {0}".format(listing(quality, 3))
    return detail


FUNCS = {
    "STACK": check_stack, "GW-AUTH": check_gw_auth, "GW-TOOLS": check_gw_tools, "DIRECT": check_direct,
    "LITELLM": check_litellm, "KEYS": check_keys, "N8N-SEED": check_n8n_seed, "N8N-RUN": check_n8n_run,
    "FLOWISE-SEED": check_flowise_seed, "FLOWISE-RUN": check_flowise_run, "LANGFLOW-SEED": check_langflow_seed,
    "LANGFLOW-RUN": check_langflow_run, "RAG": check_rag, "CODE-AGENT": check_code_agent,
    "LANGFUSE": check_langfuse, "OPENWEBUI": check_openwebui, "SECLAB": check_seclab, "AIG": check_aig,
    "EXERCISES": check_exercises, "EVALS": check_evals,
}


# =========================================================================== provider mock phase
def provider_mock_phase():
    """KEYS, provider half: runs in the mock provider container, next to a throwaway LiteLLM that
    shares its network namespace (no other network). Prints one JSON line, no secret values."""
    provider = env("LAB_MOCK_PROVIDER", "openai")
    key = env("LAB_MOCK_PROVIDER_KEY")
    master = env("LAB_MOCK_MASTER_KEY")
    source = env("LAB_MOCK_ORIGIN", "env")       # env = the key from .env, generated = a test key
    base = "http://127.0.0.1:4000"
    log = os.path.join(os.environ.get("MOCK_LOG_DIR", "/tmp/mock"), "requests.jsonl")

    def out(status, detail, hint=""):
        print(json.dumps({"status": status, "provider": provider, "key_source": source,
                          "detail": redact(detail), "hint": hint}))
        return 0

    def sha(value):
        return hashlib.sha256(value.encode()).hexdigest()

    def entries(marker):
        try:
            with open(log, encoding="utf-8") as fh:
                rows = [json.loads(l) for l in fh if l.strip()]
        except (OSError, ValueError):
            return []
        return [r for r in rows if marker in json.dumps(r.get("body"))]

    ready = False
    for _ in range(int(env("LAB_MOCK_WAIT", "5"))):
        if http("GET", base + "/health/liveliness", timeout=3).status == 200:
            ready = True
            break
        time.sleep(1)
    if not ready:
        return out("fail", "the throwaway LiteLLM is not answering", "docker compose run --rm --no-deps litellm --check")
    models = http("GET", base + "/v1/models", headers={"Authorization": "Bearer " + master})
    ids = [m.get("id") for m in ((models.json() or {}).get("data") or []) if isinstance(m, dict)]
    if models.status != 200 or "lab-chat" not in ids:
        return out("fail", "model list with the master key: {0}, lab-chat {1}".format(
            models.why(), "listed" if "lab-chat" in ids else "missing"))
    marker = "acceptance-" + uuid.uuid4().hex
    chat = litellm_like(base, master, {"model": "lab-chat", "messages": [{"role": "user", "content": "Say OK. " + marker}]})
    if chat.status != 200 or not (chat.json() or {}).get("choices"):
        return out("fail", "lab-chat through the mock: {0}".format(model_error(chat)))
    seen = entries(marker)
    if not seen:
        return out("fail", "LiteLLM answered but never called the {0} provider".format(provider))
    got = set(v for e in seen for v in (e.get("auth_sha"), e.get("api_key_sha"), e.get("x_api_key_sha"),
                                        e.get("goog_key_sha")) if v)
    if master and (sha(master) in got or sha("Bearer " + master) in got):
        return out("fail", "LiteLLM forwarded the LiteLLM master key to the provider",
                   "report this: integrations/litellm/render_config.py")
    if provider != "ollama":
        if not key:
            return out("fail", "no provider key to compare")
        if not got & {sha(key), sha("Bearer " + key)}:
            return out("fail", "the provider received a different key than the {0} key ({1})".format(
                provider, ".env" if source == "env" else "generated test key"),
                       "check the provider variables in .env, then docker compose up -d litellm")
    tool = {"type": "function", "function": {"name": "get_lab_status", "description": "Lab status.",
                                             "parameters": {"type": "object", "properties": {}, "required": []}}}
    messages = [{"role": "user", "content": "Lab status? " + marker}]
    first = litellm_like(base, master, {"model": "lab-chat", "messages": messages, "tools": [tool]})
    msg = (((first.json() or {}).get("choices") or [{}])[0].get("message") or {})
    calls = msg.get("tool_calls") or []
    if first.status != 200 or not calls:
        return out("fail", "tool call through LiteLLM: {0}".format(model_error(first) if first.status != 200
                                                                    else "no tool_calls in the reply"))
    messages += [{"role": "assistant", "content": None, "tool_calls": [{
        "id": calls[0].get("id") or "call_1", "type": "function",
        "function": {"name": "get_lab_status", "arguments": (calls[0].get("function") or {}).get("arguments") or "{}"}}]},
        {"role": "tool", "tool_call_id": calls[0].get("id") or "call_1", "content": '{"status": "ok"}'}]
    second = litellm_like(base, master, {"model": "lab-chat", "messages": messages, "tools": [tool]})
    final = (((second.json() or {}).get("choices") or [{}])[0].get("message") or {}).get("content")
    if second.status != 200 or not final:
        return out("fail", "tool result round trip: {0}".format(model_error(second)))
    if provider == "ollama":
        what = "requests (local Ollama, no key)"
    elif source == "env":
        what = "the {0} key from .env".format(provider)
    else:
        what = "a generated {0} test key (.env has no {0} key)".format(provider)
    return out("pass", "{0} reached the provider, the master key did not; tool call round trip OK".format(what))


def litellm_like(base, key, body):
    return http("POST", base + "/v1/chat/completions", body, {"Authorization": "Bearer " + key}, timeout=60)


# =========================================================================== runner
def run_one(ctx, cid, timeout):
    box = {}

    def target():
        try:
            box["r"] = ("PASS", FUNCS[cid](ctx) or "", "")
        except Skip as exc:
            box["r"] = ("SKIP", exc.reason, exc.hint)
        except Fail as exc:
            box["r"] = ("FAIL", exc.reason, exc.hint)
        except BaseException as exc:  # noqa: BLE001 - a broken check must not stop the suite
            box["r"] = ("FAIL", "the check itself failed: {0}: {1}".format(exc.__class__.__name__, exc),
                        "report this with the JSON result (tests/acceptance/acceptance.py)")
    worker = threading.Thread(target=target, name=cid)
    worker.daemon = True
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        return ("FAIL", "timed out after {0} s".format(timeout), "re-run with --only {0}; check the service logs".format(cid))
    return box.get("r", ("FAIL", "no result", ""))


def selected_ids(only, skip):
    ids = [c[0] for c in CHECKS]
    wanted = [i.strip().upper() for i in only.split(",") if i.strip()] if only else ids
    dropped = set(i.strip().upper() for i in skip.split(",") if i.strip()) if skip else set()
    unknown = [i for i in list(wanted) + list(dropped) if i not in ids]
    if unknown:
        raise SystemExit("unknown check id(s): {0}. Known: {1}".format(", ".join(unknown), " ".join(ids)))
    return [i for i in ids if i in wanted and i not in dropped]


def main():
    parser = argparse.ArgumentParser(description="Check Point AI agent lab acceptance tests")
    parser.add_argument("--phase", default="checks", choices=("checks", "provider-mock"))
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    init_secrets()
    if args.list:
        for cid, title, svc, _ in CHECKS:
            print("{0:<14} {1}{2}".format(cid, title, " (profile {0})".format(PROFILE_OF[svc]) if svc else ""))
        return 0
    if args.phase == "provider-mock":
        return provider_mock_phase()
    try:
        ids = selected_ids(env("LAB_ONLY"), env("LAB_SKIP"))
    except SystemExit as exc:
        print(redact(exc), file=sys.stderr)
        return 2
    ctx = Ctx()
    profiles = env("COMPOSE_PROFILES") or "(none: Standard lab)"
    print("Check Point AI agent lab: acceptance tests")
    print("  project {0}, network {1}, profiles {2}".format(env("LAB_PROJECT", "?"), env("LAB_NETWORK", "?"), profiles))
    print("  model calls: {0}".format("on" if ctx.model else "off ({0})".format(ctx.model_reason)))
    if not ctx.profile_aware:
        print("  profile-aware: off (checks of profiles that are off run and fail)")
    print("")
    print("  {0:<14} {1:<6} {2}".format("ID", "RESULT", "DETAIL"))
    print("  {0:<14} {1:<6} {2}".format("-" * 14, "-" * 6, "-" * 60))
    width = int(env("LAB_DETAIL_WIDTH", "200") or 200)
    results = []
    for cid, title, svc, timeout in CHECKS:
        if cid not in ids:
            continue
        started = time.time()
        if svc and ctx.profile_aware and ctx.expected and svc not in ctx.expected:
            status, detail, hint = ("SKIP", "profile {0} is off".format(PROFILE_OF[svc]),
                                    "add it to COMPOSE_PROFILES in .env, then docker compose up -d")
        else:
            status, detail, hint = run_one(ctx, cid, timeout)
        detail, hint = redact(one_line(detail)), redact(one_line(hint))
        results.append({"id": cid, "title": title, "status": status, "detail": detail, "hint": hint,
                        "seconds": round(time.time() - started, 1), "data": json.loads(redact(json.dumps(ctx.data.get(cid))))
                        if ctx.data.get(cid) is not None else None})
        print("  {0:<14} {1:<6} {2}".format(cid, status, one_line(detail, width)), flush=True)
        if status == "FAIL" and hint:
            print("  {0:<14} {1:<6} fix: {2}".format("", "", one_line(hint, width)), flush=True)
    counts = dict((s, sum(1 for r in results if r["status"] == s)) for s in ("PASS", "FAIL", "SKIP"))
    took = time.time() - ctx.started
    print("")
    print("Result: {0} passed, {1} failed, {2} skipped ({3} checks, {4:.0f} s).".format(
        counts["PASS"], counts["FAIL"], counts["SKIP"], len(results), took))
    if counts["FAIL"]:
        print("Fix the FAIL lines above, then re-run: tests/acceptance/run.sh --only " +
              ",".join(r["id"] for r in results if r["status"] == "FAIL"))
    if env("LAB_JSON") == "1":
        doc = {"suite": "cp-agentic-lab-acceptance", "version": 1,
               "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ctx.started)), "seconds": round(took, 1),
               "project": env("LAB_PROJECT"), "network": env("LAB_NETWORK"), "profiles": env("COMPOSE_PROFILES"),
               "model_calls": ctx.model, "summary": {k.lower(): v for k, v in counts.items()}, "checks": results}
        try:
            with open(os.path.join(OUT, "result.json"), "w", encoding="utf-8") as fh:
                fh.write(redact(json.dumps(doc, indent=1)))
        except OSError as exc:
            print("could not write the JSON result: {0}".format(exc), file=sys.stderr)
    sys.stdout.flush()
    os._exit(1 if counts["FAIL"] else 0)     # do not wait for a check thread that timed out


if __name__ == "__main__":
    sys.exit(main())
