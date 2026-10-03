#!/usr/bin/env python3
"""
Build-Your-Own-MCP — SCAFFOLD (your task)
=========================================
Turn the Check Point IPS/CVE API into an MCP server the agents in this lab can
call. The API client and the whole Streamable-HTTP/JSON-RPC plumbing are DONE
for you — you only fill in the 5 TODOs, all about *declaring and wiring tools*.

Run it:     python3 ips_cve_mcp.py            (listens on 127.0.0.1:3013)
            Stop it with Ctrl+C and start it again after every edit.
Check it:   python3 ../test_byo_mcp.py ips_cve_mcp.py   (offline self-test; it
            tells you which TODOs still fail — no API key needed)
Try it:     see the guide (curl initialize -> tools/list -> tools/call)
Solution:   ../solution/ips_cve_mcp.py  (peek only if stuck)

Env:
  IPS_CLIENT_ID / IPS_ACCESS_KEY   Check Point portal *Account* API key (client ID +
                                   access key); only needed for real tools/call data
  IPS_REGION                       EU (default) or US — picks the Check Point portal host
  IPS_AUTH_URL / IPS_SERVICE_URL   optional full-URL overrides (set BOTH, same region)
  MCP_HOST                         bind address, default 127.0.0.1 (this machine only)
  MCP_PORT                         default 3013
  MCP_BEARER_TOKEN                 optional; if set, clients must send Authorization: Bearer <token>
  MCP_ALLOWED_ORIGINS              optional comma-separated browser origins to accept
"""
import hmac
import json
import os
import re
import socket
import socketserver
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Protocol revisions this server speaks. initialize echoes the client's version
# when it is in this list, otherwise it answers with the first (newest) one.
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26")

HOST = os.getenv("MCP_HOST") or "127.0.0.1"
PORT = int(os.getenv("MCP_PORT") or "3013")
BEARER = os.getenv("MCP_BEARER_TOKEN", "")
ALLOWED_ORIGINS = {o.strip().rstrip("/") for o in os.getenv("MCP_ALLOWED_ORIGINS", "").split(",") if o.strip()}
MAX_BODY_BYTES = 1024 * 1024

# Check Point portal host per region (same mapping as the lab's other Check Point
# MCP servers). Auth and service URL MUST be on the same regional host.
REGION_HOSTS = {
    "EU": "https://cloudinfra-gw.portal.checkpoint.com",
    "US": "https://cloudinfra-gw-us.portal.checkpoint.com",
}
REGION = (os.getenv("IPS_REGION") or "EU").strip().upper()
_REGION_HOST = REGION_HOSTS.get(REGION, "")
# `or default` (not getenv's 2nd arg) so an empty env value falls back too.
AUTH_URL = (os.getenv("IPS_AUTH_URL") or (_REGION_HOST and _REGION_HOST + "/auth/external")).rstrip("/")
SERVICE_URL = (os.getenv("IPS_SERVICE_URL") or (_REGION_HOST and _REGION_HOST + "/app/ipsinfoapp")).rstrip("/")

CVE_RE = re.compile(r"^CVE-\d{4}-\d{4,}$")


# --------------------------------------------------------------------------- #
# The "library" you are wrapping — already written. Two methods:
#   CLIENT.latest()          -> list of latest IPS protections
#   CLIENT.by_cve(cve_id)    -> list of protections covering that CVE
# (urllib only; the access token is cached for the lifetime the portal reports
# and fetched again once if the API answers 401.)
# --------------------------------------------------------------------------- #
class CveClient:
    def __init__(self):
        self._token = None
        self._exp = 0.0
        self._lock = threading.Lock()

    @staticmethod
    def _http_error(what, err):
        detail = ""
        try:
            body = json.loads(err.read() or b"{}")
            detail = body.get("message") or body.get("error") or ""
        except Exception:
            pass
        return RuntimeError(f"{what} failed: HTTP {err.code}{(' — ' + str(detail)[:200]) if detail else ''}")

    def _auth(self, force=False):
        with self._lock:
            if not force and self._token and time.time() < self._exp:
                return self._token
            cid, key = os.getenv("IPS_CLIENT_ID"), os.getenv("IPS_ACCESS_KEY")
            if not cid or not key:
                raise RuntimeError("IPS_CLIENT_ID / IPS_ACCESS_KEY are not set — add your Check Point portal "
                                   "Account API key to .env (or export it) and restart this server")
            if not AUTH_URL or not SERVICE_URL:
                raise RuntimeError(f"IPS_REGION must be EU or US (got {REGION!r}), "
                                   "or set both IPS_AUTH_URL and IPS_SERVICE_URL")
            body = json.dumps({"clientId": cid, "accessKey": key}).encode()
            req = urllib.request.Request(AUTH_URL, data=body,
                                         headers={"Content-Type": "application/json",
                                                  "Accept": "application/json"}, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    data = json.load(r)
            except urllib.error.HTTPError as e:
                where = "IPS_AUTH_URL" if os.getenv("IPS_AUTH_URL") else f"IPS_REGION={REGION}"
                raise self._http_error(f"Check Point portal authentication at {AUTH_URL} (check that the key is an "
                                       f"Account API key and that {where} matches your tenant's region)", e) from None
            except urllib.error.URLError as e:
                raise RuntimeError(f"cannot reach {AUTH_URL}: {e.reason}") from None
            token = (data.get("data") or {}).get("token") if isinstance(data, dict) else None
            if not token:
                msg = data.get("message") if isinstance(data, dict) else None
                raise RuntimeError(f"Check Point portal authentication returned no token{(': ' + str(msg)[:200]) if msg else ''}")
            try:
                ttl = float((data.get("data") or {}).get("expiresIn") or 0)
            except (TypeError, ValueError):
                ttl = 0.0
            if ttl <= 0:
                ttl = 30 * 60  # portal tokens normally last 30 minutes
            self._token = token
            self._exp = time.time() + max(ttl - 60, ttl / 2)  # renew a little before it expires
            return token

    def _get(self, path, params=None):
        url = f"{SERVICE_URL}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        for attempt in (1, 2):
            token = self._auth(force=attempt == 2)
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}",
                                                       "Accept": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    data = json.load(r)
                return data if isinstance(data, list) else [data]
            except urllib.error.HTTPError as e:
                if e.code == 401 and attempt == 1:
                    continue  # token expired or revoked early: log in again once
                raise self._http_error(f"IPS/CVE API call {path}", e) from None
            except urllib.error.URLError as e:
                raise RuntimeError(f"cannot reach {SERVICE_URL}: {e.reason}") from None

    def latest(self):
        return self._get("/get_latest_publications/")

    def by_cve(self, cve_id):
        cve_id = str(cve_id or "").strip().upper()
        if not CVE_RE.match(cve_id):
            raise ValueError("cve_id must look like CVE-2024-3400")
        return self._get("/protections/by-cve/", {"cve_id": cve_id})


CLIENT = CveClient()

# --------------------------------------------------------------------------- #
# TODO 1 — declare your tools.
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


# --------------------------------------------------------------------------- #
# MCP JSON-RPC dispatch — one request in, one response out. initialize and ping
# are done for you; tools/list and tools/call are yours.
# --------------------------------------------------------------------------- #
def handle_rpc(msg):
    method = msg.get("method")
    mid = msg.get("id")

    if method == "initialize":
        asked = (msg.get("params") or {}).get("protocolVersion")
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": asked if asked in SUPPORTED_PROTOCOL_VERSIONS else SUPPORTED_PROTOCOL_VERSIONS[0],
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "Check Point IPS/CVE (exercise)", "version": "0.1.0"},
        }}

    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}

    if method == "tools/list":
        # TODO 2 — return every tool in TOOLS as
        #   {"name","description","inputSchema"}. The agent calls this first to
        #   discover what it can do.
        tools = []  # <- build this from TOOLS
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": tools}}

    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": "arguments must be an object"}}
        # TODO 3 — look up the tool by name in TOOLS (return a JSON-RPC error
        #             with code -32602 if it isn't found).
        # TODO 4 — call its handler(args); wrap the return value as
        #             {"content": [{"type": "text", "text": <json string>}]}.
        # TODO 5 — on exception, return the same shape with "isError": True
        #             and the error message as text (never let the server crash).
        return {"jsonrpc": "2.0", "id": mid,
                "error": {"code": -32601, "message": "tools/call not implemented yet"}}

    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}}


# --------------------------------------------------------------------------- #
# Streamable HTTP plumbing (POST /mcp) — DONE for you. Don't edit below.
# --------------------------------------------------------------------------- #
def _rpc_error(code, message, mid=None):
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def dispatch(msg):
    """One JSON-RPC message -> response dict, or None for notifications/responses."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return _rpc_error(-32600, "Invalid Request", msg.get("id") if isinstance(msg, dict) else None)
    if "method" not in msg or "id" not in msg:
        # A notification (e.g. notifications/initialized) or a client's reply to a
        # server request: JSON-RPC says the server must not answer these.
        return None
    try:
        return handle_rpc(msg)
    except Exception as e:  # a bug in a handler must not kill the connection
        return _rpc_error(-32603, f"Internal error: {e}", msg.get("id"))


def origin_allowed(origin):
    # Browsers send Origin; server-side MCP clients (gateway, n8n, SDKs) do not.
    # Rejecting foreign origins blocks DNS-rebinding attacks from web pages.
    if not origin:
        return True
    if origin.rstrip("/") in ALLOWED_ORIGINS:
        return True
    try:
        host = urllib.parse.urlsplit(origin).hostname
    except ValueError:
        return False
    return host in ("localhost", "127.0.0.1", "::1")


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, body=b"", ctype=None, headers=None):
        self.send_response(status)
        if ctype:
            self.send_header("Content-Type", ctype)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _send_json(self, status, obj, headers=None):
        self._send(status, json.dumps(obj).encode(), "application/json", headers)

    def _authed(self):
        if not BEARER:
            return True
        return hmac.compare_digest(self.headers.get("Authorization", "").encode(), f"Bearer {BEARER}".encode())

    def _read_body(self):
        if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
            data = b""
            while True:
                size = int(self.rfile.readline().split(b";")[0].strip() or b"0", 16)
                if size == 0:
                    self.rfile.readline()
                    return data
                data += self.rfile.read(size)
                self.rfile.readline()
                if len(data) > MAX_BODY_BYTES:
                    raise ValueError("body too large")
        length = int(self.headers.get("Content-Length") or 0)
        if length < 0 or length > MAX_BODY_BYTES:
            raise ValueError("bad Content-Length")
        return self.rfile.read(length)

    def _check_request(self):
        """Path, Origin and auth checks shared by every method. True = carry on."""
        if self.path.split("?")[0].rstrip("/") not in ("", "/mcp", "/sse"):
            self._send(404)
            return False
        if not origin_allowed(self.headers.get("Origin")):
            self._send_json(403, _rpc_error(-32000, "Forbidden: Origin not allowed"))
            return False
        if not self._authed():
            self._send(401, b"Unauthorized", "text/plain", {"WWW-Authenticate": "Bearer"})
            return False
        return True

    def do_POST(self):
        if not self._check_request():
            return
        try:
            raw = self._read_body()
        except ValueError:
            self._send_json(400, _rpc_error(-32600, "Invalid Request: bad body"))
            return
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json(400, _rpc_error(-32700, "Parse error"))
            return

        batch = isinstance(payload, list)
        msgs = payload if batch else [payload]
        if not msgs:
            self._send_json(400, _rpc_error(-32600, "Invalid Request: empty batch"))
            return
        responses = [r for r in (dispatch(m) for m in msgs) if r is not None]

        session_id = self.headers.get("mcp-session-id")
        if not session_id and any(isinstance(m, dict) and m.get("method") == "initialize" for m in msgs):
            session_id = str(uuid.uuid4())
        headers = {"mcp-session-id": session_id} if session_id else {}

        if not responses:  # only notifications / responses -> 202, no body
            self._send(202, headers=headers)
            return
        accept = self.headers.get("Accept", "")
        if "application/json" in accept and "text/event-stream" not in accept:
            self._send_json(200, responses if batch else responses[0], headers)
            return
        body = "".join(f"event: message\ndata: {json.dumps(r)}\n\n" for r in responses).encode()
        self._send(200, body, "text/event-stream", dict(headers, **{"Cache-Control": "no-cache"}))

    def do_GET(self):
        # Streamable HTTP allows a GET SSE stream for server->client messages;
        # this minimal server is request/response only.
        if self._check_request():
            self._send(405, headers={"Allow": "POST"})

    def do_DELETE(self):
        # Clients may DELETE to end a session; this server keeps no session state,
        # so it answers 405 as the spec allows.
        if self._check_request():
            self._send(405, headers={"Allow": "POST"})

    def log_message(self, *a):
        pass  # quiet


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self):
        # Skip HTTPServer's reverse-DNS lookup of the bind address, which can
        # stall start-up for seconds on hosts or containers without DNS.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


def main():
    if ":" in HOST:  # an IPv6 bind address such as ::1
        Server.address_family = socket.AF_INET6
    srv = Server((HOST, PORT), Handler)
    print(f"IPS/CVE MCP server (scaffold) listening on {HOST}:{PORT}/mcp  auth={'on' if BEARER else 'off'}",
          flush=True)
    print(f"  Check Point portal: region={REGION} auth={AUTH_URL or '<unset>'}  "
          f"credentials={'set' if os.getenv('IPS_CLIENT_ID') and os.getenv('IPS_ACCESS_KEY') else 'NOT set (tool calls will return an error)'}",
          flush=True)
    if HOST not in ("127.0.0.1", "localhost", "::1") and not BEARER:
        print("  note: reachable from other machines/containers without a token — keep it on a private "
              "network or set MCP_BEARER_TOKEN", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
