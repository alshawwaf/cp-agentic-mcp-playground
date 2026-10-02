#!/usr/bin/env python3
"""seed_builders.py: deploy-time import of the lab's agents into Flowise and Langflow.

Runs as the one-shot compose service `builders-import` on every deploy (parity with n8n-import). To run it
by hand, run it inside the lab's Docker network: `docker compose run --rm builders-import`. The builders'
Docker names (flowise, langflow) do not resolve on the host.

What every run does (.env is the source of truth):
  * Model access. Builders never hold provider keys: every seeded agent calls the lab model `lab-chat` on
    LiteLLM (http://litellm:4000/v1) with LITELLM_MASTER_KEY, which is required.
      Flowise : credential "Lab Model (LiteLLM)" (OpenAI API type), created or updated from LITELLM_MASTER_KEY
                and attached to every seeded model node.
      Langflow: Credential variable LITELLM_MASTER_KEY, created or updated.
  * Integration secrets, created or updated whenever the value is set in .env:
      Flowise : variables MCP_GATEWAY_TOKEN, LAKERA_API_KEY, IDP_SCIM_TOKEN, DEVHUB_MCP_TOKEN, PILOT_MCP_TOKEN
                (the flows reference them as {{$vars.NAME}}, so no token is written into a flow), plus the
                credential "Lab Qdrant" when QDRANT_API_KEY is set.
      Langflow: Credential variables IDP_SCIM_TOKEN, LAKERA_API_KEY, QDRANT_API_KEY.
  * Flows. Every agent in builders_agents.json (the umbrella gateway agent included) is created in both
    builders, or updated in place when the repo version or a value derived from .env changed. Each seeded
    flow carries a marker ("labSeed": agent slug plus a checksum of what was seeded). A flow that was changed
    in the builder after seeding is left alone and logged; set SEED_OVERWRITE=1 to replace it with the repo
    version (this also replaces flows that carry no marker, for example flows seeded by an older version of
    this script). A seeded flow is never duplicated.
  * Renamed agents. builders_agents.json lists the names ("former_names") and slugs ("former_slugs") an agent
    was seeded under before. A flow found under a former name, or an untouched flow whose marker carries a
    former slug, is the same agent: it is renamed and updated in place (no second copy). If it was changed in
    the builder, it is kept under its former name, like any changed flow, until SEED_OVERWRITE=1.
  * Flowise extras for seeded flows only: Langfuse analytics when the LANGFUSE keys are set and the flow has
    no analytics setting of its own, and the Flowise API key "Lab Agents API", which /api/v1/prediction
    requires (the canvas chat is unaffected). Langflow is traced through LiteLLM.
  * Prerequisite flags: agents that need DOMAIN, a token, a Lakera key or an opt-in profile are imported
    anyway and named in the log with what they need.

Placeholders substituted in memory (never written back, never printed): __MCP_GATEWAY_TOKEN__,
__DEVHUB_MCP_TOKEN__ and __PILOT_MCP_TOKEN__ (Langflow MCP headers) and {{DOMAIN}} (both builders).
DOMAIN rule (the same rule n8n-import must use): DOMAIN when set; otherwise N8N_HOST without its "n8n."
prefix when N8N_HOST starts with "n8n."; otherwise unset, so the placeholder stays and the agents that need
it are flagged. A loopback name such as N8N_HOST=localhost is never turned into a domain.

Auth per builder: FLOWISE_API_KEY (Bearer) or LANGFLOW_API_KEY (x-api-key) when set; otherwise the stack
admin ADMIN_EMAIL / ADMIN_PASSWORD (on a fresh Flowise database the admin is registered first).

Other env: FLOWISE_URL (default http://flowise:3020; compose passes the real port), LANGFLOW_URL (default
http://langflow:7860), BUILDER_WAIT_SECONDS (default 180), LANGFUSE_HOST, SEED_OVERWRITE.

Stdlib only; runs unmodified in python:3.12-alpine and writes nothing to disk. Secret values are never
printed. Exit 0 = both builders are seeded; exit 1 = LITELLM_MASTER_KEY is missing, a builder stayed
unreachable, or an import failed.
"""
from __future__ import annotations

import copy
import gzip
import hashlib
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(HERE, "builders_agents.json")

FLOWISE_URL = os.environ.get("FLOWISE_URL", "http://flowise:3020").rstrip("/")
LANGFLOW_URL = os.environ.get("LANGFLOW_URL", "http://langflow:7860").rstrip("/")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
WAIT_SECONDS = int(os.environ.get("BUILDER_WAIT_SECONDS", "180") or "180")
LANGFUSE_ENDPOINT = os.environ.get("LANGFUSE_HOST", "") or "http://langfuse:3000"
OVERWRITE = os.environ.get("SEED_OVERWRITE", "").strip().lower() in ("1", "true", "yes")

MODEL_CREDENTIAL = "Lab Model (LiteLLM)"
LANGFUSE_CREDENTIAL = "Lab Tracing (Langfuse)"
QDRANT_CREDENTIAL = "Lab Qdrant"
FLOWISE_API_KEY_NAME = "Lab Agents API"
LEGACY_FLOWISE_CREDENTIALS = {"CP Langfuse (auto)": LANGFUSE_CREDENTIAL}
LEGACY_PROVIDER_CREDENTIALS = ("CP OpenAI (auto)", "CP Azure OpenAI (auto)", "CP Anthropic (auto)",
                               "CP Gemini (auto)")
LEGACY_LANGFLOW_VARIABLES = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "AZURE_OPENAI_API_KEY")
PUBLIC_DEFAULTS = {"LITELLM_MASTER_KEY": "sk-cp-litellm-training-key",
                   "MCP_GATEWAY_TOKEN": "cp-mcp-gateway-training-token"}

FLOWISE_VARIABLES = ("MCP_GATEWAY_TOKEN", "LAKERA_API_KEY", "IDP_SCIM_TOKEN", "DEVHUB_MCP_TOKEN", "PILOT_MCP_TOKEN")
LANGFLOW_VARIABLES = ("LITELLM_MASTER_KEY", "IDP_SCIM_TOKEN", "LAKERA_API_KEY", "QDRANT_API_KEY")
# Langflow fails a whole flow when a field's global variable does not exist. The Lakera Guard and SCIM
# components read a value equal to the variable's own name as "not configured" and answer with setup steps,
# so these two are created with that placeholder until .env provides the real value.
LANGFLOW_PLACEHOLDER_VARIABLES = ("IDP_SCIM_TOKEN", "LAKERA_API_KEY")
SECRET_NAMES = ("LITELLM_MASTER_KEY", "MCP_GATEWAY_TOKEN", "LAKERA_API_KEY", "IDP_SCIM_TOKEN", "DEVHUB_MCP_TOKEN",
                "PILOT_MCP_TOKEN", "QDRANT_API_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_PUBLIC_KEY",
                "ADMIN_PASSWORD", "FLOWISE_API_KEY", "LANGFLOW_API_KEY")
VOLATILE_KEYS = {"position", "positionAbsolute", "selected", "dragging", "width", "height", "measured",
                 "resizing", "zIndex"}


def env(name: str) -> str:
    return os.environ.get(name, "").strip()


def domain() -> str:
    """One rule for a blank DOMAIN (docs/REFERENCE; n8n-import must match)."""
    value = env("DOMAIN")
    if value:
        return value
    host = env("N8N_HOST")
    if host.startswith("n8n.") and len(host) > 4:
        return host[4:]
    return ""


DOMAIN = domain()
PLACEHOLDERS = {
    "__MCP_GATEWAY_TOKEN__": env("MCP_GATEWAY_TOKEN"),
    "__DEVHUB_MCP_TOKEN__": env("DEVHUB_MCP_TOKEN"),
    "__PILOT_MCP_TOKEN__": env("PILOT_MCP_TOKEN"),
    "{{DOMAIN}}": DOMAIN,
}


def redact(text: str) -> str:
    for name in SECRET_NAMES:
        value = env(name)
        if len(value) >= 4:
            text = text.replace(value, "<redacted>")
    return text


def log(msg: str) -> None:
    print(redact(msg), flush=True)


# ─────────────────────────── manifest and flow content ───────────────────────────

def agent_entries() -> list:
    """Agents from builders_agents.json, umbrella first. Entries without slug/name/files are skipped."""
    try:
        with open(MANIFEST, encoding="utf-8") as fh:
            manifest = json.load(fh)
    except (OSError, ValueError) as exc:
        log(f"  ERROR: cannot read {MANIFEST} ({exc}).")
        return []
    entries = []
    for i, a in enumerate(manifest.get("agents", [])):
        if not isinstance(a, dict) or not all(a.get(k) for k in ("slug", "name", "flowise", "langflow")):
            log(f"  WARNING: builders_agents.json entry {i} lacks slug, name, flowise or langflow; skipped.")
            continue
        entries.append({**a, "flowise": os.path.join(HERE, a["flowise"]), "langflow": os.path.join(HERE, a["langflow"])})
    umbrella = manifest.get("umbrella")
    entries.sort(key=lambda e: 0 if e["name"] == umbrella else 1)
    return entries


def substitute(obj):
    """Replace placeholders inside every string of a parsed flow (values are never JSON-escaped by hand)."""
    if isinstance(obj, str):
        for token, value in PLACEHOLDERS.items():
            if value and token in obj:
                obj = obj.replace(token, value)
        return obj
    if isinstance(obj, list):
        return [substitute(x) for x in obj]
    if isinstance(obj, dict):
        return {k: substitute(v) for k, v in obj.items()}
    return obj


def load_flow(path: str):
    with open(path, encoding="utf-8") as fh:
        return substitute(json.load(fh))


def checksum(graph: dict) -> str:
    """Checksum of the seeded content: nodes and edges without layout-only keys (moving a node is no edit)."""
    def clean(item: dict) -> dict:
        out = {k: v for k, v in item.items() if k not in VOLATILE_KEYS}
        if isinstance(out.get("data"), dict):
            out["data"] = {k: v for k, v in out["data"].items() if k != "selected"}
        return out
    nodes = sorted((clean(n) for n in graph.get("nodes") or [] if isinstance(n, dict)), key=lambda n: str(n.get("id")))
    edges = sorted((clean(e) for e in graph.get("edges") or [] if isinstance(e, dict)), key=lambda e: str(e.get("id")))
    blob = json.dumps({"nodes": nodes, "edges": edges}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def marker_of(graph: dict | None) -> dict:
    m = (graph or {}).get("labSeed")
    return m if isinstance(m, dict) else {}


def decide(deployed: dict | None, desired_sum: str) -> str:
    """create | same | update | overwrite | keep.

    The labSeed marker holds "source" (checksum of the repo version that was seeded, after substitution)
    and "checksum" (checksum of what the builder stored). checksum == current content means nobody changed
    the flow since it was seeded, so a new repo version may replace it."""
    if deployed is None:
        return "create"
    current = checksum(deployed)
    marker = marker_of(deployed)
    if marker.get("checksum") == current:
        return "same" if marker.get("source") == desired_sum else "update"
    if current == desired_sum:
        return "same"
    return "overwrite" if OVERWRITE else "keep"


def needs_marker(deployed: dict, desired_sum: str) -> bool:
    m = marker_of(deployed)
    return m.get("source") != desired_sum or m.get("checksum") != checksum(deployed)


def keep_reason(deployed: dict) -> str:
    if marker_of(deployed):
        return "changed in the builder after it was seeded"
    return "no seed marker (seeded by an older version, or created by hand)"


def slugs_of(entry: dict) -> set:
    """The agent's slug and the slugs it was seeded under before (builders_agents.json "former_slugs")."""
    return {entry["slug"], *[s for s in entry.get("former_slugs") or [] if isinstance(s, str) and s]}


def former_names(entry: dict) -> list:
    """Names the agent was seeded under before (builders_agents.json "former_names")."""
    return [n for n in entry.get("former_names") or [] if isinstance(n, str) and n and n != entry["name"]]


def pick(rows: list, entry: dict, name_of, graph_of):
    """The deployed flow of an agent among rows (None = not deployed). name_of(row) and graph_of(row) read a row.
      1. a flow with the agent's name (the one whose marker carries the agent's slug first);
      2. a flow with a former name of the agent whose marker carries one of its slugs, or no marker at all
         (seeded under that name by this or an older seeder): renamed in place, never duplicated;
      3. the only untouched flow whose marker carries one of the agent's slugs (an earlier name not listed).
    A flow under another name that carries the slug (for example a trainee's duplicate) is never taken over."""
    slugs = slugs_of(entry)

    def marked(row) -> bool:
        return marker_of(graph_of(row)).get("slug") in slugs

    named = [r for r in rows if name_of(r) == entry["name"]]
    if named:
        return next((r for r in named if marked(r)), named[0])
    for old in former_names(entry):
        prev = [r for r in rows if name_of(r) == old and (marked(r) or not marker_of(graph_of(r)))]
        if prev:
            return next((r for r in prev if marked(r)), prev[0])
    same = [r for r in rows if marked(r) and marker_of(graph_of(r)).get("checksum") == checksum(graph_of(r))]
    return same[0] if len(same) == 1 else None


def kept_line(name: str, deployed_name, deployed: dict) -> str:
    where = f" under its former name '{deployed_name}'" if deployed_name and deployed_name != name else ""
    return f"  - {name}: kept{where}, {keep_reason(deployed)}."


def note_leftovers(builder: str, entries: list, rows: list, name_of, used: set, id_of) -> None:
    """Name flows that still carry a former agent name but are not the seeded copy (for example an edited
    copy that was duplicated by hand). They are never deleted."""
    former = {n: e["name"] for e in entries for n in former_names(e)}
    for r in rows:
        name = name_of(r)
        if name in former and id_of(r) not in used:
            log(f"  note: {builder} flow '{name}' has a former name of '{former[name]}' and is not the seeded copy. "
                "It is left as it is; delete it in the builder if you no longer need it.")


def summary(builder: str, counts: dict) -> None:
    log(f"  {builder} flows: {counts['create']} created, {counts['update'] + counts['overwrite']} updated, "
        f"{counts['same']} up to date, {counts['keep']} kept.")
    if counts["keep"]:
        log(f"  {counts['keep']} {builder} flow(s) were kept as they are. To replace them with the repo version "
            "(their edits are lost), run: docker compose run --rm -e SEED_OVERWRITE=1 builders-import")


def flag_prerequisites(entries: list) -> None:
    """Name the agents that are imported but cannot work yet, and what they need."""
    for e in entries:
        missing = []
        for name in e.get("requires") or []:
            if name == "DOMAIN":
                if not DOMAIN:
                    missing.append("DOMAIN")
            elif not env(name):
                missing.append(name)
        if missing:
            log(f"  note: {e['name']} needs {', '.join(missing)} in .env; set it and re-run builders-import.")
        if e.get("profile"):
            host = urllib.parse.urlparse(e.get("endpoint") or "").hostname or ""
            try:
                socket.getaddrinfo(host, None)
            except OSError:
                log(f"  note: {e['name']} needs the {e['profile']} profile: "
                    f"docker compose --profile {e['profile']} up -d {host}. Until then it has no tools.")


# ─────────────────────────── HTTP ───────────────────────────

def request(method: str, url: str, *, body=None, headers=None, form=False, timeout=60, want_cookies=False):
    """One HTTP call. Returns (status, parsed-or-text[, cookie header]). Transport errors give status 0.

    want_cookies replays Set-Cookie by hand: http.cookiejar drops cookies for single-label Docker names
    such as "flowise"."""
    if form:
        payload, ctype = urllib.parse.urlencode(body).encode(), "application/x-www-form-urlencoded"
    elif body is not None:
        payload, ctype = json.dumps(body).encode(), "application/json"
    else:
        payload, ctype = None, None
    req = urllib.request.Request(url, data=payload, method=method)
    if ctype:
        req.add_header("Content-Type", ctype)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    cookies: list = []
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw, status, cookies = r.read(), r.status, r.headers.get_all("Set-Cookie") or []
    except urllib.error.HTTPError as e:
        raw, status, cookies = e.read(), e.code, e.headers.get_all("Set-Cookie") or []
    except (urllib.error.URLError, OSError, ValueError) as e:
        reason = getattr(e, "reason", e)
        raw, status = f"{type(reason).__name__}: {reason}".encode(), 0
    if raw[:2] == b"\x1f\x8b":            # Langflow gzips some list endpoints regardless of Accept-Encoding
        raw = gzip.decompress(raw)
    text = raw.decode("utf-8", "replace")
    try:
        parsed = json.loads(text)
    except ValueError:
        parsed = text
    if want_cookies:
        return status, parsed, "; ".join(c.split(";", 1)[0] for c in cookies)
    return status, parsed


def detail(resp) -> str:
    return redact(resp if isinstance(resp, str) else json.dumps(resp))[:200]


def wait_for(name: str, url: str) -> bool:
    """Wait until the builder answers HTTP (auth errors count as up). Fail fast when the name never resolves."""
    host = urllib.parse.urlparse(url).hostname or ""
    deadline = time.monotonic() + WAIT_SECONDS
    dns_failures = 0
    while time.monotonic() < deadline:
        try:
            socket.getaddrinfo(host, None)
        except OSError:
            dns_failures += 1
            if dns_failures >= 10:
                log(f"  ERROR: {host} does not resolve, so {name} is not reachable from here. Run the seeder "
                    "inside the lab network: docker compose run --rm builders-import")
                return False
            time.sleep(2)
            continue
        status, _ = request("GET", url, timeout=5)
        if status:
            log(f"  {name} is up (HTTP {status}).")
            return True
        time.sleep(3)
    log(f"  ERROR: {name} did not answer at {url} within {WAIT_SECONDS}s.")
    return False


# ─────────────────────────── Flowise ───────────────────────────

class Flowise:
    def __init__(self):
        self.headers: dict = {}

    def call(self, method: str, path: str, body=None, timeout=60):
        return request(method, f"{FLOWISE_URL}{path}", body=body, headers=self.headers, timeout=timeout)

    def login(self) -> bool:
        if env("FLOWISE_API_KEY"):
            self.headers = {"Authorization": f"Bearer {env('FLOWISE_API_KEY')}"}
            log("  auth: FLOWISE_API_KEY.")
            return True
        if not (ADMIN_EMAIL and ADMIN_PASSWORD):
            log("  ERROR: set ADMIN_EMAIL and ADMIN_PASSWORD (or FLOWISE_API_KEY) so the seeder can sign in.")
            return False
        creds = {"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        status, _, cookies = request("POST", f"{FLOWISE_URL}/api/v1/auth/login", body=creds, want_cookies=True)
        if status != 200 or not cookies:
            # Fresh database: Flowise 3.x has no env-based admin bootstrap, so register the stack admin (this
            # also creates the default organization and workspace) and sign in again. For any other cause
            # (for example a different password on an existing account) register fails and the error stays.
            reg, _ = request("POST", f"{FLOWISE_URL}/api/v1/account/register",
                             body={"user": {"name": ADMIN_EMAIL.split("@")[0], "email": ADMIN_EMAIL,
                                            "credential": ADMIN_PASSWORD, "confirmPassword": ADMIN_PASSWORD}})
            if reg in (200, 201):
                log("  auth: fresh Flowise database; registered the stack admin.")
                status, _, cookies = request("POST", f"{FLOWISE_URL}/api/v1/auth/login", body=creds,
                                             want_cookies=True)
        if status != 200 or not cookies:
            log(f"  ERROR: Flowise sign-in failed (HTTP {status}). Check N8N_ADMIN_EMAIL / N8N_ADMIN_PASSWORD, "
                "or set FLOWISE_API_KEY.")
            return False
        # Flowise honours cookie sessions only for requests marked as coming from its own UI.
        self.headers = {"Cookie": cookies, "x-request-from": "internal"}
        log("  auth: admin sign-in.")
        return True

    # credentials ------------------------------------------------------------
    def credential(self, name: str, cred_type: str, plain: dict, legacy: tuple = ()) -> str:
        """Create or update one credential from env. Returns its id ('' on failure)."""
        status, rows = self.call("GET", "/api/v1/credentials")
        rows = rows if status == 200 and isinstance(rows, list) else []
        found = next((r for r in rows if isinstance(r, dict) and r.get("name") == name), None)
        if not found:
            found = next((r for r in rows if isinstance(r, dict) and r.get("name") in legacy), None)
        body = {"name": name, "credentialName": cred_type, "plainDataObj": plain}
        if found:
            status, resp = self.call("PUT", f"/api/v1/credentials/{found['id']}", body)
            if status in (200, 201):
                renamed = f" (renamed from '{found['name']}')" if found["name"] != name else ""
                log(f"  credential '{name}': updated from .env{renamed}.")
                return found["id"]
            log(f"  WARNING: credential '{name}': update failed (HTTP {status}: {detail(resp)}).")
            return found["id"]
        status, resp = self.call("POST", "/api/v1/credentials", body)
        if status in (200, 201) and isinstance(resp, dict) and resp.get("id"):
            log(f"  credential '{name}': created from .env.")
            return resp["id"]
        log(f"  WARNING: credential '{name}': create failed (HTTP {status}: {detail(resp)}).")
        return ""

    def legacy_credentials(self) -> None:
        status, rows = self.call("GET", "/api/v1/credentials")
        names = sorted(r.get("name") for r in rows if isinstance(r, dict)) if status == 200 and isinstance(rows, list) else []
        old = [n for n in names if n in LEGACY_PROVIDER_CREDENTIALS]
        if old:
            log(f"  note: credentials {', '.join(old)} hold provider keys from an older seeder. The lab agents no "
                "longer use them (LiteLLM holds the provider keys); delete them in Flowise > Credentials once no "
                "flow of yours uses them.")

    # variables ---------------------------------------------------------------
    def variables(self) -> None:
        status, rows = self.call("GET", "/api/v1/variables")
        if status != 200 or not isinstance(rows, list):
            log(f"  WARNING: cannot list Flowise variables (HTTP {status}); the agents' tokens are not synced.")
            return
        by_name = {r.get("name"): r for r in rows if isinstance(r, dict)}
        for name in FLOWISE_VARIABLES:
            value = env(name)
            if not value:
                state = "kept (not set in .env)" if name in by_name else "not created (not set in .env)"
                log(f"  variable {name}: {state}.")
                continue
            row = by_name.get(name)
            body = {"name": name, "value": value, "type": "static"}
            if row and row.get("value") == value and row.get("type") == "static":
                log(f"  variable {name}: unchanged.")
                continue
            if row:
                status, resp = self.call("PUT", f"/api/v1/variables/{row['id']}", body)
                verb = "updated from .env"
            else:
                status, resp = self.call("POST", "/api/v1/variables", body)
                verb = "created from .env"
            log(f"  variable {name}: {verb}." if status in (200, 201) else
                f"  WARNING: variable {name}: HTTP {status}: {detail(resp)}")

    # API key for /api/v1/prediction --------------------------------------------
    def api_key_id(self) -> str:
        status, rows = self.call("GET", "/api/v1/apikey")
        if status == 200 and isinstance(rows, list):
            for r in rows:
                if isinstance(r, dict) and r.get("keyName") == FLOWISE_API_KEY_NAME and r.get("id"):
                    return r["id"]
        status, resp = self.call("POST", "/api/v1/apikey", {"keyName": FLOWISE_API_KEY_NAME,
                                                            "permissions": ["chatflows:view"]})
        rows = resp if isinstance(resp, list) else [resp] if isinstance(resp, dict) else []
        for r in rows:
            if isinstance(r, dict) and r.get("keyName") == FLOWISE_API_KEY_NAME and r.get("id"):
                log(f"  API key '{FLOWISE_API_KEY_NAME}': created (Flowise > API Keys); /api/v1/prediction on "
                    "the seeded agents requires it.")
                return r["id"]
        log(f"  WARNING: could not create the Flowise API key '{FLOWISE_API_KEY_NAME}' (HTTP {status}); the "
            "seeded agents' prediction API stays open.")
        return ""


def attach_flowise_credentials(graph: dict, model_id: str, qdrant_id: str) -> None:
    for n in graph.get("nodes", []):
        d = n.get("data", {})
        name = d.get("name")
        if name == "chatOpenAI":
            d["credential"] = model_id
            d.setdefault("inputs", {})["credential"] = model_id
        elif name == "agentAgentflow":
            cfg = d.get("inputs", {}).get("agentModelConfig")
            if isinstance(cfg, dict):
                cfg["credential"] = model_id
                cfg["FLOWISE_CREDENTIAL_ID"] = model_id
        elif name == "qdrant":
            d["credential"] = qdrant_id
            d.setdefault("inputs", {})["credential"] = qdrant_id


def seed_flowise(entries: list) -> bool:
    log(f"Flowise: {FLOWISE_URL}")
    if not wait_for("Flowise", f"{FLOWISE_URL}/api/v1/ping"):
        return False
    fw = Flowise()
    if not fw.login():
        return False

    model_id = fw.credential(MODEL_CREDENTIAL, "openAIApi", {"openAIApiKey": env("LITELLM_MASTER_KEY")})
    if not model_id:
        log("  ERROR: without the model credential no seeded agent can answer.")
        return False
    qdrant_id = fw.credential(QDRANT_CREDENTIAL, "qdrantApi", {"qdrantApiKey": env("QDRANT_API_KEY")}) \
        if env("QDRANT_API_KEY") else ""
    langfuse_id = ""
    if env("LANGFUSE_PUBLIC_KEY") and env("LANGFUSE_SECRET_KEY"):
        # Capital F in the field names: that is what Flowise's langfuseApi credential expects.
        langfuse_id = fw.credential(LANGFUSE_CREDENTIAL, "langfuseApi", {
            "langFusePublicKey": env("LANGFUSE_PUBLIC_KEY"), "langFuseSecretKey": env("LANGFUSE_SECRET_KEY"),
            "langFuseEndpoint": LANGFUSE_ENDPOINT}, legacy=tuple(LEGACY_FLOWISE_CREDENTIALS))
    else:
        log("  Langfuse keys not set: seeded chatflows are not traced.")
    fw.legacy_credentials()
    fw.variables()
    api_key_id = fw.api_key_id()
    analytic = json.dumps({"langFuse": {"credentialId": langfuse_id, "release": "", "status": True}}) \
        if langfuse_id else ""

    # Fail-safe: a list we cannot parse means no import at all (a blind import would duplicate every flow).
    status, rows = fw.call("GET", "/api/v1/chatflows")
    if status != 200 or not isinstance(rows, list):
        log(f"  ERROR: could not list chatflows (HTTP {status}); nothing imported.")
        return False
    existing = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        try:
            graph = json.loads(r.get("flowData") or "{}")
        except ValueError:
            graph = {}
        existing.append((r, graph if isinstance(graph, dict) else {}))

    ok, counts = True, {"create": 0, "same": 0, "update": 0, "overwrite": 0, "keep": 0}
    used: set = set()
    for e in entries:
        name, slug = e["name"], e["slug"]
        try:
            graph = load_flow(e["flowise"])
        except (OSError, ValueError) as exc:
            log(f"  ! {name}: cannot read {os.path.relpath(e['flowise'], HERE)} ({exc}).")
            ok = False
            continue
        attach_flowise_credentials(graph, model_id, qdrant_id)
        ftype = "AGENTFLOW" if any(n.get("type") == "agentFlow" for n in graph.get("nodes", [])) else "CHATFLOW"
        desired_sum = checksum(graph)
        graph["labSeed"] = {"slug": slug, "source": desired_sum, "checksum": desired_sum}
        match = find_flowise(existing, e)
        row, deployed = match if match else (None, None)
        if row:
            used.add(row.get("id"))
        renamed = f" (renamed from '{row.get('name')}')" if row and row.get("name") != name else ""
        action = decide(deployed, desired_sum)
        counts[action] += 1
        if action == "keep":
            log(kept_line(name, row.get("name"), deployed))
            continue
        if action == "same":
            body = {}
            if needs_marker(deployed, desired_sum) or row.get("name") != name:
                kept = dict(deployed)
                kept["labSeed"] = {"slug": slug, "source": desired_sum, "checksum": checksum(deployed)}
                body.update({"name": name, "flowData": json.dumps(kept)})
            if analytic and not row.get("analytic"):
                body["analytic"] = analytic
            if body:
                status, resp = fw.call("PUT", f"/api/v1/chatflows/{row['id']}", body)
                if renamed and status not in (200, 201):
                    log(f"  ! {name}: rename failed (HTTP {status}: {detail(resp)}).")
                    ok = False
                    continue
            log(f"  = {name}: up to date{renamed}.")
            continue
        body = {"name": name, "type": ftype, "flowData": json.dumps(graph)}
        if api_key_id:
            body["apikeyid"] = api_key_id
        if analytic and not (row or {}).get("analytic"):
            body["analytic"] = analytic
        if action == "create":
            body["deployed"] = True
            status, resp = fw.call("POST", "/api/v1/chatflows", body)
            verb = "created"
        else:
            if row.get("type") != ftype:
                # A chatflow cannot become an agentflow in place: replace the old record.
                status, resp = fw.call("DELETE", f"/api/v1/chatflows/{row['id']}")
                if status in (200, 201, 204):
                    body["deployed"] = True
                    status, resp = fw.call("POST", "/api/v1/chatflows", body)
                verb = f"replaced ({row.get('type')} -> {ftype})" + renamed
            else:
                status, resp = fw.call("PUT", f"/api/v1/chatflows/{row['id']}", body)
                verb = "updated in place" + (" (SEED_OVERWRITE=1)" if action == "overwrite" else "") + renamed
        if status in (200, 201):
            log(f"  + {name}: {verb}.")
            stored = flowise_graph(resp)
            if stored is not None and checksum(stored) != desired_sum and isinstance(resp, dict):
                graph["labSeed"]["checksum"] = checksum(stored)      # Flowise normalised the content
                stored["labSeed"] = graph["labSeed"]
                fw.call("PUT", f"/api/v1/chatflows/{resp['id']}", {"flowData": json.dumps(stored)})
        else:
            log(f"  ! {name}: {verb} failed (HTTP {status}: {detail(resp)}).")
            ok = False
    note_leftovers("Flowise", entries, existing, lambda x: x[0].get("name"), used, lambda x: x[0].get("id"))
    summary("Flowise", counts)
    return ok


def flowise_graph(resp):
    if not isinstance(resp, dict):
        return None
    try:
        g = json.loads(resp.get("flowData") or "")
    except ValueError:
        return None
    return g if isinstance(g, dict) else None


def find_flowise(existing: list, entry: dict):
    """The deployed (row, graph) of an agent, or None (see pick: current name, former name, untouched slug)."""
    return pick(existing, entry, lambda x: x[0].get("name"), lambda x: x[1])


# ─────────────────────────── Langflow ───────────────────────────

class Langflow:
    def __init__(self):
        self.headers: dict = {}

    def call(self, method: str, path: str, body=None, timeout=120):
        return request(method, f"{LANGFLOW_URL}{path}", body=body, headers=self.headers, timeout=timeout)

    def login(self) -> bool:
        if env("LANGFLOW_API_KEY"):
            self.headers = {"x-api-key": env("LANGFLOW_API_KEY")}
            log("  auth: LANGFLOW_API_KEY.")
            return True
        if not (ADMIN_EMAIL and ADMIN_PASSWORD):
            log("  ERROR: set ADMIN_EMAIL and ADMIN_PASSWORD (or LANGFLOW_API_KEY) so the seeder can sign in.")
            return False
        status, resp = request("POST", f"{LANGFLOW_URL}/api/v1/login",
                               body={"username": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, form=True)
        token = resp.get("access_token") if isinstance(resp, dict) else None
        if status != 200 or not token:
            log(f"  ERROR: Langflow superuser sign-in failed (HTTP {status}). Set LANGFLOW_API_KEY as an override.")
            return False
        self.headers = {"Authorization": f"Bearer {token}"}
        log("  auth: superuser sign-in.")
        return True

    def variables(self) -> bool:
        status, rows = self.call("GET", "/api/v1/variables/")
        if status != 200 or not isinstance(rows, list):
            log(f"  ERROR: cannot list Langflow variables (HTTP {status}).")
            return False
        by_name = {r.get("name"): r for r in rows if isinstance(r, dict)}
        ok = True
        for name in LANGFLOW_VARIABLES:
            value = env(name)
            row = by_name.get(name)
            if not value and row:
                log(f"  variable {name}: kept (not set in .env).")
                continue
            if not value and name not in LANGFLOW_PLACEHOLDER_VARIABLES:
                log(f"  variable {name}: not created (not set in .env).")
                continue
            if not value:
                status, resp = self.call("POST", "/api/v1/variables/", {"name": name, "value": name,
                                                                        "type": "Credential", "default_fields": []})
                log(f"  variable {name}: created as a not-configured placeholder (set {name} in .env to enable "
                    "it)." if status in (200, 201) else f"  WARNING: variable {name}: HTTP {status}: {detail(resp)}")
                continue
            if row:
                status, resp = self.call("PATCH", f"/api/v1/variables/{row['id']}",
                                         {"id": row["id"], "name": name, "value": value})
                verb = "updated from .env"
            else:
                status, resp = self.call("POST", "/api/v1/variables/", {"name": name, "value": value,
                                                                        "type": "Credential", "default_fields": []})
                verb = "created from .env"
            if status in (200, 201):
                log(f"  variable {name}: {verb}.")
            else:
                log(f"  WARNING: variable {name}: HTTP {status}: {detail(resp)}")
                ok = ok and name != "LITELLM_MASTER_KEY"
        old = sorted(n for n in by_name if n in LEGACY_LANGFLOW_VARIABLES)
        if old:
            log(f"  note: variables {', '.join(old)} hold provider keys from an older seeder. The lab flows no longer "
                "use them; delete them in Langflow > Settings > Global Variables once no flow of yours uses them.")
        return ok


def resolves(host: str, tries: int = 3) -> bool:
    for _ in range(tries):
        try:
            socket.getaddrinfo(host, None)
            return True
        except OSError:
            time.sleep(2)
    return False


def seed_langflow(entries: list) -> bool:
    log(f"Langflow: {LANGFLOW_URL}")
    # Langflow is an opt-in compose profile; compose sets LANGFLOW_OPTIONAL=1 so a stack without it is not an error.
    if os.environ.get("LANGFLOW_OPTIONAL") == "1" and not resolves(urllib.parse.urlparse(LANGFLOW_URL).hostname or ""):
        log("  Langflow is not running (add langflow to COMPOSE_PROFILES); skipped.")
        return True
    if not wait_for("Langflow", f"{LANGFLOW_URL}/api/v1/version"):
        return False
    lf = Langflow()
    if not lf.login():
        return False
    if not lf.variables():
        return False

    status, data = lf.call("GET", "/api/v1/flows/?get_all=true&header_flows=true")
    rows = data if isinstance(data, list) else (data.get("flows") if isinstance(data, dict) else None)
    if status != 200 or not isinstance(rows, list):
        log(f"  ERROR: could not list flows (HTTP {status}); nothing imported.")
        return False
    flows = [r for r in rows if isinstance(r, dict) and r.get("id") and not r.get("is_component")]
    cache: dict = {}

    def full(flow_id: str) -> dict:
        if flow_id not in cache:
            st, f = lf.call("GET", f"/api/v1/flows/{flow_id}")
            cache[flow_id] = f if st == 200 and isinstance(f, dict) else {}
        return cache[flow_id]

    def graph_of(row: dict) -> dict:
        return (full(row["id"]).get("data") or {}) if full(row["id"]) else {}

    ok, counts = True, {"create": 0, "same": 0, "update": 0, "overwrite": 0, "keep": 0}
    used: set = set()
    for e in entries:
        name, slug = e["name"], e["slug"]
        try:
            flow = load_flow(e["langflow"])
        except (OSError, ValueError) as exc:
            log(f"  ! {name}: cannot read {os.path.relpath(e['langflow'], HERE)} ({exc}).")
            ok = False
            continue
        graph = flow.get("data") or {}
        desired_sum = checksum(graph)
        graph["labSeed"] = {"slug": slug, "source": desired_sum, "checksum": desired_sum}
        row = pick(flows, e, lambda r: r.get("name"), graph_of)
        match = full(row["id"]) if row else None
        if row and not match:
            # Never create a second copy of a flow that exists but cannot be read right now.
            log(f"  ! {name}: cannot read the existing flow '{row.get('name')}'; skipped. Run builders-import again.")
            ok = False
            continue
        if row:
            used.add(row["id"])
        renamed = f" (renamed from '{match.get('name')}')" if match and match.get("name") != name else ""
        deployed = (match.get("data") or {}) if match else None
        action = decide(deployed, desired_sum)
        counts[action] += 1
        if action == "keep":
            log(kept_line(name, match.get("name"), deployed))
            continue
        if action == "same":
            if needs_marker(deployed, desired_sum) or match.get("name") != name:
                kept = dict(deployed)
                kept["labSeed"] = {"slug": slug, "source": desired_sum, "checksum": checksum(deployed)}
                status, resp = lf.call("PATCH", f"/api/v1/flows/{match['id']}", {"name": name, "data": kept})
                if renamed and status not in (200, 201):
                    log(f"  ! {name}: rename failed (HTTP {status}: {detail(resp)}).")
                    ok = False
                    continue
            log(f"  = {name}: up to date{renamed}.")
            continue
        if action == "create":
            body = copy.deepcopy(flow)
            body.pop("id", None)           # the create endpoint wants a UUID or nothing
            body.update({"name": name, "data": graph, "endpoint_name": None})
            status, resp = lf.call("POST", "/api/v1/flows/", body)
            verb = "created"
        else:
            body = {"name": name, "data": graph, "description": flow.get("description")}
            status, resp = lf.call("PATCH", f"/api/v1/flows/{match['id']}", body)
            verb = "updated in place" + (" (SEED_OVERWRITE=1)" if action == "overwrite" else "") + renamed
        if status in (200, 201):
            log(f"  + {name}: {verb}.")
            stored = resp.get("data") if isinstance(resp, dict) else None
            if isinstance(stored, dict) and checksum(stored) != desired_sum and resp.get("id"):
                graph["labSeed"]["checksum"] = checksum(stored)      # Langflow normalised the content
                stored["labSeed"] = graph["labSeed"]
                lf.call("PATCH", f"/api/v1/flows/{resp['id']}", {"data": stored})
        else:
            log(f"  ! {name}: {verb} failed (HTTP {status}: {detail(resp)}).")
            ok = False
    note_leftovers("Langflow", entries, flows, lambda r: r.get("name"), used, lambda r: r.get("id"))
    summary("Langflow", counts)
    return ok


# ─────────────────────────── main ───────────────────────────

def main() -> int:
    if not env("LITELLM_MASTER_KEY"):
        log("ERROR: LITELLM_MASTER_KEY is not set. Every seeded agent reaches the lab model (LiteLLM) with this key. "
            "Run ./setup.sh (or add LITELLM_MASTER_KEY to .env), then run: docker compose up -d litellm && "
            "docker compose run --rm builders-import")
        return 1
    for name, public in PUBLIC_DEFAULTS.items():
        if env(name) == public:
            log(f"WARNING: {name} is the published training default. Generate a private value with ./setup.sh.")
    if not env("MCP_GATEWAY_TOKEN"):
        log("WARNING: MCP_GATEWAY_TOKEN is not set; the gateway agents cannot authenticate to the MCP Gateway.")
    entries = agent_entries()
    if not entries:
        log("ERROR: no agents to seed (see builders_agents.json).")
        return 1
    log(f"Seeding {len(entries)} agents into Flowise and Langflow"
        f"{' (SEED_OVERWRITE=1: flows changed in the builder are replaced)' if OVERWRITE else ''}.")
    if not DOMAIN:
        log("  DOMAIN is not set (and N8N_HOST is not n8n.<domain>): external endpoints keep the {{DOMAIN}} "
            "placeholder.")
    flag_prerequisites(entries)
    results = {}
    for builder, seed in (("Flowise", seed_flowise), ("Langflow", seed_langflow)):
        try:
            results[builder] = seed(entries)
        except Exception as exc:  # one builder failing must not stop the other
            log(f"  ERROR: {builder} seeding stopped: {type(exc).__name__}: {exc}")
            results[builder] = False
    if all(results.values()):
        log("Builders import completed.")
        return 0
    failed = ", ".join(b for b, r in results.items() if not r)
    log(f"Builders import failed for {failed} (see the errors above).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
