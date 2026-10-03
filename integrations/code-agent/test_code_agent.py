#!/usr/bin/env python3
"""
Offline tests for the code-first agent. Standard library only; no gateway,
no model: a mock MCP gateway runs on 127.0.0.1 and the model is the lab's
mock provider (tests/acceptance/mock_provider.py), so no inference happens.

    python3 integrations/code-agent/test_code_agent.py

Client: SSE replies with several events (a notification before the result),
plain JSON replies, Bearer auth (401 explained, token never echoed), a JSON-RPC
error surfaced instead of "0 tools", nextCursor pagination, HTTP 404 on a
forgotten session -> one new session and a retry, DELETE on close.
Loop: agent_loop.py against the mock provider: tool scoping (MCP_TOOLS), one
tool call through the gateway, the tool result returned to the model, a final
answer, and a failing tool call sent back to the model instead of crashing.
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
MOCK_PROVIDER = os.path.join(REPO, "tests", "acceptance", "mock_provider.py")
sys.path.insert(0, HERE)
import mcp_gateway_client as mgc  # noqa: E402

TOKEN = "test-gateway-token-0123456789"
TOOLS = [
    {"name": "reputation_ip", "description": "Get the reputation of an IP\naddress.",
     "inputSchema": {"$schema": "http://json-schema.org/draft-07/schema#", "type": "object",
                     "properties": {"ip": {"type": "string"}}, "required": ["ip"]}},
    {"name": "reputation_url", "description": "Get the reputation of a URL.",
     "inputSchema": {"type": "object", "properties": {"url": {"type": "string"}}}},
] + [{"name": f"show_object_{i}", "description": "Management tool.", "inputSchema": {"type": "object"}}
     for i in range(5)]


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self):  # skip the reverse-DNS lookup HTTPServer does
        super(HTTPServer, self).server_bind()
        self.server_name, self.server_port = "127.0.0.1", self.server_address[1]


class MockGateway:
    """Just enough of the Docker MCP Gateway's Streamable HTTP behaviour."""

    def __init__(self):
        self.sessions = set()
        self.deleted = []
        self.calls = []          # (tool name, arguments)
        self.inits = 0
        self.plain_json = False  # answer with application/json instead of SSE
        gw = self

        class H(BaseHTTPRequestHandler):
            def _send(self, status, body=b"", ctype="text/plain", headers=()):
                self.send_response(status)
                if body:
                    self.send_header("Content-Type", ctype)
                for k, v in headers:
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def _reply(self, msg, headers=()):
                if gw.plain_json:
                    return self._send(200, json.dumps(msg).encode(), "application/json", headers)
                note = {"jsonrpc": "2.0", "method": "notifications/message",
                        "params": {"level": "info", "data": "processing"}}
                body = (": keep-alive\n\nevent: message\ndata: " + json.dumps(note) + "\n\n"
                        "event: message\nid: 7\ndata: " + json.dumps(msg) + "\n\n").encode()
                self._send(200, body, "text/event-stream", headers)

            def do_POST(self):
                if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                    return self._send(401, b"Unauthorized")
                if "text/event-stream" not in self.headers.get("Accept", ""):
                    return self._send(400, b"Accept must contain text/event-stream")
                msg = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
                sid = self.headers.get("Mcp-Session-Id")
                method, mid = msg.get("method"), msg.get("id")
                if method == "initialize":
                    gw.inits += 1
                    new = str(uuid.uuid4())
                    gw.sessions.add(new)
                    return self._reply({"jsonrpc": "2.0", "id": mid, "result": {
                        "protocolVersion": msg["params"]["protocolVersion"], "capabilities": {"tools": {}},
                        "serverInfo": {"name": "Docker AI MCP Gateway (mock)", "version": "0"}}},
                        [("Mcp-Session-Id", new)])
                if sid and sid not in gw.sessions:
                    return self._send(404, b"session not found")
                if not sid:
                    return self._reply({"jsonrpc": "2.0", "id": mid, "error": {
                        "code": 0, "message": f'method "{method}" is invalid during session initialization'}})
                if mid is None:
                    return self._send(202)
                if method == "tools/list":
                    cursor = (msg.get("params") or {}).get("cursor")
                    page = TOOLS[4:] if cursor == "page2" else TOOLS[:4]
                    result = {"tools": page}
                    if cursor != "page2":
                        result["nextCursor"] = "page2"
                    return self._reply({"jsonrpc": "2.0", "id": mid, "result": result})
                if method == "tools/call":
                    p = msg["params"]
                    gw.calls.append((p["name"], p.get("arguments")))
                    if p["name"] == "reputation_ip" and "ip" not in (p.get("arguments") or {}):
                        return self._reply({"jsonrpc": "2.0", "id": mid, "result": {
                            "content": [{"type": "text", "text": "Invalid arguments: ip is required"}],
                            "isError": True}})
                    return self._reply({"jsonrpc": "2.0", "id": mid, "result": {
                        "content": [{"type": "text", "text": json.dumps({"risk": 0, "classification": "Benign"})}]}})
                return self._reply({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "not found"}})

            def do_DELETE(self):
                sid = self.headers.get("Mcp-Session-Id")
                if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                    return self._send(401, b"Unauthorized")
                if sid not in gw.sessions:
                    return self._send(404, b"session not found")
                gw.sessions.discard(sid)
                gw.deleted.append(sid)
                self._send(204)

            def log_message(self, *a):
                pass

        self.server = _Server(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d/mcp" % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


class ParseMessages(unittest.TestCase):
    def test_sse_with_several_events_and_crlf(self):
        body = ('event: message\r\ndata: {"jsonrpc":"2.0","method":"notifications/progress"}\r\n\r\n'
                'event: message\r\ndata: {"jsonrpc":"2.0",\r\ndata: "id":3,"result":{}}\r\n\r\n')
        msgs = mgc.parse_messages(body.encode())
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[1]["id"], 3)

    def test_plain_json_and_batch(self):
        self.assertEqual(mgc.parse_messages(b'{"jsonrpc":"2.0","id":1,"result":{}}')[0]["id"], 1)
        self.assertEqual(len(mgc.parse_messages(b'[{"id":1},{"id":2}]')), 2)


class Client(unittest.TestCase):
    def setUp(self):
        self.gw = MockGateway()

    def tearDown(self):
        self.gw.server.shutdown()

    def client(self, token=TOKEN):
        return mgc.MCPGatewayClient(url=self.gw.url, token=token, timeout=10)

    def test_handshake_pagination_call_and_delete(self):
        with self.client() as c:
            c.initialize()
            sid = c.session_id
            self.assertTrue(sid)
            tools = c.list_tools()
            self.assertEqual(len(tools), len(TOOLS), "nextCursor pages must be followed")
            res = c.call_tool("reputation_ip", {"ip": "8.8.8.8"})
            self.assertIn("Benign", mgc.tool_result_text(res))
        self.assertEqual(self.gw.deleted, [sid], "close() must DELETE the session")
        self.assertIsNone(c.session_id)

    def test_plain_json_reply(self):
        self.gw.plain_json = True
        with self.client() as c:
            c.initialize()
            self.assertEqual(len(c.list_tools()), len(TOOLS))

    def test_jsonrpc_error_is_raised_not_zero_tools(self):
        c = self.client()
        with self.assertRaises(mgc.MCPError) as ctx:
            c.list_tools()  # no initialize: the gateway answers with a JSON-RPC error
        self.assertIn("invalid during session initialization", str(ctx.exception))

    def test_404_reinitializes_once_and_retries(self):
        with self.client() as c:
            c.initialize()
            old = c.session_id
            self.gw.sessions.clear()  # the gateway restarted / evicted the session
            tools = c.list_tools()
            self.assertEqual(len(tools), len(TOOLS))
            self.assertNotEqual(c.session_id, old)
            self.assertEqual((c.reinitialized, self.gw.inits), (1, 2))

    def test_401_is_explained_and_token_not_echoed(self):
        c = self.client(token="wrong-token-abcdef")
        with self.assertRaises(mgc.MCPError) as ctx:
            c.initialize()
        self.assertEqual(ctx.exception.status, 401)
        self.assertIn("MCP_GATEWAY_TOKEN", str(ctx.exception))
        self.assertNotIn("wrong-token-abcdef", str(ctx.exception))

    def test_unreachable(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        c = mgc.MCPGatewayClient(url=f"http://127.0.0.1:{port}/mcp", token=TOKEN, timeout=3)
        with self.assertRaises(mgc.MCPError) as ctx:
            c.initialize()
        self.assertIn("Cannot reach the gateway", str(ctx.exception))


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@unittest.skipUnless(os.path.isfile(MOCK_PROVIDER), "tests/acceptance/mock_provider.py not present")
class AgentLoop(unittest.TestCase):
    """agent_loop.py end to end: mock gateway (tools) + mock provider (model)."""

    @classmethod
    def setUpClass(cls):
        cls.logdir = tempfile.mkdtemp(prefix="mock-provider-")
        cls.port = _free_port()
        env = dict(os.environ, MOCK_PORT=str(cls.port), MOCK_LOG_DIR=cls.logdir, PYTHONDONTWRITEBYTECODE="1")
        cls.provider = subprocess.Popen([sys.executable, MOCK_PROVIDER], env=env,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + 30  # its HTTPServer does a reverse-DNS lookup at start
        while time.time() < deadline:
            try:
                socket.create_connection(("127.0.0.1", cls.port), 1).close()
                return
            except OSError:
                time.sleep(0.2)
        raise RuntimeError("mock provider did not start")

    @classmethod
    def tearDownClass(cls):
        cls.provider.terminate()
        cls.provider.wait(10)

    def setUp(self):
        self.gw = MockGateway()

    def tearDown(self):
        self.gw.server.shutdown()

    def run_loop(self, **extra):
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONDONTWRITEBYTECODE": "1",
               "GATEWAY_URL": self.gw.url, "MCP_GATEWAY_TOKEN": TOKEN,
               "LITELLM_BASE_URL": f"http://127.0.0.1:{self.port}/v1", "LITELLM_MASTER_KEY": "sk-test-master-key",
               "MCP_TOOLS": "reputation_ip", "MAX_TURNS": "4", "LLM_TIMEOUT": "20"}
        env.update(extra)
        p = subprocess.run([sys.executable, os.path.join(HERE, "agent_loop.py")], env=env, cwd=tempfile.gettempdir(),
                           capture_output=True, text=True, timeout=120)
        return p.returncode, p.stdout + p.stderr

    def requests(self):
        with open(os.path.join(self.logdir, "requests.jsonl"), encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def test_tool_call_round_trip(self):
        before = len(self.requests())
        rc, out = self.run_loop()
        self.assertEqual(rc, 0, out)
        self.assertIn("=== ANSWER ===", out)
        self.assertRegex(out, r"(?m)^-> model called reputation_ip\(")
        self.assertIn("(error, sent back to the model)", out, "the mock's arguments miss 'ip': error path")
        self.assertEqual([n for n, _ in self.gw.calls], ["reputation_ip"])
        chats = [r for r in self.requests()[before:] if r["path"].endswith("/chat/completions")]
        self.assertEqual(len(chats), 2, "one call with the tools, one with the tool result")
        first, second = chats[0]["body"], chats[1]["body"]
        self.assertEqual(first["model"], "lab-chat")
        self.assertEqual([t["function"]["name"] for t in first["tools"]], ["reputation_ip"], "MCP_TOOLS scoping")
        self.assertNotIn("$schema", first["tools"][0]["function"]["parameters"])
        tool_msgs = [m for m in second["messages"] if m["role"] == "tool"]
        self.assertEqual(len(tool_msgs), 1)
        self.assertTrue(tool_msgs[0]["content"].startswith("ERROR:"))
        self.assertEqual(self.gw.deleted and len(self.gw.deleted), 1, "the MCP session must be closed")
        self.assertNotIn("sk-test-master-key", out)
        self.assertNotIn(TOKEN, out)

    def test_pattern_scoping_and_no_match(self):
        rc, out = self.run_loop(MCP_TOOLS="reputation_*")
        self.assertEqual(rc, 0, out)
        self.assertIn("binding 2", out)
        rc, out = self.run_loop(MCP_TOOLS="nothing_matches_*")
        self.assertEqual(rc, 1)
        self.assertIn("matches none", out)

    def test_bad_settings_fail_cleanly(self):
        rc, out = self.run_loop(MAX_TURNS="many")
        self.assertEqual(rc, 1)
        self.assertIn("MAX_TURNS='many' is not a number", out)
        rc, out = self.run_loop(LITELLM_MASTER_KEY="")
        self.assertEqual(rc, 1)
        self.assertIn("LITELLM_MASTER_KEY is not set", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
