#!/usr/bin/env python3
"""seed_builders.py — deploy-time import of the CP MCP agent fleet into Flowise and Langflow.

Runs as the one-shot `builders-import` compose service (parity with `n8n-import`): waits for each
builder, then imports every agent flow — the umbrella "CP MCP Gateway Agent" plus the per-domain
gateway agents listed in builders_agents.json (Quantum Management, Gaia, GW-CLI, Threat Prevention,
Threat Emulation, Reputation, cpinfo, HTTPS Inspection, Logs, Documentation, Policy Insights). Each
flow is a committed JSON with __PLACEHOLDER__ secrets substituted from the environment in memory and
imported idempotently (skip when a flow with the same name already exists). Nothing is written back
to the repo and no secret is ever printed.

Auth (per builder, in order):
  * Flowise : FLOWISE_API_KEY (Bearer) if set; otherwise LOGIN with ADMIN_EMAIL / ADMIN_PASSWORD —
              the stack admin account created at first setup — and reuse the session cookies.
  * Langflow: LANGFLOW_API_KEY (x-api-key) if set; otherwise LOGIN with ADMIN_EMAIL / ADMIN_PASSWORD
              (the provisioned LANGFLOW_SUPERUSER) and use the returned JWT as a Bearer token.

Stdlib only — runs unmodified in python:3.12-alpine. Exit 0 = every targeted flow is seeded (or was
already) on every reachable builder; exit 1 = a builder stayed unreachable or an import failed, so
the deploy surfaces it.
"""
from __future__ import annotations

import gzip
import http.cookiejar
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
UMBRELLA_NAME = "CP MCP Gateway Agent"
FLOWISE_DIR = os.path.join(HERE, "flowise")
LANGFLOW_DIR = os.path.join(HERE, "langflow")
MANIFEST = os.path.join(HERE, "builders_agents.json")

FLOWISE_URL = os.environ.get("FLOWISE_URL", "http://flowise:3020").rstrip("/")
LANGFLOW_URL = os.environ.get("LANGFLOW_URL", "http://langflow:7860").rstrip("/")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
WAIT_SECONDS = int(os.environ.get("BUILDER_WAIT_SECONDS", "180"))

PLACEHOLDERS = {
    "__MCP_GATEWAY_TOKEN__": os.environ.get("MCP_GATEWAY_TOKEN", ""),
    "__OPENAI_API_KEY__": os.environ.get("OPENAI_API_KEY", ""),
    "__AZURE_OPENAI_API_KEY__": os.environ.get("AZURE_OPENAI_API_KEY", ""),
    "__AZURE_OPENAI_ENDPOINT__": os.environ.get("AZURE_OPENAI_ENDPOINT", ""),
    "__AZURE_OPENAI_DEPLOYMENT__": os.environ.get("AZURE_OPENAI_DEPLOYMENT", ""),
}


def log(msg: str) -> None:
    print(msg, flush=True)


def substituted(path: str) -> dict:
    data = open(path, encoding="utf-8").read()
    for token, value in PLACEHOLDERS.items():
        data = data.replace(token, value)
    return json.loads(data)


def agent_entries() -> list:
    """Umbrella flow first, then every per-domain agent from the manifest. Each entry is
    {name, flowise, langflow} with absolute paths. Missing manifest = umbrella only (back-compat)."""
    entries = [{
        "name": UMBRELLA_NAME,
        "flowise": os.path.join(FLOWISE_DIR, "cp-mcp-gateway-agent.flowdata.json"),
        "langflow": os.path.join(LANGFLOW_DIR, "cp-mcp-gateway-agent.flow.json"),
    }]
    if os.path.exists(MANIFEST):
        try:
            m = json.load(open(MANIFEST, encoding="utf-8"))
        except ValueError:
            log(f"  WARNING: {MANIFEST} is not valid JSON — importing the umbrella flow only.")
            return entries
        for a in m.get("agents", []):
            entries.append({
                "name": a["name"],
                "flowise": os.path.join(HERE, a["flowise"]),
                "langflow": os.path.join(HERE, a["langflow"]),
            })
    return entries


def request(opener, method: str, url: str, *, body=None, headers=None, form=False, timeout=30,
            want_cookies=False):
    """One HTTP call. Returns (status, parsed-or-raw-body[, cookie-header]); raises only on transport
    errors. ``want_cookies`` returns a ready-to-send ``Cookie:`` header value assembled from the
    response's Set-Cookie headers — needed because http.cookiejar refuses to store cookies for
    single-label docker hostnames like ``flowise``, so sessions must be replayed manually."""
    if form:
        payload = urllib.parse.urlencode(body).encode()
        ctype = "application/x-www-form-urlencoded"
    elif body is not None:
        payload = json.dumps(body).encode()
        ctype = "application/json"
    else:
        payload, ctype = None, None
    req = urllib.request.Request(url, data=payload, method=method)
    if ctype:
        req.add_header("Content-Type", ctype)
    for k, v in (headers or {}).items():
        req.add_header(k, v)

    def _decode(blob: bytes) -> str:
        # Langflow gzips some list endpoints regardless of Accept-Encoding — decompress by magic bytes.
        if blob[:2] == b"\x1f\x8b":
            blob = gzip.decompress(blob)
        return blob.decode("utf-8", "replace")

    set_cookies: list = []
    try:
        with opener.open(req, timeout=timeout) as r:
            raw = _decode(r.read())
            status = r.status
            set_cookies = r.headers.get_all("Set-Cookie") or []
    except urllib.error.HTTPError as e:
        raw = _decode(e.read())
        status = e.code
        set_cookies = e.headers.get_all("Set-Cookie") or []
    try:
        parsed = json.loads(raw)
    except ValueError:
        parsed = raw
    if want_cookies:
        cookie_header = "; ".join(c.split(";", 1)[0] for c in set_cookies)
        return status, parsed, cookie_header
    return status, parsed


def wait_for(opener, name: str, url: str) -> bool:
    """Wait until the builder answers HTTP on any path (auth errors count as 'up')."""
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        try:
            status, _ = request(opener, "GET", url, timeout=5)
            log(f"  {name} is up (HTTP {status}).")
            return True
        except (urllib.error.URLError, OSError, TimeoutError):
            time.sleep(3)
    log(f"  ERROR: {name} did not answer at {url} within {WAIT_SECONDS}s.")
    return False


# ─────────────────────────── Flowise ───────────────────────────

def flowise_ensure_openai_credential(opener, headers) -> str:
    """Flowise keeps the model API key in a CREDENTIAL object, not in the flow JSON.
    Create one from OPENAI_API_KEY (already in our env) and return its id, so the
    imported agents' chatOpenAI nodes have a working key with no UI step. Idempotent by
    name. Returns "" when no key is available or creation fails (nodes then need the UI)."""
    key = os.environ.get("OPENAI_API_KEY", "")
    if not key:
        log("  (no OPENAI_API_KEY — the model nodes will need their credential set in the UI)")
        return ""
    name = "CP OpenAI (auto)"
    status, rows = request(opener, "GET", f"{FLOWISE_URL}/api/v1/credentials", headers=headers)
    if status == 200 and isinstance(rows, list):
        for r in rows:
            if isinstance(r, dict) and r.get("name") == name and r.get("id"):
                log(f"  credential '{name}' already present.")
                return r["id"]
    body = {"name": name, "credentialName": "openAIApi", "plainDataObj": {"openAIApiKey": key}}
    status, resp = request(opener, "POST", f"{FLOWISE_URL}/api/v1/credentials", body=body, headers=headers)
    if status in (200, 201) and isinstance(resp, dict) and resp.get("id"):
        log(f"  created model credential '{name}' from OPENAI_API_KEY.")
        return resp["id"]
    detail = resp if isinstance(resp, str) else json.dumps(resp)
    log(f"  WARNING: could not create the model credential (HTTP {status}: {detail[:160]}); set it in the UI.")
    return ""


def seed_flowise(entries: list) -> bool:
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    log(f"• Flowise: target {FLOWISE_URL}")
    if not wait_for(opener, "Flowise", f"{FLOWISE_URL}/api/v1/chatflows"):
        return False

    headers: dict = {}
    api_key = os.environ.get("FLOWISE_API_KEY", "")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        log("  auth: FLOWISE_API_KEY (Bearer).")
    else:
        if not (ADMIN_EMAIL and ADMIN_PASSWORD):
            log("  ERROR: neither FLOWISE_API_KEY nor ADMIN_EMAIL/ADMIN_PASSWORD is set — cannot authenticate.")
            return False
        status, resp, cookies = request(opener, "POST", f"{FLOWISE_URL}/api/v1/auth/login",
                                        body={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
                                        want_cookies=True)
        if status != 200 or not cookies:
            log(f"  ERROR: Flowise login failed (HTTP {status}) — is the admin account the stack admin? "
                "Set FLOWISE_API_KEY in .env as an override.")
            return False
        headers["Cookie"] = cookies    # replayed manually: cookiejar drops single-label docker hostnames
        # Flowise only honours cookie-JWT auth for "internal" (UI) requests; without this header the API
        # answers 401 and expects a Bearer API key instead.
        headers["x-request-from"] = "internal"
        log("  auth: admin login OK (session cookies).")

    # Fail-safe idempotency: if we cannot POSITIVELY parse the existing list, do NOT import — a mis-parse
    # here (e.g. an unexpected encoding) would otherwise create duplicate flows on every deploy.
    status, rows = request(opener, "GET", f"{FLOWISE_URL}/api/v1/chatflows", headers=headers)
    if status != 200 or not isinstance(rows, list):
        log(f"  ERROR: could not list chatflows (HTTP {status}) — refusing to import blind.")
        return False
    existing = {r.get("name") for r in rows if isinstance(r, dict)}

    cred_id = flowise_ensure_openai_credential(opener, headers)
    ok = True
    for e in entries:
        name = e["name"]
        if name in existing:
            log(f"  = {name} (exists — skip)")
            continue
        if not os.path.exists(e["flowise"]):
            log(f"  ! {name}: missing flow file {e['flowise']}")
            ok = False
            continue
        graph = substituted(e["flowise"])
        # Attach the auto-created model credential to the chatOpenAI node so the agent works on
        # import with no UI step (Flowise references the key by credential id).
        if cred_id:
            for n in graph.get("nodes", []):
                d = n.get("data", {})
                if d.get("name") == "chatOpenAI":
                    d["credential"] = cred_id
                    d.setdefault("inputs", {})["credential"] = cred_id
        body = {"name": name, "type": "CHATFLOW", "deployed": True, "flowData": json.dumps(graph)}
        status, resp = request(opener, "POST", f"{FLOWISE_URL}/api/v1/chatflows", body=body, headers=headers)
        if status in (200, 201):
            log(f"  + {name} (HTTP {status})")
        else:
            detail = resp if isinstance(resp, str) else json.dumps(resp)
            log(f"  ! {name}: chatflow POST failed (HTTP {status}): {detail[:200]}")
            ok = False
    return ok


# ─────────────────────────── Langflow ──────────────────────────

def seed_langflow(entries: list) -> bool:
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    log(f"• Langflow: target {LANGFLOW_URL}")
    if not wait_for(opener, "Langflow", f"{LANGFLOW_URL}/api/v1/version"):
        return False

    headers: dict = {}
    api_key = os.environ.get("LANGFLOW_API_KEY", "")
    if api_key:
        headers["x-api-key"] = api_key
        log("  auth: LANGFLOW_API_KEY (x-api-key).")
    else:
        if not (ADMIN_EMAIL and ADMIN_PASSWORD):
            log("  ERROR: neither LANGFLOW_API_KEY nor ADMIN_EMAIL/ADMIN_PASSWORD is set — cannot authenticate.")
            return False
        status, resp = request(opener, "POST", f"{LANGFLOW_URL}/api/v1/login",
                               body={"username": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, form=True)
        token = resp.get("access_token") if isinstance(resp, dict) else None
        if status != 200 or not token:
            log(f"  ERROR: Langflow superuser login failed (HTTP {status}). "
                "Set LANGFLOW_API_KEY in .env as an override.")
            return False
        headers["Authorization"] = f"Bearer {token}"
        log("  auth: superuser login OK (JWT).")

    # Same fail-safe as Flowise: a list we can't positively parse means NO import (Langflow gzips this
    # endpoint — a mis-parse here once caused a duplicate "CP MCP Gateway Agent (1)" on every run).
    status, data = request(opener, "GET", f"{LANGFLOW_URL}/api/v1/flows/?get_all=true&header_flows=true",
                           headers=headers)
    rows = data if isinstance(data, list) else (data.get("flows") if isinstance(data, dict) else None)
    if status != 200 or not isinstance(rows, list):
        log(f"  ERROR: could not list flows (HTTP {status}) — refusing to import blind.")
        return False
    existing = {r.get("name") for r in rows if isinstance(r, dict)}

    ok = True
    for e in entries:
        name = e["name"]
        if name in existing:
            log(f"  = {name} (exists — skip)")
            continue
        if not os.path.exists(e["langflow"]):
            log(f"  ! {name}: missing flow file {e['langflow']}")
            ok = False
            continue
        flow = substituted(e["langflow"])
        flow.pop("id", None)          # the export may carry a slug id; the create endpoint wants a UUID or none
        flow["name"] = name           # the create endpoint keys the flow on this name
        flow["endpoint_name"] = None  # avoid unique-endpoint collisions across the fleet
        status, resp = request(opener, "POST", f"{LANGFLOW_URL}/api/v1/flows/", body=flow, headers=headers)
        if status in (200, 201):
            log(f"  + {name} (HTTP {status})")
        else:
            detail = resp if isinstance(resp, str) else json.dumps(resp)
            log(f"  ! {name}: flow POST failed (HTTP {status}): {detail[:200]}")
            ok = False
    return ok


def main() -> int:
    entries = agent_entries()
    log(f"Seeding {len(entries)} agent flows into the org's agent builders "
        f"(umbrella + {len(entries) - 1} per-domain)…")
    if not PLACEHOLDERS["__MCP_GATEWAY_TOKEN__"]:
        log("  (warning: MCP_GATEWAY_TOKEN is empty — the agents won't reach the gateway)")
    ok_flowise = seed_flowise(entries)
    ok_langflow = seed_langflow(entries)
    if ok_flowise and ok_langflow:
        log("Builders import completed!!!")
        return 0
    log("Builders import FAILED for at least one builder (see errors above).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
