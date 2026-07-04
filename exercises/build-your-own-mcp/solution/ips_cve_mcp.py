#!/usr/bin/env python3
"""
Build-Your-Own-MCP — SOLUTION
=============================
A minimal Model Context Protocol server that exposes Check Point's IPS/CVE
Publications API as two agent tools:

  * ips_latest_protections   -> latest published IPS protections
  * ips_protections_by_cve   -> protections that cover a given CVE

It speaks **Streamable HTTP** (the transport the Docker MCP Gateway and n8n use)
and is written with the Python standard library ONLY — no pip installs, no MCP
SDK. That keeps it runnable anywhere and shows exactly what a framework like
FastMCP does for you under the hood (initialize handshake, session id,
tools/list, tools/call). See the guide for a "going further with FastMCP" note.

Env:
  IPS_CLIENT_ID / IPS_ACCESS_KEY   Infinity Portal API key (client id + access key)
  IPS_AUTH_URL                     default https://cloudinfra-gw-us.portal.checkpoint.com/auth/external
  IPS_SERVICE_URL                  default https://cloudinfra-gw-us.portal.checkpoint.com/app/ips-info/api/v1
  MCP_PORT                         default 3013
  MCP_BEARER_TOKEN                 optional; if set, clients must send Authorization: Bearer <token>
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
# Tiny CVE API client (urllib, token cached ~55 min). In production you'd reuse
# the fuller requests-based client in ../../../ (retries, region fallback).
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
# Tool registry — name -> (schema, handler). This IS the MCP "toolbox".
# --------------------------------------------------------------------------- #
TOOLS = {
    "ips_latest_protections": {
        "description": "Get the latest published Check Point IPS protections.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "handler": lambda args: CLIENT.latest(),
    },
    "ips_protections_by_cve": {
        "description": "Get the Check Point IPS protections that cover a specific CVE.",
        "inputSchema": {
            "type": "object",
            "properties": {"cve_id": {"type": "string", "description": "e.g. CVE-2024-3400"}},
            "required": ["cve_id"],
            "additionalProperties": False,
        },
        "handler": lambda args: CLIENT.by_cve(args.get("cve_id", "")),
    },
}


# --------------------------------------------------------------------------- #
# MCP JSON-RPC dispatch
# --------------------------------------------------------------------------- #
def handle_rpc(msg, session_id):
    method = msg.get("method")
    mid = msg.get("id")

    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "Check Point IPS/CVE (exercise)", "version": "1.0.0"},
        }}

    if method in ("notifications/initialized", "notifications/cancelled"):
        return None  # notifications get no response

    if method == "tools/list":
        tools = [{"name": n, "description": t["description"], "inputSchema": t["inputSchema"]}
                 for n, t in TOOLS.items()]
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": tools}}

    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        tool = TOOLS.get(name)
        if not tool:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"Unknown tool: {name}"}}
        try:
            result = tool["handler"](args)
            text = json.dumps(result, indent=2)
            return {"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": text}]}}
        except Exception as e:  # surface tool errors as an MCP tool result, not a crash
            return {"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": f"Error: {e}"}], "isError": True}}

    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}}


class Handler(BaseHTTPRequestHandler):
    def _unauthorized(self):
        self.send_response(401); self.end_headers(); self.wfile.write(b"Unauthorized")

    def _authed(self):
        if not BEARER:
            return True
        return self.headers.get("Authorization", "") == f"Bearer {BEARER}"

    def do_POST(self):
        if self.path.rstrip("/") not in ("", "/mcp", "/sse"):
            self.send_response(404); self.end_headers(); return
        if not self._authed():
            return self._unauthorized()
        length = int(self.headers.get("Content-Length", 0))
        try:
            msg = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self.send_response(400); self.end_headers(); return

        session_id = self.headers.get("mcp-session-id") or (
            str(uuid.uuid4()) if msg.get("method") == "initialize" else None)
        resp = handle_rpc(msg, session_id)

        if resp is None:  # a notification -> 202, no body
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
        # Streamable HTTP allows a GET SSE stream for server->client messages;
        # this minimal server is request/response only.
        self.send_response(405); self.end_headers()

    def log_message(self, *a):
        pass  # quiet


if __name__ == "__main__":
    print(f"IPS/CVE MCP server (exercise) on :{PORT}  auth={'on' if BEARER else 'off'}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
