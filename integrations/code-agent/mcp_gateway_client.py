#!/usr/bin/env python3
"""
Code-First MCP Gateway Client  (teaching artifact)
==================================================
This is the *graduation path* from the low-code agents (n8n / Flowise /
Langflow) to plain code. It talks to the **exact same** Docker MCP Gateway the
low-code agents use:

    http://mcp-gateway:8080/mcp        (Streamable HTTP, Bearer auth)

...but instead of a visual node doing the MCP handshake for you, this file does
it by hand so you can *see* every step. It uses the Python standard library
ONLY (``urllib``): no ``pip install``, no MCP SDK, no ``requests``. It runs
unchanged inside a bare ``python:3.12-alpine`` container and shows exactly what
the framework nodes hide from you.

What it does when run:

    1. initialize                 -> capture the Mcp-Session-Id response header
    2. notifications/initialized  -> tell the gateway we're ready (no reply)
    3. tools/list                 -> print the tool count + a few tool names
    4. tools/call                 -> call one READ-ONLY tool and print the result
                                     (reputation_ip on 8.8.8.8 by default)
    5. DELETE the session         -> let the gateway free it

If the gateway forgets the session (it restarted, or evicted an idle session)
it answers HTTP 404 to the old Mcp-Session-Id. As the MCP spec requires, the
client then starts a new session (initialize again) and retries the request
once.

Three things this file exists to teach (see docs/guides/MCP_Gateway_Explained.md):

  * MCP is a *stateful* protocol. The Mcp-Session-Id from ``initialize`` ties
    every later request to your session. A ``tools/list`` sent without it is
    REJECTED with a JSON-RPC error ('method "tools/list" is invalid during
    session initialization'). It is not an empty list. This client raises that
    error instead of hiding it as "0 tools".
  * The gateway replies as **Server-Sent Events**: each response is an
    ``event: message`` / ``data: {json}`` frame, not a plain JSON body. The
    ``Accept`` header must list BOTH ``application/json`` and
    ``text/event-stream`` (the gateway answers HTTP 400 otherwise), and the
    client pulls the JSON out of the ``data:`` lines itself.
  * Sessions cost memory. The gateway keeps each session, plus one upstream
    session on every MCP server the session used, until the client sends
    ``DELETE``. Close what you open: ``client.close()``, or
    ``with MCPGatewayClient() as client: ...``.

Environment:
    MCP_GATEWAY_TOKEN   required: the gateway Bearer token from the lab .env
    GATEWAY_URL         default http://mcp-gateway:8080/mcp
    MCP_TIMEOUT         default 60 (seconds per gateway request)
    SAMPLE_TOOL         default reputation_ip   (any read-only tool name)
    SAMPLE_TOOL_ARGS    default {"ip": "8.8.8.8"}  (JSON object of tool args)

Run it from this folder (integrations/code-agent), inside the lab's Docker
network (find its name with `docker network ls`). Pass only the token, not the
whole .env (which also holds provider keys and Check Point credentials):

    docker run --rm --network <lab-network> \\
      --env-file <(grep '^MCP_GATEWAY_TOKEN=' ../../.env) \\
      -v "$PWD":/app:ro python:3.12-alpine python /app/mcp_gateway_client.py

With 1Password references in .env, resolve them first:
    op run --env-file=../../.env -- docker run --rm --network <lab-network> \\
      -e MCP_GATEWAY_TOKEN -v "$PWD":/app:ro python:3.12-alpine python /app/mcp_gateway_client.py
"""

import http.client
import json
import os
import re
import urllib.error
import urllib.request

DEFAULT_GATEWAY_URL = "http://mcp-gateway:8080/mcp"

# The MCP protocol revision we ask for. The server answers with the revision it
# will actually speak; we send that back on every later request.
PROTOCOL_VERSION = "2025-06-18"

RUN_HINT = ("From integrations/code-agent run:  docker run --rm --network <lab-network> "
            "--env-file <(grep -E '^(<vars>)=' ../../.env) -v \"$PWD\":/app:ro "
            "python:3.12-alpine python /app/<script>.py   (docker network ls shows <lab-network>)")


def env(name, default=""):
    """Read one setting from the environment.

    ``docker run --env-file`` passes values exactly as written, so surrounding
    quotes are stripped here. A 1Password reference (``op://...``) means the
    value was never resolved: run the container under ``op run`` instead.
    """
    value = os.environ.get(name, "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1].strip()
    if value.startswith("op://"):
        raise SystemExit(
            f"{name} is an unresolved 1Password reference (op://...), not a value. Resolve it "
            f"with 1Password CLI:  op run --env-file=../../.env -- docker run ... -e {name} ...")
    return value or default


class MCPError(Exception):
    """A gateway failure: HTTP status, network problem, or a JSON-RPC error reply.
    ``status`` is the HTTP status code when there was one, else None."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------------- #
# Reply parsing.
#
# A Streamable-HTTP MCP reply is either plain JSON or an SSE stream such as:
#     event: message
#     data: {"jsonrpc":"2.0","id":1,"result":{...}}
#     <blank line>
# A blank line ends one event, and the stream may carry several events (for
# example a log notification before the result). So we parse event by event,
# then pick the message whose "id" matches our request.
# --------------------------------------------------------------------------- #
def parse_messages(raw_body):
    """Return the list of JSON-RPC messages in one HTTP reply body."""
    text = raw_body.decode("utf-8", "replace") if isinstance(raw_body, bytes) else raw_body
    if text.lstrip().startswith(("{", "[")):          # plain JSON, no SSE framing
        obj = json.loads(text)
        return obj if isinstance(obj, list) else [obj]
    messages, data_lines = [], []
    for line in text.splitlines() + [""]:              # the extra "" flushes the last event
        if line == "":                                 # blank line = end of one event
            if data_lines:
                messages.append(json.loads("\n".join(data_lines)))
                data_lines = []
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].lstrip())
        # "event:", "id:", "retry:" and ":" comment lines carry no JSON; skip them.
    return messages


def tool_result_text(result):
    """Flatten a tools/call result into plain text for printing or for an LLM."""
    parts = []
    for block in (result or {}).get("content") or []:
        if block.get("type") == "text":
            parts.append(block.get("text", ""))
        else:
            parts.append(f"[{block.get('type', 'non-text')} content omitted]")
    if not parts and (result or {}).get("structuredContent") is not None:
        parts.append(json.dumps(result["structuredContent"]))
    return "\n".join(parts)


def _explain_http_error(code, detail):
    """Turn a gateway HTTP error into an actionable message."""
    detail = detail.strip()
    if code == 401:
        return ("HTTP 401 from the gateway: the Bearer token was rejected. MCP_GATEWAY_TOKEN must "
                "be the value from the lab .env.")
    if code == 404:
        return ("HTTP 404 from the gateway: session not found (the gateway restarted or closed the "
                "session), or GATEWAY_URL does not end in /mcp.")
    return f"HTTP {code} from the gateway: {detail or '(empty body)'}"


# --------------------------------------------------------------------------- #
# The client. One instance == one MCP session (one Mcp-Session-Id).
# --------------------------------------------------------------------------- #
class MCPGatewayClient:
    def __init__(self, url=None, token=None, timeout=None):
        self.url = url or env("GATEWAY_URL", DEFAULT_GATEWAY_URL)
        self.token = token if token is not None else env("MCP_GATEWAY_TOKEN")
        if not self.token:
            raise SystemExit("MCP_GATEWAY_TOKEN is not set. The gateway needs the token from the "
                             "lab .env. " + RUN_HINT.replace("<script>", "mcp_gateway_client")
                             .replace("<vars>", "MCP_GATEWAY_TOKEN"))
        self.timeout = float(timeout or env("MCP_TIMEOUT", "60"))
        self.session_id = None          # filled in by initialize()
        self.protocol_version = None    # what the server agreed to speak
        self.server_info = {}
        self.reinitialized = 0          # new sessions opened after an HTTP 404
        self._next_id = 0               # JSON-RPC request id counter

    # --- transport -------------------------------------------------------- #

    def _headers(self):
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            # BOTH types, or the gateway answers HTTP 400.
            "Accept": "application/json, text/event-stream",
        }
        # After initialize, every request must echo the session id. Without it
        # the gateway treats us as a brand-new client that has not handshaken.
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        if self.protocol_version:
            headers["MCP-Protocol-Version"] = self.protocol_version
        return headers

    def _post(self, payload):
        """POST one JSON-RPC message and return the raw reply body."""
        req = urllib.request.Request(self.url, data=json.dumps(payload).encode("utf-8"),
                                     headers=self._headers(), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                # The session id arrives as a response header on initialize.
                sid = resp.headers.get("Mcp-Session-Id")
                if sid and not self.session_id:
                    self.session_id = sid
                return resp.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:500].replace(self.token, "***")
            raise MCPError(_explain_http_error(e.code, detail), status=e.code) from None
        except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
            # Connection refused, DNS failure, or a timeout while waiting/reading.
            reason = getattr(e, "reason", None) or e
            raise MCPError(f"Cannot reach the gateway at {self.url}: {reason}. Run this inside the "
                           f"lab network (docker run --network <lab-network> ...).") from None

    def request(self, method, params=None):
        """Send a JSON-RPC request and return its ``result``. Raises MCPError
        when the server answers with a JSON-RPC ``error`` instead.

        HTTP 404 on an existing session means the gateway no longer knows it:
        open a new session and retry the request once (MCP spec, session
        management)."""
        try:
            return self._request_once(method, params)
        except MCPError as e:
            if e.status != 404 or not self.session_id or method == "initialize":
                raise
        self.session_id = None
        self.protocol_version = None
        self.reinitialized += 1
        self.initialize()
        return self._request_once(method, params)

    def _request_once(self, method, params):
        req_id = self._next_id = self._next_id + 1
        raw = self._post({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}})
        try:
            messages = parse_messages(raw)
        except json.JSONDecodeError as e:
            raise MCPError(f"{method}: unreadable reply from the gateway ({e})") from None
        reply = next((m for m in messages if isinstance(m, dict) and m.get("id") == req_id
                      and ("result" in m or "error" in m)), None)
        if reply is None:
            raise MCPError(f"{method}: the gateway sent no response for request id {req_id}")
        if "error" in reply:
            err = reply["error"] or {}
            raise MCPError(f"{method} failed: JSON-RPC error {err.get('code')}: {err.get('message')}")
        return reply.get("result") or {}

    def notify(self, method, params=None):
        """Send a JSON-RPC notification (no id, so no reply: the server sends 202)."""
        msg = {"jsonrpc": "2.0", "method": method}
        if params:
            msg["params"] = params
        self._post(msg)

    # --- MCP handshake + calls ------------------------------------------- #

    def initialize(self):
        """Step 1: handshake. Captures the Mcp-Session-Id, then step 2 confirms
        readiness with a notification. Returns the server's initialize result."""
        self.session_id = None          # a new handshake always starts a new session
        self.protocol_version = None
        result = self.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "code-first-client", "version": "1.1.0"},
        })
        self.protocol_version = result.get("protocolVersion") or PROTOCOL_VERSION
        self.server_info = result.get("serverInfo") or {}
        # Step 2: notifications/initialized. The spec requires it before normal
        # requests; some servers (not this gateway) refuse tools/list without it.
        self.notify("notifications/initialized")
        return result

    def list_tools(self):
        """Step 3: the real catalog. Follows ``nextCursor`` pagination and
        returns every tool dict (name, description, inputSchema)."""
        tools, cursor = [], None
        while True:
            result = self.request("tools/list", {"cursor": cursor} if cursor else {})
            tools.extend(result.get("tools") or [])
            cursor = result.get("nextCursor")
            if not cursor:
                return tools

    def call_tool(self, name, arguments):
        """Step 4: invoke one tool. Returns the result dict: ``content`` blocks
        plus ``isError: true`` when the tool itself reported a failure."""
        return self.request("tools/call", {"name": name, "arguments": arguments or {}})

    def close(self):
        """Step 5: DELETE the session so the gateway can free it, together
        with the upstream sessions it opened on the MCP servers you used."""
        if not self.session_id:
            return
        req = urllib.request.Request(self.url, method="DELETE", headers=self._headers())
        try:
            urllib.request.urlopen(req, timeout=10).close()
        except (urllib.error.URLError, OSError, http.client.HTTPException):
            pass  # already gone (404) or DELETE not supported (405): nothing more to free
        finally:
            self.session_id = None
            self.protocol_version = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def one_line(text, width=60):
    """Collapse whitespace (some tool descriptions contain newlines) and trim."""
    text = " ".join((text or "").split())
    return text if len(text) <= width else text[:width - 3] + "..."


# --------------------------------------------------------------------------- #
# Walkthrough driver: print every step of one session.
# --------------------------------------------------------------------------- #
def main():
    tool_name = env("SAMPLE_TOOL", "reputation_ip")
    try:
        tool_args = json.loads(env("SAMPLE_TOOL_ARGS", '{"ip": "8.8.8.8"}'))
    except json.JSONDecodeError as e:
        raise SystemExit(f"SAMPLE_TOOL_ARGS is not valid JSON: {e}") from None
    if not isinstance(tool_args, dict):
        raise SystemExit("SAMPLE_TOOL_ARGS must be a JSON object, for example {\"ip\": \"8.8.8.8\"}")

    client = MCPGatewayClient()
    print(f"-> Connecting to MCP gateway: {client.url}")
    try:
        with client:
            # 1 + 2: handshake.
            client.initialize()
            print(f"-> initialize OK. session={client.session_id} "
                  f"server={client.server_info.get('name', '?')} protocol={client.protocol_version}")

            # 3: enumerate tools.
            tools = client.list_tools()
            print(f"-> tools/list returned {len(tools)} tools.")
            for t in tools[:8]:
                print(f"     - {t.get('name')}: {one_line(t.get('description'))}")
            if len(tools) > 8:
                print(f"     ... and {len(tools) - 8} more")

            # 4: call one READ-ONLY tool and print the result.
            if tool_name not in {t.get("name") for t in tools}:
                print(f"-> '{tool_name}' is not in the catalog; skipping the tools/call step.")
            else:
                print(f"-> tools/call {tool_name}({json.dumps(tool_args)}) ...")
                result = client.call_tool(tool_name, tool_args)
                if result.get("isError"):
                    print("-> the tool reported an error (isError: true):")
                text = tool_result_text(result) or "(no content returned)"
                print("     " + text.replace("\n", "\n     "))
        # 5: leaving the "with" block sent DELETE for the session.
        print("-> session closed (HTTP DELETE).")
    except MCPError as e:
        raise SystemExit(f"ERROR: {e}") from None


if __name__ == "__main__":
    main()
