#!/usr/bin/env python3
"""
Code-First Agent Loop  (teaching artifact, ~120 lines)
======================================================
This is the *smallest honest* LLM tool-use loop that drives the Check Point MCP
gateway from code. It is the code-first twin of the n8n / Flowise / Langflow
"via-gateway" agents: same gateway, same tools, same Bearer token — but the
agent loop is now something you can read top to bottom.

It reuses ``mcp_gateway_client.MCPGatewayClient`` (stdlib-only, urllib) for the
MCP side, and talks to the **Anthropic Messages API** for the brain.

Because PyPI/pip is blocked by org policy, this does NOT use the ``anthropic``
SDK. It calls ``https://api.anthropic.com/v1/messages`` directly with urllib:

    x-api-key: $ANTHROPIC_API_KEY
    anthropic-version: 2023-06-01

The loop:

    1. handshake with the gateway + tools/list
    2. map each MCP tool -> an Anthropic tool schema (name/description/input_schema)
    3. send the user prompt + tool list to the model
    4. while the model returns stop_reason == "tool_use":
         - run each requested tool via the gateway's tools/call
         - append the tool_result blocks and call the model again
    5. print the final text answer

Environment:
    ANTHROPIC_API_KEY   required (your Claude API key)
    ANTHROPIC_MODEL     default claude-opus-4-8
    GATEWAY_URL         default http://mcp-gateway:8080/mcp   (via mcp_gateway_client)
    MCP_GATEWAY_TOKEN   default cp-mcp-gateway-training-token (via mcp_gateway_client)
    USER_PROMPT         default a reputation lookup on 8.8.8.8
"""

import json
import os
import urllib.request
import urllib.error

from mcp_gateway_client import MCPGatewayClient

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-8")
MAX_TOKENS = 1024
MAX_TURNS = 6  # safety valve so a misbehaving loop can't run forever


def anthropic_messages(api_key, messages, tools, system):
    """One POST to the Messages API. Returns the parsed response dict."""
    payload = {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "system": system,
        "tools": tools,
        "messages": messages,
    }
    req = urllib.request.Request(
        ANTHROPIC_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"Anthropic HTTP {e.code}: {e.read().decode('utf-8', 'replace')}") from None


def mcp_tools_to_anthropic(mcp_tools):
    """Map MCP tool dicts to the Anthropic tool schema. MCP's ``inputSchema`` is
    already JSON-Schema, which is exactly what Anthropic's ``input_schema`` wants."""
    tools = []
    for t in mcp_tools:
        tools.append({
            "name": t["name"],
            "description": (t.get("description") or "")[:1024],
            "input_schema": t.get("inputSchema") or {"type": "object", "properties": {}},
        })
    return tools


def run_tool(client, name, args):
    """Execute one MCP tool via the gateway and return its text content."""
    resp = client.call_tool(name, args)
    content = ((resp or {}).get("result") or {}).get("content", [])
    texts = [b.get("text", "") for b in content if b.get("type") == "text"]
    return "\n".join(texts) or json.dumps(resp)


def main():
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("ANTHROPIC_API_KEY is not set.")

    # 1: MCP handshake + tool discovery (see mcp_gateway_client.py).
    client = MCPGatewayClient()
    client.initialize()
    mcp_tools = client.list_tools()
    print(f"-> gateway exposed {len(mcp_tools)} tools; handing them to {MODEL}")

    # 2: map to Anthropic tool schema.
    tools = mcp_tools_to_anthropic(mcp_tools)

    system = ("You are a Check Point operations assistant. Use the provided MCP "
              "tools to answer. Read first; summarise results in plain language.")
    user_prompt = os.getenv(
        "USER_PROMPT",
        "What is the reputation of the IP address 8.8.8.8? Use the reputation tool.")
    messages = [{"role": "user", "content": user_prompt}]

    # 3 + 4: the tool-use loop.
    for _turn in range(MAX_TURNS):
        reply = anthropic_messages(api_key, messages, tools, system)
        # Record the assistant turn verbatim (text + any tool_use blocks).
        messages.append({"role": "assistant", "content": reply["content"]})

        if reply.get("stop_reason") != "tool_use":
            # Final answer — print the text blocks and stop.
            for block in reply["content"]:
                if block.get("type") == "text":
                    print("\n=== ANSWER ===\n" + block["text"])
            return

        # Run every tool the model asked for and feed the results back.
        tool_results = []
        for block in reply["content"]:
            if block.get("type") != "tool_use":
                continue
            print(f"-> model called {block['name']}({json.dumps(block['input'])})")
            output = run_tool(client, block["name"], block.get("input") or {})
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block["id"],
                "content": output,
            })
        messages.append({"role": "user", "content": tool_results})

    print("-> stopped: hit MAX_TURNS without a final answer.")


if __name__ == "__main__":
    main()
