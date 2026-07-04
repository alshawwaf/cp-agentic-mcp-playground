#!/usr/bin/env python3
"""
Build-Your-Own-MCP — SCAFFOLD (your task!)
==========================================
Turn the Check Point IPS/CVE API into an MCP server the agents in this lab can
call. The API client and the whole Streamable-HTTP/JSON-RPC plumbing are DONE
for you — you only fill in the 5 TODOs, all about *declaring and wiring tools*.

Run it:            IPS_CLIENT_ID=... IPS_ACCESS_KEY=... python3 ips_cve_mcp.py
Test it:           see the guide (curl initialize -> tools/list -> tools/call)
Solution:          ../solution/ips_cve_mcp.py  (peek only if stuck)

Env: IPS_CLIENT_ID / IPS_ACCESS_KEY (Infinity Portal API key), MCP_PORT (3013).
"""
import json
import os
import time
import uuid
import urllib.request
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PROTOCOL_VERSION = "2025-03-26"
PORT = int(os.getenv("MCP_PORT", "3013"))
BEARER = os.getenv("MCP_BEARER_TOKEN", "")
AUTH_URL = os.getenv("IPS_AUTH_URL", "https://cloudinfra-gw-us.portal.checkpoint.com/auth/external").rstrip("/")
SERVICE_URL = os.getenv("IPS_SERVICE_URL", "https://cloudinfra-gw-us.portal.checkpoint.com/app/ips-info/api/v1").rstrip("/")


# --------------------------------------------------------------------------- #
# The "library" you are wrapping — already written. Two methods:
#   CLIENT.latest()          -> list of latest IPS protections
#   CLIENT.by_cve(cve_id)    -> list of protections covering that CVE
# --------------------------------------------------------------------------- #
class CveClient:
    def __init__(self):
        self._token = None
        self._exp = 0.0

    def _auth(self):
        if self._token and time.time() < self._exp:
            return
        cid, key = os.getenv("IPS_CLIENT_ID"), os.getenv("IPS_ACCESS_KEY")
        if not cid or not key:
            raise RuntimeError("IPS_CLIENT_ID / IPS_ACCESS_KEY are not set")
        body = json.dumps({"clientId": cid, "accessKey": key}).encode()
        req = urllib.request.Request(AUTH_URL, data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
        self._token = data["data"]["token"]
        self._exp = time.time() + 55 * 60

    def _get(self, path, params=None):
        self._auth()
        url = f"{SERVICE_URL}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self._token}",
                                                   "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
        return data if isinstance(data, list) else [data]

    def latest(self):
        return self._get("/get_latest_publications/")

    def by_cve(self, cve_id):
        if not cve_id:
            raise ValueError("cve_id is required")
        return self._get("/protections/by-cve/", {"cve_id": cve_id})


CLIENT = CveClient()


# --------------------------------------------------------------------------- #
# ✏️ TODO 1 — declare your tools.
# Each entry is: "tool_name": { "description", "inputSchema", "handler" }
#   - description : one clear sentence (the LLM reads this to decide when to call)
#   - inputSchema : JSON Schema for the arguments (empty object = no args)
#   - handler     : a function taking the args dict and returning JSON-able data
#
# Add TWO tools:
#   (a) "ips_latest_protections" — no arguments — calls CLIENT.latest()
#   (b) "ips_protections_by_cve" — one required string arg "cve_id" —
#        calls CLIENT.by_cve(cve_id)
# --------------------------------------------------------------------------- #
TOOLS = {
    # "ips_latest_protections": {
    #     "description": "...",
    #     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    #     "handler": lambda args: ...,
    # },
    # "ips_protections_by_cve": {
    #     "description": "...",
    #     "inputSchema": {"type": "object", "properties": {"cve_id": {"type": "string"}},
    #                     "required": ["cve_id"], "additionalProperties": False},
    #     "handler": lambda args: ...,
    # },
}


def handle_rpc(msg, session_id):
    method = msg.get("method")
    mid = msg.get("id")

    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "Check Point IPS/CVE (exercise)", "version": "0.1.0"},
        }}

    if method in ("notifications/initialized", "notifications/cancelled"):
        return None

    if method == "tools/list":
        # ✏️ TODO 2 — return every tool in TOOLS as
        #   {"name","description","inputSchema"}. The agent calls this first to
        #   discover what it can do.
        tools = []  # <- build this from TOOLS
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": tools}}

    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        # ✏️ TODO 3 — look up the tool by name in TOOLS (return a JSON-RPC error
        #             with code -32602 if it isn't found).
        # ✏️ TODO 4 — call its handler(args); wrap the return value as
        #             {"content": [{"type": "text", "text": <json string>}]}.
        # ✏️ TODO 5 — on exception, return the same shape with "isError": True
        #             and the error message as text (never let the server crash).
        return {"jsonrpc": "2.0", "id": mid,
                "error": {"code": -32601, "message": "tools/call not implemented yet"}}

    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}}


# --------------------------------------------------------------------------- #
# HTTP / Streamable-HTTP plumbing — DONE for you. Don't edit below.
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    def _authed(self):
        return (not BEARER) or self.headers.get("Authorization", "") == f"Bearer {BEARER}"

    def do_POST(self):
        if self.path.rstrip("/") not in ("", "/mcp", "/sse"):
            self.send_response(404); self.end_headers(); return
        if not self._authed():
            self.send_response(401); self.end_headers(); self.wfile.write(b"Unauthorized"); return
        length = int(self.headers.get("Content-Length", 0))
        try:
            msg = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self.send_response(400); self.end_headers(); return
        session_id = self.headers.get("mcp-session-id") or (
            str(uuid.uuid4()) if msg.get("method") == "initialize" else None)
        resp = handle_rpc(msg, session_id)
        if resp is None:
            self.send_response(202); self.end_headers(); return
        body = f"event: message\ndata: {json.dumps(resp)}\n\n".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        if session_id:
            self.send_header("mcp-session-id", session_id)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.send_response(405); self.end_headers()

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"IPS/CVE MCP server (scaffold) on :{PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
