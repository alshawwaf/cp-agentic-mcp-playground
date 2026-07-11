#!/usr/bin/env python3
"""
Code-First MCP Gateway Client  (teaching artifact)
==================================================
This is the *graduation path* from the low-code agents (n8n / Flowise /
Langflow) to plain code. It talks to the **exact same** Docker MCP Gateway the
low-code agents use:

    http://mcp-gateway:8080/mcp        (Streamable-HTTP, Bearer auth)

...but instead of a visual node doing the MCP handshake for you, this file does
it by hand so you can *see* every step. It is written with the Python standard
library ONLY (``urllib``) — no ``pip install``, no MCP SDK, no ``requests``.
That keeps it runnable inside a bare ``python:3.12-alpine`` container and shows
exactly what the framework nodes hide from you.

What it does when run:

    1. initialize                 -> capture the Mcp-Session-Id response header
    2. notifications/initialized  -> tell the gateway we're ready (no reply)
    3. tools/list                 -> print the tool count + a few tool names
    4. tools/call                 -> call one READ-ONLY tool and print the result
                                     (reputation_ip on 8.8.8.8 by default)

Two gotchas this file exists to teach (see docs/guides/MCP_Gateway_Explained.md):

  * MCP is a *stateful* protocol. A bare ``tools/list`` BEFORE the handshake
    returns ZERO tools. You must initialize, echo back the session id, and send
    ``notifications/initialized`` first.
  * The gateway replies as **Server-Sent Events**, so each response is an
    ``event: message`` / ``data: {json}`` frame, not a plain JSON body. You must
    ask for ``Accept: text/event-stream`` and pull the JSON out of the
    ``data:`` line(s) yourself.

Environment:
    GATEWAY_URL         default http://mcp-gateway:8080/mcp
    MCP_GATEWAY_TOKEN   default cp-mcp-gateway-training-token
    DEMO_TOOL           default reputation_ip   (any read-only tool name)
    DEMO_TOOL_ARGS      default {"ip": "8.8.8.8"}  (JSON object of tool args)
"""

import json
import os
import urllib.request
import urllib.error

# --------------------------------------------------------------------------- #
# Configuration — all overridable by env so the same file runs unchanged in the
# demo network, in CI, or against a differently-named gateway.
# --------------------------------------------------------------------------- #
GATEWAY_URL = os.getenv("GATEWAY_URL", "http://mcp-gateway:8080/mcp")
TOKEN = os.getenv("MCP_GATEWAY_TOKEN", "cp-mcp-gateway-training-token")

# The MCP protocol revision the gateway speaks. Any recent value is accepted;
# the gateway echoes back the one it actually supports in the initialize reply.
PROTOCOL_VERSION = "2025-06-18"


# --------------------------------------------------------------------------- #
# SSE helper: pull the JSON payload out of a Server-Sent-Events response body.
#
# A Streamable-HTTP MCP reply looks like:
#     event: message
#     data: {"jsonrpc":"2.0","id":1,"result":{...}}
#
# There can be blank lines and multiple frames. We concatenate every ``data:``
# line and JSON-decode the result. (Per the SSE spec, multiple data: lines in a
# single event are joined with newlines — which is still valid JSON here.)
# --------------------------------------------------------------------------- #
def _extract_sse_json(raw_body):
    data_lines = []
    for line in raw_body.splitlines():
        # Lines may arrive as bytes; normalise to str.
        if isinstance(line, bytes):
            line = line.decode("utf-8", "replace")
        if line.startswith("data:"):
            data_lines.append(line[len("data:"):].lstrip())
    if not data_lines:
        # Some servers return a plain JSON body (no SSE framing) — try that too.
        text = raw_body.decode("utf-8", "replace") if isinstance(raw_body, bytes) else raw_body
        text = text.strip()
        return json.loads(text) if text else None
    return json.loads("\n".join(data_lines))


# --------------------------------------------------------------------------- #
# The client. One instance == one MCP session (one Mcp-Session-Id).
# --------------------------------------------------------------------------- #
class MCPGatewayClient:
    def __init__(self, url=GATEWAY_URL, token=TOKEN):
        self.url = url
        self.token = token
        self.session_id = None      # filled in by initialize()
        self._next_id = 0           # JSON-RPC request id counter

    def _rpc_id(self):
        self._next_id += 1
        return self._next_id

    def _send(self, payload, notification=False):
        """POST one JSON-RPC message. Returns the decoded result dict (or None
        for a notification). Also captures the Mcp-Session-Id header the first
        time the server hands one back."""
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            # MUST accept SSE or the gateway won't stream the reply back.
            "Accept": "application/json, text/event-stream",
        }
        # After initialize, every subsequent call must echo the session id, or
        # the gateway treats us as a brand-new (unhandshaked) client -> 0 tools.
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id

        req = urllib.request.Request(self.url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                # Capture the session id from the initialize response headers.
                sid = resp.headers.get("Mcp-Session-Id")
                if sid and not self.session_id:
                    self.session_id = sid
                raw = resp.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            # 401 here almost always means a wrong/missing MCP_GATEWAY_TOKEN.
            raise SystemExit(f"HTTP {e.code} from gateway: {detail}") from None
        except urllib.error.URLError as e:
            raise SystemExit(f"Cannot reach gateway at {self.url}: {e.reason}. "
                             f"Run this INSIDE the demo network (see INTEGRATION.md).") from None

        # Notifications (no id) get a 202 with an empty body — nothing to parse.
        if notification or not raw.strip():
            return None
        return _extract_sse_json(raw)

    # --- MCP handshake + calls ------------------------------------------- #

    def initialize(self):
        """Step 1: handshake. Captures the Mcp-Session-Id, then step 2 confirms
        readiness with a notification (which expects no response)."""
        result = self._send({
            "jsonrpc": "2.0",
            "id": self._rpc_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "code-first-client", "version": "1.0.0"},
            },
        })
        # Step 2: notifications/initialized — no id, no reply expected.
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"},
                   notification=True)
        return result

    def list_tools(self):
        """Step 3: the real catalog. Returns the list of tool dicts."""
        result = self._send({
            "jsonrpc": "2.0",
            "id": self._rpc_id(),
            "method": "tools/list",
            "params": {},
        })
        return ((result or {}).get("result") or {}).get("tools", [])

    def call_tool(self, name, arguments):
        """Step 4: invoke one tool. Returns the raw JSON-RPC response dict."""
        return self._send({
            "jsonrpc": "2.0",
            "id": self._rpc_id(),
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        })


# --------------------------------------------------------------------------- #
# Demo driver — the "print the whole story" walkthrough.
# --------------------------------------------------------------------------- #
def main():
    print(f"-> Connecting to MCP gateway: {GATEWAY_URL}")
    client = MCPGatewayClient()

    # 1 + 2: handshake.
    info = client.initialize()
    server_info = ((info or {}).get("result") or {}).get("serverInfo", {})
    print(f"-> initialize OK. session={client.session_id} "
          f"server={server_info.get('name', '?')}")

    # 3: enumerate tools.
    tools = client.list_tools()
    print(f"-> tools/list returned {len(tools)} tools.")
    for t in tools[:8]:
        print(f"     - {t.get('name')}: {(t.get('description') or '').strip()[:60]}")
    if len(tools) > 8:
        print(f"     ... and {len(tools) - 8} more")

    # 4: call one READ-ONLY tool and print the result.
    tool_name = os.getenv("DEMO_TOOL", "reputation_ip")
    tool_args = json.loads(os.getenv("DEMO_TOOL_ARGS", '{"ip": "8.8.8.8"}'))
    available = {t.get("name") for t in tools}
    if tool_name not in available:
        print(f"-> '{tool_name}' not in the catalog; skipping the tools/call demo.")
        return
    print(f"-> tools/call {tool_name}({json.dumps(tool_args)}) ...")
    resp = client.call_tool(tool_name, tool_args)
    content = ((resp or {}).get("result") or {}).get("content", [])
    for block in content:
        if block.get("type") == "text":
            print("     " + block.get("text", "").replace("\n", "\n     "))


if __name__ == "__main__":
    main()
