#!/usr/bin/env python3
"""
Self-test for the Build Your Own MCP exercise. Python standard library only.

Offline mode (no API key, no Docker, no internet). Starts a mock Check Point
portal + IPS/CVE API on 127.0.0.1, loads your server file, and walks the real
protocol over Streamable HTTP: initialize -> notifications/initialized ->
tools/list -> tools/call. Each check names the TODO it proves.

  cd exercises/build-your-own-mcp/scaffold && python3 ../test_byo_mcp.py ips_cve_mcp.py
  cd exercises/build-your-own-mcp/solution && python3 ../test_byo_mcp.py ips_cve_mcp.py

  python3 test_byo_mcp.py --scaffold-check scaffold/ips_cve_mcp.py
      CI guard: the plumbing passes, the file has exactly TODO 1-5, and
      exactly those five checks are still open.

Live mode (from a container on the lab's Docker network; find its name with
`docker network ls`), for example from this folder:
`docker run --rm --network <lab-network> -v "$PWD":/x:ro python:3.12-alpine
python3 /x/test_byo_mcp.py --url http://ips-cve-mcp:3013/mcp`

  --url URL       handshake + tools/list against your running server
  --call CVE_ID   also call ips_protections_by_cve (needs the API key on the server)
  --gateway URL   check that the MCP gateway lists both tools after you registered
                  the server (Bearer token from MCP_GATEWAY_TOKEN; never printed)

Exit code: 0 = all checks passed, 1 = a check failed, 2 = usage error.
"""
import argparse
import http.client
import importlib.util
import json
import os
import re
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer

EXPECTED = ("ips_latest_protections", "ips_protections_by_cve")
TEST_CLIENT_ID = "test-client-id"
TEST_ACCESS_KEY = "test-access-key-not-a-secret"
LATEST = [{"protection_name": "Test Protection A", "severity": "High", "cve": ["CVE-2024-0001"]},
          {"protection_name": "Test Protection B", "severity": "Medium", "cve": []}]
BY_CVE = [{"protection_name": "Palo Alto PAN-OS GlobalProtect Command Injection (CVE-2024-3400)",
           "cve": ["CVE-2024-3400"], "severity": "Critical"}]


# --------------------------------------------------------------------------- #
# Minimal MCP client (http.client, so the test controls every header)          #
# --------------------------------------------------------------------------- #
class Mcp:
    def __init__(self, url, token=None, timeout=30):
        u = urllib.parse.urlsplit(url)
        if u.scheme not in ("http", "https"):
            raise ValueError(f"not an http(s) URL: {url}")
        self.https = u.scheme == "https"
        self.host, self.port = u.hostname, u.port or (443 if self.https else 80)
        self.path = u.path or "/"
        self.token, self.timeout, self.sid = token, timeout, None

    def raw(self, method, body=None, headers=None):
        cls = http.client.HTTPSConnection if self.https else http.client.HTTPConnection
        conn = cls(self.host, self.port, timeout=self.timeout)
        h = {"Accept": "application/json, text/event-stream"}
        if body is not None:
            h["Content-Type"] = "application/json"
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        if self.sid:
            h["mcp-session-id"] = self.sid
        h.update(headers or {})
        data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
        try:
            conn.request(method, self.path, body=data, headers=h)
            resp = conn.getresponse()
            return resp.status, {k.lower(): v for k, v in resp.getheaders()}, resp.read().decode("utf-8", "replace")
        finally:
            conn.close()

    @staticmethod
    def parse(headers, text):
        """JSON body, or the last JSON-RPC response in an SSE stream (events split on blank lines)."""
        if "text/event-stream" not in headers.get("content-type", ""):
            return json.loads(text)
        found = None
        for event in re.split(r"\r?\n\r?\n", text):
            data = "\n".join(line[5:].lstrip() for line in event.splitlines() if line.startswith("data:"))
            if data:
                msg = json.loads(data)
                if isinstance(msg, list) or "result" in msg or "error" in msg:
                    found = msg
        if found is None:
            raise ValueError("no JSON-RPC response in the SSE stream")
        return found

    def rpc(self, method, params=None, mid=1, headers=None):
        msg = {"jsonrpc": "2.0", "id": mid, "method": method}
        if params is not None:
            msg["params"] = params
        status, h, text = self.raw("POST", msg, headers)
        if status != 200:
            raise AssertionError(f"{method}: HTTP {status} {text[:160]}")
        if h.get("mcp-session-id"):
            self.sid = h["mcp-session-id"]
        return self.parse(h, text)

    def handshake(self):
        self.sid = None
        init = self.rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                       "clientInfo": {"name": "byo-mcp-self-test", "version": "1"}})
        if "result" not in init:
            raise AssertionError(f"initialize failed: {init.get('error')}")
        status = self.raw("POST", {"jsonrpc": "2.0", "method": "notifications/initialized"})[0]
        if status not in (200, 202):
            raise AssertionError(f"notifications/initialized: HTTP {status}")
        return init

    def tools(self):
        reply = self.rpc("tools/list", mid=2)
        if "error" in reply:
            raise AssertionError(f"tools/list error: {reply['error']}")
        return reply["result"].get("tools", [])

    def call(self, name, arguments):
        return self.rpc("tools/call", {"name": name, "arguments": arguments}, mid=3)

    def close(self):
        if self.sid:
            try:
                self.raw("DELETE")
            except OSError:
                pass
            self.sid = None


# --------------------------------------------------------------------------- #
# Mock Check Point portal (auth) + IPS/CVE API, 127.0.0.1 only                    #
# --------------------------------------------------------------------------- #
class MockApi:
    def __init__(self):
        self.tokens_issued = 0
        self.revoke_next = False   # answer the next API call with 401 (token revoked early)
        self.fail_next = False     # answer the next API call with 500
        self.seen = []             # (path, query, authorization) of every API call
        api = self

        class H(BaseHTTPRequestHandler):
            def _json(self, status, obj):
                body = json.dumps(obj).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                if self.path != "/auth/external":
                    return self._json(404, {"message": "not found"})
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                if body.get("clientId") != TEST_CLIENT_ID or body.get("accessKey") != TEST_ACCESS_KEY:
                    return self._json(401, {"success": False, "message": "Authentication failed"})
                api.tokens_issued += 1
                self._json(200, {"success": True, "data": {"token": f"tok-{api.tokens_issued}", "expiresIn": 1800}})

            def do_GET(self):
                u = urllib.parse.urlsplit(self.path)
                auth = self.headers.get("Authorization", "")
                api.seen.append((u.path, urllib.parse.parse_qs(u.query), auth))
                if auth != f"Bearer tok-{api.tokens_issued}" or api.revoke_next:
                    api.revoke_next = False
                    return self._json(401, {"message": "token expired"})
                if api.fail_next:
                    api.fail_next = False
                    return self._json(500, {"message": "upstream unavailable"})
                if u.path == "/app/ipsinfoapp/get_latest_publications/":
                    return self._json(200, LATEST)
                if u.path == "/app/ipsinfoapp/protections/by-cve/":
                    cve = (urllib.parse.parse_qs(u.query).get("cve_id") or [""])[0]
                    return self._json(200, BY_CVE if cve == "CVE-2024-3400" else [])
                self._json(404, {"message": "not found"})

            def log_message(self, *a):
                pass

        self.server = _QuietServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


class _QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self):  # no reverse-DNS lookup (stalls without DNS)
        super(HTTPServer, self).server_bind()
        self.server_name, self.server_port = "127.0.0.1", self.server_address[1]


def load_server(path, api, bearer=""):
    os.environ.update({"IPS_CLIENT_ID": TEST_CLIENT_ID, "IPS_ACCESS_KEY": TEST_ACCESS_KEY,
                       "IPS_AUTH_URL": api.base + "/auth/external",
                       "IPS_SERVICE_URL": api.base + "/app/ipsinfoapp",
                       "MCP_HOST": "127.0.0.1", "MCP_BEARER_TOKEN": bearer})
    os.environ.pop("MCP_ALLOWED_ORIGINS", None)
    name = f"byo_server_under_test_{abs(hash((path, bearer)))}"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    server_cls = getattr(mod, "Server", ThreadingHTTPServer)
    srv = server_cls(("127.0.0.1", 0), mod.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return mod, srv, f"http://127.0.0.1:{srv.server_address[1]}/mcp"


# --------------------------------------------------------------------------- #
# Check runner                                                                 #
# --------------------------------------------------------------------------- #
class Checks:
    def __init__(self):
        self.rows = []

    def run(self, todo, name, fn, hint=""):
        try:
            detail = fn() or ""
            self.rows.append(("PASS", todo, name, str(detail)))
        except Exception as e:  # report every check, then decide at the end
            msg = f"{type(e).__name__}: {e}" if not isinstance(e, AssertionError) else str(e)
            self.rows.append(("FAIL", todo, name, (msg + (f"  -> {hint}" if hint else ""))[:300]))

    def report(self):
        w = max(len(r[2]) for r in self.rows)
        for result, todo, name, detail in self.rows:
            print(f"{result}  {todo:<9} {name.ljust(w)}  {detail}")
        failed = [r for r in self.rows if r[0] == "FAIL"]
        print(f"\n{len(self.rows) - len(failed)}/{len(self.rows)} checks passed")
        return failed


def _eq(got, want, what):
    if got != want:
        raise AssertionError(f"{what}: got {got!r}, want {want!r}")


def offline(path):
    if not os.path.isfile(path):
        print(f"no such file: {path}", file=sys.stderr)
        return None
    outbound = []

    def audit(event, args):  # prove the IPS API is mocked: no connection leaves 127.0.0.1
        if event == "socket.connect" and "127.0.0.1" not in repr(args[1]) and "localhost" not in repr(args[1]):
            outbound.append(repr(args[1])[:80])
    sys.addaudithook(audit)

    api = MockApi()
    mod, srv, url = load_server(path, api)
    c = Checks()
    m = Mcp(url)

    def initialize():
        init = m.handshake()
        res = init["result"]
        assert res.get("protocolVersion"), "initialize returned no protocolVersion"
        assert m.sid, "no mcp-session-id response header"
        return f"protocolVersion {res['protocolVersion']}, session id issued"
    c.run("plumbing", "initialize + notifications/initialized", initialize)
    c.run("plumbing", "ping", lambda: _eq(m.rpc("ping", mid=9).get("result"), {}, "ping result"))

    def todo1():
        tools = getattr(mod, "TOOLS", {})
        missing = [n for n in EXPECTED if n not in tools]
        assert not missing, f"TOOLS is missing {missing}"
        for n in EXPECTED:
            t = tools[n]
            assert isinstance(t.get("description"), str) and t["description"].strip(), f"{n}: empty description"
            assert (t.get("inputSchema") or {}).get("type") == "object", f"{n}: inputSchema must be a JSON Schema object"
            assert callable(t.get("handler")), f"{n}: handler is not callable"
        assert "cve_id" in (tools["ips_protections_by_cve"]["inputSchema"].get("required") or []), \
            "ips_protections_by_cve must require cve_id"
        return "2 tools declared"
    c.run("TODO 1", "declare both tools in TOOLS", todo1, "see the TODO 1 block")

    def todo2():
        tools = m.tools()
        names = sorted(t.get("name") for t in tools)
        _eq(names, sorted(EXPECTED), "tools/list names")
        for t in tools:
            assert set(t) >= {"name", "description", "inputSchema"}, f"{t.get('name')}: missing keys"
            assert "handler" not in t, "do not send the handler to the client"
        return ", ".join(names)
    c.run("TODO 2", "tools/list returns name/description/inputSchema", todo2, "build the list from TOOLS")

    def todo3():
        reply = m.call("no_such_tool", {})
        _eq((reply.get("error") or {}).get("code"), -32602, "error code for an unknown tool")
        return "unknown tool -> -32602"
    c.run("TODO 3", "unknown tool -> JSON-RPC error -32602", todo3, "return the error, do not raise")

    def todo4():
        reply = m.call("ips_latest_protections", {})
        res = reply.get("result") or {}
        assert not res.get("isError"), f"tool error: {res}"
        _eq(json.loads(res["content"][0]["text"]), LATEST, "ips_latest_protections data")
        reply = m.call("ips_protections_by_cve", {"cve_id": "CVE-2024-3400"})
        res = reply.get("result") or {}
        assert not res.get("isError"), f"tool error: {res}"
        _eq(res["content"][0]["type"], "text", "content type")
        _eq(json.loads(res["content"][0]["text"]), BY_CVE, "ips_protections_by_cve data")
        path, query, auth = api.seen[-1]
        _eq(query.get("cve_id"), ["CVE-2024-3400"], "cve_id sent to the API")
        assert auth.startswith("Bearer tok-"), "API call carried no portal token"
        return "both tools return the API data as text content"
    c.run("TODO 4", "tools/call runs the handler, wraps the result", todo4, "content=[{type:text,text:json}]")

    def todo5():
        res = (m.call("ips_protections_by_cve", {"cve_id": "not-a-cve"}).get("result") or {})
        assert res.get("isError") is True, f"bad cve_id should give isError: true, got {res or 'an error reply'}"
        api.fail_next = True
        res = (m.call("ips_latest_protections", {}).get("result") or {})
        assert res.get("isError") is True, "an API failure should give isError: true"
        text = json.dumps(res)
        assert TEST_ACCESS_KEY not in text, "the access key leaked into a tool result"
        _eq(m.rpc("ping", mid=10).get("result"), {}, "server still answers after errors")
        return "errors become isError results; server stays up"
    c.run("TODO 5", "handler errors -> isError result, no crash", todo5, "wrap the handler call in try/except")

    def reauth():  # the provided API client, called directly (independent of the TODOs)
        mod.CLIENT.latest()  # make sure a token is cached
        before = api.tokens_issued
        api.revoke_next = True
        _eq(mod.CLIENT.latest(), LATEST, "data after a 401")
        _eq(api.tokens_issued, before + 1, "logins after a 401")
        return "one re-login after HTTP 401"
    c.run("client", "API client logs in again once on 401", reauth)

    def accept_json():
        status, h, text = m.raw("POST", {"jsonrpc": "2.0", "id": 4, "method": "ping"}, {"Accept": "application/json"})
        _eq(status, 200, "HTTP status")
        if "application/json" not in h.get("content-type", ""):
            raise AssertionError(f"Accept: application/json answered with {h.get('content-type')}")
        return "JSON reply when the client accepts only JSON"
    c.run("plumbing", "Accept: application/json", accept_json)

    def origin():
        _eq(m.raw("POST", {"jsonrpc": "2.0", "id": 5, "method": "ping"}, {"Origin": "https://attacker.example"})[0],
            403, "foreign Origin")
        _eq(m.raw("POST", {"jsonrpc": "2.0", "id": 6, "method": "ping"}, {"Origin": "http://localhost:5678"})[0],
            200, "localhost Origin")
        return "foreign browser origins get 403 (DNS-rebinding guard)"
    c.run("plumbing", "Origin check", origin)

    def notifications():
        status, _, body = m.raw("POST", {"jsonrpc": "2.0", "method": "notifications/cancelled",
                                         "params": {"requestId": 1}})
        _eq((status, body), (202, ""), "notification reply")
    c.run("plumbing", "notifications get 202 and no body", notifications)

    def bearer():
        _, srv2, url2 = load_server(path, api, bearer="test-bearer")
        try:
            _eq(Mcp(url2).raw("POST", {"jsonrpc": "2.0", "id": 1, "method": "ping"})[0], 401, "no token")
            _eq(Mcp(url2, token="test-bearer").rpc("ping").get("result"), {}, "with token")
        finally:
            srv2.shutdown()
        return "MCP_BEARER_TOKEN enforced when set"
    c.run("plumbing", "optional Bearer token", bearer)

    c.run("mock", "no connection left 127.0.0.1", lambda: _eq(outbound, [], "outbound connections"))
    m.close()
    srv.shutdown()
    api.server.shutdown()
    return c


def scaffold_check(path):
    with open(path, encoding="utf-8") as f:
        markers = sorted(set(int(n) for n in re.findall(r"#\s*TODO (\d)\b", f.read())))
    c = offline(path)
    if c is None:
        return 2
    failed = c.report()
    open_todos = sorted(r[1] for r in failed)
    problems = []
    if markers != [1, 2, 3, 4, 5]:
        problems.append(f"TODO markers in the file are {markers}, the guide describes TODO 1-5")
    if open_todos != [f"TODO {n}" for n in range(1, 6)]:
        problems.append(f"open checks are {open_todos}, expected exactly TODO 1-5 (plumbing must pass)")
    for p in problems:
        print(f"FAIL  scaffold: {p}")
    print("scaffold check: " + ("OK" if not problems else "FAILED"))
    return 1 if problems else 0


def live(url, cve, gateway):
    c = Checks()
    if url:
        m = Mcp(url)
        c.run("live", f"initialize {url}", lambda: m.handshake() and f"session {'issued' if m.sid else 'none'}")

        def listing():
            names = sorted(t.get("name") for t in m.tools())
            _eq(names, sorted(EXPECTED), "tools/list names")
            return ", ".join(names)
        c.run("live", "tools/list", listing, "finish TODO 1 and 2, rebuild the image")
        if cve:
            def call():
                res = m.call("ips_protections_by_cve", {"cve_id": cve}).get("result") or {}
                text = res.get("content", [{}])[0].get("text", "")
                assert not res.get("isError"), text[:200]
                return f"{len(json.loads(text))} protection(s) for {cve}"
            c.run("live", "tools/call ips_protections_by_cve", call, "check IPS_CLIENT_ID / IPS_ACCESS_KEY / IPS_REGION")
        m.close()
    if gateway:
        token = os.getenv("MCP_GATEWAY_TOKEN", "")
        g = Mcp(gateway, token=token or None, timeout=60)

        def registered():
            if not token:
                raise AssertionError("MCP_GATEWAY_TOKEN is not set in this shell/container")
            g.handshake()
            names = [t.get("name", "") for t in g.tools()]
            found = [n for n in names if any(n == e or n.endswith(e) for e in EXPECTED)]
            assert len(found) == 2, f"gateway lists {len(names)} tools but not both exercise tools"
            return f"{len(names)} gateway tools, including {', '.join(found)}"
        c.run("gateway", "exercise tools listed by the gateway", registered,
              "catalog entry + --servers + docker compose up -d mcp-gateway")
        g.close()
    return 1 if c.report() else 0


def main():
    p = argparse.ArgumentParser(description="Build Your Own MCP self-test")
    p.add_argument("server", nargs="?", help="server file to test offline (e.g. ips_cve_mcp.py)")
    p.add_argument("--scaffold-check", action="store_true", help="assert the scaffold still has exactly TODO 1-5 open")
    p.add_argument("--url", help="live: your running server, e.g. http://ips-cve-mcp:3013/mcp")
    p.add_argument("--call", metavar="CVE_ID", help="live: also call ips_protections_by_cve")
    p.add_argument("--gateway", help="live: the gateway, e.g. http://mcp-gateway:8080/mcp")
    a = p.parse_args()
    if a.url or a.gateway:
        return live(a.url, a.call, a.gateway)
    if not a.server:
        p.print_usage(sys.stderr)
        return 2
    if a.scaffold_check:
        return scaffold_check(a.server)
    c = offline(a.server)
    if c is None:
        return 2
    failed = c.report()
    todos = sorted({r[1] for r in failed if r[1].startswith("TODO")})
    if todos:
        print("Still open: " + ", ".join(todos) + ". Edit the file, then run this test again.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
