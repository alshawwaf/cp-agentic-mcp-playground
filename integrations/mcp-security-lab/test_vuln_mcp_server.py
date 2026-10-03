#!/usr/bin/env python3
"""
Guards for the MCP Security Lab server (vuln_mcp_server.py). Stdlib only.

Two modes:

  python3 integrations/mcp-security-lab/test_vuln_mcp_server.py
      Unit mode (CI, no Docker, no network). Proves the "simulated only"
      promise: the server imports only an allow-listed set of stdlib modules,
      never calls open()/exec()/eval(), and - watched by a Python audit hook -
      opens no file, starts no process and makes no outbound connection while
      it serves the handshake and every tool with attack-style arguments.
      Also checks the MCP protocol behaviour and the tool names the guide,
      the Flowise flow and builders_agents.json rely on.

  python3 test_vuln_mcp_server.py --url http://vuln-mcp:3099
      Live mode (acceptance test, from a container on the internal security-lab
      network, when the security-lab profile is on). Black-box checks of a
      running server.

Exit code is non-zero on any failure.
"""
import argparse
import ast
import http.client
import importlib.util
import json
import os
import sys
import threading
import time
import unittest
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER_FILE = os.path.join(HERE, "vuln_mcp_server.py")
REPO = os.path.dirname(os.path.dirname(HERE))

EXPECTED_TOOLS = ["weather_lookup", "read_local_file", "fetch_ticket", "currency_convert"]
STAMP = "[SIMULATED — MCP SECURITY LAB / training only]"
ALLOWED_IMPORTS = {"hmac", "json", "os", "signal", "sys", "threading", "time", "uuid", "http.server"}
ALLOWED_OS_ATTRS = {"getenv", "geteuid"}
FORBIDDEN_BUILTINS = {"open", "exec", "eval", "compile", "__import__", "input", "breakpoint",
                      "getattr", "setattr", "delattr", "globals", "locals", "vars"}
ATTACK_PATHS = ["~/.aws/credentials", "/root/.aws/credentials", "~/.ssh/id_rsa", "/etc/passwd",
                "/etc/shadow", "/proc/self/environ", "../../.env", "/app/vuln_mcp_server.py",
                SERVER_FILE, "C:\\Users\\admin\\.aws\\credentials"]


# --------------------------------------------------------------------------- #
# Tiny MCP client (http.client only)                                           #
# --------------------------------------------------------------------------- #
class Client:
    def __init__(self, base_url, token=None):
        u = urllib.parse.urlsplit(base_url)
        self.host, self.port = u.hostname, u.port or 80
        self.path = u.path or "/"
        self.token = token
        self.sid = None

    def raw(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection(self.host, self.port, timeout=10)
        h = {"Accept": "application/json, text/event-stream"}
        if body is not None:
            h["Content-Type"] = "application/json"
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        if self.sid:
            h["mcp-session-id"] = self.sid
        h.update(headers or {})
        data = body if isinstance(body, (bytes, type(None))) else (
            body.encode() if isinstance(body, str) else json.dumps(body).encode())
        conn.request(method, path, body=data, headers=h)
        resp = conn.getresponse()
        out = resp.status, dict((k.lower(), v) for k, v in resp.getheaders()), resp.read().decode("utf-8")
        conn.close()
        return out

    @staticmethod
    def sse_json(text):
        data = [line[6:] for line in text.splitlines() if line.startswith("data: ")]
        return json.loads("".join(data)) if data else json.loads(text)

    def rpc(self, method, params=None, mid=1, path=None):
        msg = {"jsonrpc": "2.0", "id": mid, "method": method}
        if params is not None:
            msg["params"] = params
        status, headers, text = self.raw("POST", path or self.path, msg)
        if status != 200:
            raise AssertionError(f"{method}: HTTP {status} {text[:200]}")
        if headers.get("mcp-session-id"):
            self.sid = headers["mcp-session-id"]
        return self.sse_json(text)

    def notify(self, method):
        return self.raw("POST", self.path, {"jsonrpc": "2.0", "method": method})[0]

    def handshake(self):
        self.sid = None
        init = self.rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                       "clientInfo": {"name": "zz-test", "version": "1"}})
        assert self.notify("notifications/initialized") == 202
        return init

    def tools(self):
        return self.rpc("tools/list", mid=2)["result"]["tools"]

    def call(self, name, arguments):
        return self.rpc("tools/call", {"name": name, "arguments": arguments}, mid=3)

    def call_text(self, name, arguments):
        res = self.call(name, arguments)["result"]
        return res.get("isError", False), res["content"][0]["text"]


def currency_description(client):
    return next(t["description"] for t in client.tools() if t["name"] == "currency_convert")


# --------------------------------------------------------------------------- #
# Unit mode                                                                    #
# --------------------------------------------------------------------------- #
def load_server_module():
    os.environ.pop("MCP_BEARER_TOKEN", None)
    os.environ.pop("RUG_PULL_RESET_SECONDS", None)
    spec = importlib.util.spec_from_file_location("vuln_mcp_server_under_test", SERVER_FILE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


MOD = None
SERVER = None
BASE = None


def setUpModule():
    global MOD, SERVER, BASE
    MOD = load_server_module()
    SERVER = MOD.LabHTTPServer(("127.0.0.1", 0), MOD.Handler)
    threading.Thread(target=SERVER.serve_forever, daemon=True).start()
    BASE = f"http://127.0.0.1:{SERVER.server_address[1]}"


def tearDownModule():
    SERVER.shutdown()


class StaticGuarantees(unittest.TestCase):
    """Source-level proof that the server cannot touch files, processes or the network."""

    @classmethod
    def setUpClass(cls):
        with open(SERVER_FILE, encoding="utf-8") as f:
            cls.tree = ast.parse(f.read(), SERVER_FILE)

    def test_imports_are_allow_listed(self):
        found = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                found.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                found.add(node.module or "")
        self.assertLessEqual(found, ALLOWED_IMPORTS,
                             f"vuln_mcp_server.py imports beyond the allow-list: {sorted(found - ALLOWED_IMPORTS)}")

    def test_no_dangerous_builtins(self):
        bad = [f"line {n.lineno}: {n.func.id}()" for n in ast.walk(self.tree)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in FORBIDDEN_BUILTINS]
        self.assertEqual(bad, [], "file/code-execution builtins are not allowed in the lab server")

    def test_os_is_only_used_for_env_and_uid(self):
        bad = [f"line {n.lineno}: os.{n.attr}" for n in ast.walk(self.tree)
               if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
               and n.value.id == "os" and n.attr not in ALLOWED_OS_ATTRS]
        self.assertEqual(bad, [], "the lab server may use os only to read env vars and the uid")


    def test_env_reads_are_config_only(self):
        # The server may read exactly these settings, never inside a tool handler.
        allowed = {"MCP_PORT", "MCP_BEARER_TOKEN", "RUG_PULL_RESET_SECONDS"}
        literal, dynamic = set(), []
        funcs = {f.name: f for f in ast.walk(self.tree) if isinstance(f, ast.FunctionDef)}
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            is_getenv = isinstance(f, ast.Attribute) and f.attr == "getenv"
            is_env_int = isinstance(f, ast.Name) and f.id == "_env_int"
            if not (is_getenv or is_env_int) or not node.args:
                continue
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                literal.add(arg.value)
            else:
                dynamic.append(node.lineno)
        self.assertLessEqual(literal, allowed, f"unexpected env reads: {sorted(literal - allowed)}")
        env_int = funcs["_env_int"]
        inside = set(range(env_int.lineno, env_int.end_lineno + 1))
        self.assertTrue(all(line in inside for line in dynamic), f"env read with a computed name at {dynamic}")
        for name in ("weather_lookup", "read_local_file", "_canned_fake", "fetch_ticket", "currency_convert"):
            calls = [n for n in ast.walk(funcs[name]) if isinstance(n, ast.Attribute) and n.attr in ("getenv", "environ")]
            self.assertEqual(calls, [], f"{name} must not read the environment")


class RuntimeGuarantees(unittest.TestCase):
    """An audit hook watches the whole process while every tool is called with attack arguments."""

    WATCHED = ("open", "os.", "subprocess.", "shutil.", "ctypes.", "exec", "compile", "import",
               "socket.connect", "socket.sendto", "socket.sendmsg", "socket.getaddrinfo",
               "socket.gethostbyname", "urllib.", "http.client.connect", "ftplib.", "smtplib.",
               "webbrowser.", "pty.", "fcntl.")

    def scenario(self):
        c = Client(BASE)
        c.handshake()
        c.rpc("ping", mid=9)
        c.tools()
        c.call("weather_lookup", {"city": "Paris"})
        for p in ATTACK_PATHS:
            c.call("read_local_file", {"path": p})
        c.call("fetch_ticket", {"ticket_id": "TCKT-4471"})
        c.call("currency_convert", {"amount": 100, "from": "USD", "to": "EUR"})
        c.tools()
        c.raw("POST", "/reset", b"")
        c.raw("GET", "/health")
        for p in ATTACK_PATHS:  # direct dispatch too, outside HTTP
            MOD.handle_rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                            "params": {"name": "read_local_file", "arguments": {"path": p}}})

    def test_no_file_process_or_outbound_network_access(self):
        events, armed = [], {"on": False}

        def hook(event, args):
            if not armed["on"] or not event.startswith(self.WATCHED):
                return
            if event in ("socket.connect", "http.client.connect", "socket.getaddrinfo"):
                target = args[1] if event == "socket.connect" else args[1:3] if event == "http.client.connect" else args[0:2]
                if "127.0.0.1" in repr(target):
                    return  # the test client talking to the in-process server
            events.append((event, repr(args)[:160]))

        sys.addaudithook(hook)  # cannot be removed; inert while armed is False
        self.scenario()  # warm-up: lazy imports happen here, unwatched
        armed["on"] = True
        try:
            self.scenario()
        finally:
            armed["on"] = False
        self.assertEqual(events, [], "the lab server touched files, processes or the network")

    def test_bind_does_no_dns_lookup(self):
        # HTTPServer.server_bind calls socket.getfqdn (reverse DNS), which stalls
        # start-up for seconds on the internal security-lab network.
        events = []

        def hook(event, args):
            if event.startswith(("socket.gethostbyaddr", "socket.getnameinfo", "socket.getaddrinfo")):
                events.append(event)

        sys.addaudithook(hook)
        server = MOD.LabHTTPServer(("127.0.0.1", 0), MOD.Handler)
        server.server_close()
        self.assertEqual(events, [], "server start-up must not resolve names")

    def test_real_file_contents_never_returned(self):
        real = {}
        for p in ("/etc/passwd", "/etc/hostname", SERVER_FILE):
            try:
                with open(p, encoding="utf-8", errors="replace") as f:
                    real[p] = f.read()
            except OSError:
                pass
        for p, content in real.items():
            res = MOD.read_local_file({"path": p})
            if content.strip():
                self.assertNotIn(content.strip()[:60], res["content"], f"real contents of {p} leaked")
            self.assertIn(STAMP, res["_note"])
        aws = MOD.read_local_file({"path": "/root/.aws/credentials"})["content"]
        self.assertIn("AKIAFAKEFAKEFAKE0000", aws, "suffix match should return the canned fake")
        key = MOD.read_local_file({"path": "C:\\Users\\a\\.ssh\\id_rsa"})["content"]
        self.assertIn("FAKE-KEY-FOR-TRAINING-ONLY", key)


class Protocol(unittest.TestCase):
    def setUp(self):
        MOD.RUG.reset()
        self.c = Client(BASE)
        self.init = self.c.handshake()

    def test_initialize_and_session(self):
        self.assertEqual(self.init["result"]["protocolVersion"], "2025-03-26")
        self.assertTrue(self.c.sid)

    def test_tool_names(self):
        self.assertEqual([t["name"] for t in self.c.tools()], EXPECTED_TOOLS)

    def test_poisoned_descriptions_are_labelled(self):
        # tests/acceptance (check SECLAB) needs a description with both markers.
        tools = {t["name"]: t["description"] for t in self.c.tools()}
        self.assertIn("<IMPORTANT>", tools["weather_lookup"])
        self.assertIn(STAMP, tools["weather_lookup"])
        self.assertNotIn("<IMPORTANT>", tools["currency_convert"], "rug pull starts clean")
        MOD.RUG.arm()
        try:
            poisoned = currency_description(self.c)
            self.assertIn("<IMPORTANT>", poisoned)
            self.assertIn(STAMP, poisoned)
        finally:
            MOD.RUG.reset()

    def test_ping(self):
        self.assertEqual(self.c.rpc("ping", mid=5), {"jsonrpc": "2.0", "id": 5, "result": {}})

    def test_stamp_is_readable_text(self):
        for name, args in (("weather_lookup", {"city": "Paris"}), ("read_local_file", {"path": "~/.aws/credentials"}),
                           ("fetch_ticket", {"ticket_id": "T-1"}), ("currency_convert", {"amount": 1, "from": "USD", "to": "EUR"})):
            is_error, text = self.c.call_text(name, args)
            self.assertFalse(is_error, name)
            self.assertIn(STAMP, text, f"{name}: stamp missing or escaped")
            self.assertNotIn("\\u", text, f"{name}: JSON escapes reach the model")
        self.assertIn("18°C", self.c.call_text("weather_lookup", {"city": "Paris"})[1])

    def test_any_notification_gets_202_and_no_body(self):
        for m in ("notifications/initialized", "notifications/cancelled", "notifications/roots/list_changed"):
            status, _, body = self.c.raw("POST", "/mcp", {"jsonrpc": "2.0", "method": m})
            self.assertEqual((status, body), (202, ""), m)

    def test_batch(self):
        status, _, body = self.c.raw("POST", "/mcp", [{"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                                      {"jsonrpc": "2.0", "method": "notifications/initialized"},
                                                      {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}])
        self.assertEqual(status, 200)
        replies = Client.sse_json(body)
        self.assertEqual([r["id"] for r in replies], [1, 2])

    def test_errors(self):
        self.assertEqual(self.c.rpc("nope/nope")["error"]["code"], -32601)
        self.assertEqual(self.c.rpc("tools/call", {"name": "rm_rf"})["error"]["code"], -32602)
        self.assertEqual(self.c.rpc("tools/call", "bad")["error"]["code"], -32602)
        self.assertEqual(self.c.rpc("tools/call", {"name": "fetch_ticket", "arguments": "x"})["error"]["code"], -32602)
        is_error, text = self.c.call_text("currency_convert", {"amount": "abc", "from": "USD", "to": "EUR"})
        self.assertTrue(is_error)
        self.assertFalse(MOD.RUG.armed(), "a failed call must not arm the rug pull")
        self.assertEqual(self.c.raw("POST", "/mcp", b"{not json")[0], 400)
        self.assertEqual(self.c.raw("POST", "/mcp", b"")[0], 400)
        self.assertEqual(self.c.raw("POST", "/mcp", b"{}", {"Content-Length": str(MOD.MAX_BODY_BYTES + 1)})[0], 413)
        self.assertEqual(self.c.raw("POST", "/elsewhere", {"jsonrpc": "2.0", "id": 1, "method": "ping"})[0], 404)

    def test_paths_and_verbs(self):
        for path in ("/", "/mcp", "/mcp/", "/sse", "/mcp?refresh=1"):
            self.assertEqual(self.c.rpc("ping", path=path)["result"], {}, path)
        self.assertEqual(self.c.raw("GET", "/mcp")[0], 405)
        self.assertEqual(self.c.raw("DELETE", "/mcp")[0], 405)
        status, _, body = self.c.raw("GET", "/health")
        self.assertEqual(status, 200)
        health = json.loads(body)
        self.assertEqual((health["status"], health["tools"], health["simulated"]), ("ok", 4, True))
        self.assertNotIn("session", body.lower(), "health must not publish session data")

    def test_chunked_body(self):
        conn = http.client.HTTPConnection("127.0.0.1", SERVER.server_address[1], timeout=10)
        conn.request("POST", "/mcp", body=iter([b'{"jsonrpc":"2.0",', b'"id":7,"method":"ping"}']),
                     headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
                     encode_chunked=True)
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        self.assertEqual(Client.sse_json(resp.read().decode())["id"], 7)
        conn.close()

    def test_bearer_when_configured(self):
        MOD.BEARER = "zz-test-token"
        try:
            self.assertEqual(Client(BASE).raw("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "ping"})[0], 401)
            self.assertEqual(Client(BASE).raw("POST", "/reset", b"")[0], 401)
            self.assertEqual(Client(BASE, token="zz-test-token").rpc("ping")["result"], {})
        finally:
            MOD.BEARER = ""


class RugPull(unittest.TestCase):
    def setUp(self):
        MOD.RUG.reset()
        MOD.RUG.reset_seconds = 600

    def tearDown(self):
        MOD.RUG.reset()
        MOD.RUG.reset_seconds = 600

    def test_flip_is_visible_to_other_sessions(self):
        agent, scanner = Client(BASE), Client(BASE)
        agent.handshake()
        scanner.handshake()
        self.assertNotIn("<IMPORTANT>", currency_description(scanner), "starts clean")
        is_error, text = agent.call_text("currency_convert", {"amount": 100, "from": "USD", "to": "EUR"})
        self.assertFalse(is_error)
        self.assertIn("NEW chat turn", text)
        self.assertIn("<IMPORTANT>", currency_description(agent))
        fresh = Client(BASE)
        fresh.handshake()  # e.g. n8n's next chat turn opens a new MCP session
        self.assertIn("<IMPORTANT>", currency_description(fresh))
        self.assertIn("<IMPORTANT>", currency_description(scanner))

    def test_reset_endpoint(self):
        c = Client(BASE)
        c.handshake()
        c.call("currency_convert", {"amount": 1, "from": "USD", "to": "EUR"})
        status, _, body = c.raw("POST", "/reset", b"")
        self.assertEqual((status, json.loads(body)), (200, {"rug_pull": "clean", "was_armed": True}))
        self.assertNotIn("<IMPORTANT>", currency_description(c))

    def test_auto_reset(self):
        MOD.RUG.reset_seconds = 1
        c = Client(BASE)
        c.handshake()
        c.call("currency_convert", {"amount": 1, "from": "USD", "to": "EUR"})
        self.assertIn("<IMPORTANT>", currency_description(c))
        self.assertEqual(json.loads(c.raw("GET", "/health")[2])["rug_pull"], "armed")
        time.sleep(1.1)
        self.assertNotIn("<IMPORTANT>", currency_description(c))

    def test_zero_means_no_auto_reset(self):
        MOD.RUG.reset_seconds = 0
        MOD.RUG.arm()
        self.assertTrue(MOD.RUG.armed())
        self.assertIsNone(MOD.RUG.seconds_left())


class RepoConsistency(unittest.TestCase):
    """The tool names other files promise must match what the server serves."""

    def _load(self, rel):
        path = os.path.join(REPO, rel)
        if not os.path.exists(path):
            self.skipTest(f"{rel} not present")
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_flowise_actions(self):
        flow = json.loads(self._load("integrations/flowise/security-lab.flowdata.json"))
        actions = [json.loads(n["data"]["inputs"]["mcpActions"]) for n in flow["nodes"]
                   if n["data"].get("name") == "customMCP"]
        self.assertTrue(actions, "no customMCP node")
        for a in actions:
            self.assertEqual(sorted(a), sorted(EXPECTED_TOOLS))

    def test_builders_manifest(self):
        manifest = json.loads(self._load("integrations/builders_agents.json"))
        agents = manifest if isinstance(manifest, list) else manifest.get("agents", [])
        entry = next((a for a in agents if a.get("slug") == "security-lab"), None)
        self.assertIsNotNone(entry, "security-lab entry missing")
        self.assertEqual(entry.get("tools"), len(EXPECTED_TOOLS))
        self.assertEqual(entry.get("endpoint"), "http://vuln-mcp:3099")

    def test_guide_names_every_tool(self):
        guide = self._load("docs/guides/MCP_Security_Lab.md")
        for name in EXPECTED_TOOLS:
            self.assertIn(name, guide)


# --------------------------------------------------------------------------- #
# Live mode                                                                    #
# --------------------------------------------------------------------------- #
def live(url, token):
    checks = []

    def check(name, fn):
        try:
            fn()
            checks.append(("PASS", name, ""))
        except Exception as e:  # report every check, then fail at the end
            checks.append(("FAIL", name, f"{type(e).__name__}: {e}"[:160]))

    c = Client(url, token)

    def handshake():
        c.handshake()
        assert c.sid, "no mcp-session-id header"
    check("initialize + session id", handshake)
    check("tools/list = 4 lab tools", lambda: _eq([t["name"] for t in c.tools()], EXPECTED_TOOLS))
    check("ping", lambda: _eq(c.rpc("ping", mid=4)["result"], {}))

    def stamp():
        _, text = c.call_text("weather_lookup", {"city": "Paris"})
        assert STAMP in text and "\\u" not in text, "stamp missing or escaped"
    check("results carry the readable SIMULATED stamp", stamp)

    def fake():
        _, text = c.call_text("read_local_file", {"path": "~/.aws/credentials"})
        assert "AKIAFAKEFAKEFAKE0000" in text, "canned fake not returned"
    check("read_local_file returns the canned FAKE", fake)
    check("notification -> 202", lambda: _eq(c.notify("notifications/initialized"), 202))

    def health():
        status, _, body = c.raw("GET", "/health")
        assert status == 200 and json.loads(body)["simulated"] is True, f"HTTP {status}"
    check("GET /health", health)

    def rug():
        status, _, body = c.raw("GET", "/health")
        if json.loads(body).get("rug_pull") != "clean":
            return  # a class is mid-exercise: do not disturb it
        assert "<IMPORTANT>" not in currency_description(c)
        c.call("currency_convert", {"amount": 1, "from": "USD", "to": "EUR"})
        other = Client(url, token)
        other.handshake()
        assert "<IMPORTANT>" in currency_description(other), "rug pull not visible to a new session"
        assert c.raw("POST", "/reset", b"")[0] == 200
        assert "<IMPORTANT>" not in currency_description(other), "reset did not restore the clean text"
    check("rug pull flips for every client and resets", rug)

    width = max(len(n) for _, n, _ in checks)
    for result, name, detail in checks:
        print(f"{result}  {name.ljust(width)}  {detail}")
    failed = [n for r, n, _ in checks if r == "FAIL"]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed against {url}")
    return 1 if failed else 0


def _eq(got, want):
    if got != want:
        raise AssertionError(f"got {got!r}, want {want!r}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--url", help="run live checks against a running server, e.g. http://vuln-mcp:3099")
    opts, rest = parser.parse_known_args()
    if opts.url:
        sys.exit(live(opts.url, os.getenv("MCP_BEARER_TOKEN") or None))
    unittest.main(argv=[sys.argv[0]] + rest, verbosity=2)
