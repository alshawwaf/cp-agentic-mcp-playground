#!/usr/bin/env python3
"""
Code-First Agent Loop  (teaching artifact)
==========================================
This is the *smallest honest* LLM tool-use loop that drives the Check Point MCP
gateway from code. It is the code-first twin of the n8n / Flowise / Langflow
"via-gateway" agents: same gateway, same Bearer token, same lab model
(``lab-chat``), but the agent loop is now something you can read top to bottom.

Both halves use the Python standard library only (``urllib``: no pip install,
no SDK), so the script runs inside a bare ``python:3.12-alpine`` container:

  * the TOOLS: ``mcp_gateway_client.MCPGatewayClient`` speaks MCP to
    http://mcp-gateway:8080/mcp
  * the BRAIN: the lab's LiteLLM proxy, which speaks the OpenAI-compatible
    Chat Completions API:

        POST http://litellm:4000/v1/chat/completions
        Authorization: Bearer $LITELLM_MASTER_KEY
        {"model": "lab-chat", "messages": [...], "tools": [...]}

    LiteLLM forwards ``lab-chat`` to the provider chosen in the lab .env
    (LAB_MODEL_PROVIDER: Azure OpenAI, OpenAI, Anthropic, Gemini or local
    Ollama) and sends a trace to Langfuse when Langfuse keys are set. To switch
    providers, change .env and run ``docker compose up -d litellm``. This file
    does not change.

The loop:

    1. handshake with the gateway + tools/list
    2. keep only the tools this agent needs (MCP_TOOLS) and map each MCP tool
       to an OpenAI "function" tool: name / description / parameters
    3. send the system prompt, the user prompt and the tools to lab-chat
    4. while the reply contains tool_calls:
         - run each call through the gateway's tools/call
         - append one {"role": "tool"} message per call (errors included, so
           the model can react) and call the model again
    5. print the final answer and close the MCP session

Why scope the tools? The gateway serves about 190 tools. Sending all of them
on every turn costs tens of thousands of input tokens, overflows small local
models, and breaks providers that accept at most 128 tools per request. The
n8n agents scope theirs the same way (the MCP Client node's "include" list).

Data handling: every tool result (rulebases, objects, logs, IP addresses) is
sent to the model provider behind lab-chat. With a cloud provider, that is an
external service. Use lab data only. Never send customer configurations or
telemetry unless the provider is approved for that data.

Environment:
    LITELLM_MASTER_KEY  required: the LiteLLM key from the lab .env
    MCP_GATEWAY_TOKEN   required: the gateway Bearer token from the lab .env
    LITELLM_BASE_URL    default http://litellm:4000/v1
    LAB_MODEL           default lab-chat
    MCP_TOOLS           default reputation_*  (comma-separated tool names or
                        shell-style patterns; "*" means every tool, max 128)
    USER_PROMPT         default a reputation lookup on 8.8.8.8
    MAX_TURNS           default 8 model calls before giving up
    MAX_TOKENS          optional cap per reply (default: the provider's own)
    LLM_TIMEOUT         default 300 seconds per model call (CPU models are slow)
    GATEWAY_URL         default http://mcp-gateway:8080/mcp

Run it from this folder (integrations/code-agent), inside the lab's Docker
network (find its name with `docker network ls`). Pass only the two keys the
script needs, not the whole .env (provider keys stay with LiteLLM):

    docker run --rm --network <lab-network> \\
      --env-file <(grep -E '^(MCP_GATEWAY_TOKEN|LITELLM_MASTER_KEY)=' ../../.env) \\
      -v "$PWD":/app:ro python:3.12-alpine python /app/agent_loop.py

With 1Password references in .env:
    op run --env-file=../../.env -- docker run --rm --network <lab-network> \\
      -e MCP_GATEWAY_TOKEN -e LITELLM_MASTER_KEY -v "$PWD":/app:ro \\
      python:3.12-alpine python /app/agent_loop.py

Exit code: 0 with a final answer (or a refusal), 1 on any error or when
MAX_TURNS model calls produce no final answer.
"""

import fnmatch
import http.client
import json
import urllib.error
import urllib.request

from mcp_gateway_client import MCPError, MCPGatewayClient, RUN_HINT, env, tool_result_text

MAX_TOOLS = 128             # the per-request tool limit of OpenAI and Azure OpenAI
MAX_TOOL_RESULT_CHARS = 12000   # keep one huge tool reply from flooding the context

SYSTEM_PROMPT = ("You are a Check Point operations assistant. Use the provided MCP tools "
                 "to answer. Read before you change anything, and summarize results in "
                 "plain language. If a tool returns an error, say so plainly and do not "
                 "invent data.")
DEFAULT_PROMPT = "What is the reputation of the IP address 8.8.8.8? Use the reputation tool."


# --------------------------------------------------------------------------- #
# The brain: one OpenAI-compatible Chat Completions call through LiteLLM.
# --------------------------------------------------------------------------- #
def chat(cfg, messages, tools):
    """POST /chat/completions and return the parsed reply. Model errors stop
    the run (there is no agent without a brain), with a hint on how to fix them."""
    payload = {"model": cfg["model"], "messages": messages, "tools": tools}
    if cfg["max_tokens"]:
        payload["max_tokens"] = cfg["max_tokens"]
    req = urllib.request.Request(
        cfg["base_url"] + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {cfg['api_key']}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=cfg["timeout"]) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace").replace(cfg["api_key"], "***")[:1500]
        raise SystemExit(f"Model call failed: HTTP {e.code} from {cfg['base_url']}."
                         f"{_model_error_hint(e.code, detail, cfg['model'])}\n{detail}") from None
    except (urllib.error.URLError, OSError, http.client.HTTPException, json.JSONDecodeError) as e:
        reason = getattr(e, "reason", None) or e
        raise SystemExit(f"Model call failed: no usable reply from {cfg['base_url']}: {reason}. "
                         f"Run inside the lab network; a slow local model may need "
                         f"LLM_TIMEOUT=600.") from None


def _model_error_hint(code, detail, model):
    low = detail.lower()
    if "no connected db" in low or "invalid proxy server token" in low or "no api key passed" in low:
        return " LITELLM_MASTER_KEY does not match the key LiteLLM runs with (use the lab .env)."
    if "authenticationerror" in low or "api key" in low:
        return (" The model provider rejected LiteLLM's provider key. Fix the key in .env, "
                "then run: docker compose up -d litellm")
    if "invalid model name" in low or code == 404:
        return f" LiteLLM has no model named '{model}'. Check: docker logs litellm"
    return ""


def upstream_model(cfg):
    """Ask LiteLLM which provider model serves ``lab-chat`` (best effort, for the banner)."""
    req = urllib.request.Request(cfg["base_url"] + "/model/info",
                                 headers={"Authorization": f"Bearer {cfg['api_key']}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            for entry in json.loads(resp.read()).get("data") or []:
                if entry.get("model_name") == cfg["model"]:
                    return (entry.get("litellm_params") or {}).get("model")
    except Exception:  # noqa: BLE001 - only used for an informational line
        return None
    return None


# --------------------------------------------------------------------------- #
# The tools: pick the MCP tools this agent may use and map them to OpenAI tools.
# --------------------------------------------------------------------------- #
def select_tools(mcp_tools, spec):
    """Keep the tools whose name matches any comma-separated pattern in spec."""
    patterns = [p.strip() for p in spec.split(",") if p.strip()]
    return [t for t in mcp_tools if any(fnmatch.fnmatchcase(t["name"], p) for p in patterns)]


def mcp_tool_to_openai(tool):
    """MCP's ``inputSchema`` is already JSON Schema, which is what an OpenAI
    function's ``parameters`` wants. Only the dialect marker is dropped."""
    schema = dict(tool.get("inputSchema") or {})
    schema.pop("$schema", None)
    schema.setdefault("type", "object")
    schema.setdefault("properties", {})
    return {"type": "function", "function": {
        "name": tool["name"],
        "description": (tool.get("description") or "")[:1024],
        "parameters": schema,
    }}


def run_tool(client, allowed, name, raw_args):
    """Execute one tool call and return (text for the model, is_error).
    Never raises: a failed call goes back to the model as an error result."""
    if name not in allowed:
        return f"ERROR: tool '{name}' is not available to this agent.", True
    try:
        args = json.loads(raw_args or "{}") if isinstance(raw_args, str) else (raw_args or {})
    except json.JSONDecodeError as e:
        return f"ERROR: the arguments were not valid JSON ({e}). Call the tool again.", True
    if not isinstance(args, dict):
        return "ERROR: the arguments must be a JSON object.", True
    try:
        result = client.call_tool(name, args)
    except MCPError as e:          # HTTP error, timeout, JSON-RPC error
        return f"ERROR: the tool call failed: {e}", True
    text = tool_result_text(result) or "(the tool returned no content)"
    if len(text) > MAX_TOOL_RESULT_CHARS:
        text = (text[:MAX_TOOL_RESULT_CHARS]
                + f"\n[truncated: {len(text) - MAX_TOOL_RESULT_CHARS} more characters]")
    if result.get("isError"):
        return "ERROR: " + text, True
    return text, False


def _number(name, default, cast=int, minimum=0):
    raw = env(name, str(default))
    try:
        value = cast(raw)
    except ValueError:
        raise SystemExit(f"{name}={raw!r} is not a number.") from None
    if value < minimum:
        raise SystemExit(f"{name} must be at least {minimum} (got {raw}).")
    return value


def load_config():
    api_key = env("LITELLM_MASTER_KEY")
    if not api_key:
        raise SystemExit("LITELLM_MASTER_KEY is not set. The lab model (LiteLLM) needs the key "
                         "from the lab .env. " + RUN_HINT.replace("<script>", "agent_loop")
                         .replace("<vars>", "MCP_GATEWAY_TOKEN|LITELLM_MASTER_KEY"))
    return {
        "api_key": api_key,
        "base_url": env("LITELLM_BASE_URL", "http://litellm:4000/v1").rstrip("/"),
        "model": env("LAB_MODEL", "lab-chat"),
        "max_tokens": _number("MAX_TOKENS", 0),
        "max_turns": _number("MAX_TURNS", 8, minimum=1),
        "timeout": _number("LLM_TIMEOUT", 300, cast=float, minimum=1),
    }


def main():
    cfg = load_config()
    client = MCPGatewayClient()
    try:
        with client:
            agent(cfg, client)
    except MCPError as e:
        raise SystemExit(f"ERROR: {e}") from None


def agent(cfg, client):
    # 1: MCP handshake + tool discovery (see mcp_gateway_client.py).
    client.initialize()
    mcp_tools = client.list_tools()

    # 2: scope the tools, then map them to the OpenAI tool schema.
    spec = env("MCP_TOOLS", "reputation_*")
    chosen = select_tools(mcp_tools, spec)
    if not chosen:
        raise SystemExit(f"MCP_TOOLS='{spec}' matches none of the {len(mcp_tools)} gateway tools. "
                         f"Run mcp_gateway_client.py to see the tool names.")
    if len(chosen) > MAX_TOOLS:
        raise SystemExit(f"MCP_TOOLS='{spec}' matches {len(chosen)} tools; most providers accept "
                         f"at most {MAX_TOOLS}. Narrow it, e.g. MCP_TOOLS='show_access_*'.")
    tools = [mcp_tool_to_openai(t) for t in chosen]
    allowed = {t["name"] for t in chosen}

    upstream = upstream_model(cfg)
    print(f"-> gateway exposed {len(mcp_tools)} tools; binding {len(tools)} ({spec}) "
          f"to {cfg['model']}" + (f" (LiteLLM routes it to {upstream})" if upstream else ""))
    where = ("local Ollama, so tool data stays inside the lab" if (upstream or "").startswith("ollama")
             else "the model provider behind it. Use lab data only")
    print(f"-> note: tool results are sent to {where}.")

    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": env("USER_PROMPT", DEFAULT_PROMPT)}]

    # 3 + 4: the tool-use loop.
    for turn in range(1, cfg["max_turns"] + 1):
        reply = chat(cfg, messages, tools)
        choice = (reply.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        finish = choice.get("finish_reason")
        calls = message.get("tool_calls") or []
        content = message.get("content") or ""

        if finish == "length":
            # Cut off mid-reply: the text, or a tool call's JSON, is incomplete.
            if content:
                print("\n=== PARTIAL ANSWER ===\n" + content)
            raise SystemExit("-> stopped: the reply hit the token limit (finish_reason=length). "
                             "Set a larger MAX_TOKENS and run again.")

        if not calls:
            # No tool requested: this is the final answer (or a refusal).
            if message.get("refusal") or finish == "content_filter":
                print("\n=== REFUSED ===\n" + (message.get("refusal") or content
                                               or "The provider's content filter blocked the reply."))
            else:
                print("\n=== ANSWER ===\n" + (content or "(the model returned no text)"))
            return

        # Some providers decide on tool use without setting finish_reason to
        # "tool_calls", so the presence of tool_calls is what we act on.
        if content:
            print(f"-> model says: {content.strip()[:300]}")
        # Echo the assistant turn back exactly as a clean OpenAI message: every
        # tool_call id must be answered by one "tool" message below.
        clean_calls = []
        for i, call in enumerate(calls):
            fn = call.get("function") or {}
            args = fn.get("arguments")
            clean_calls.append({
                "id": call.get("id") or f"call_{turn}_{i}",
                "type": "function",
                "function": {"name": fn.get("name", ""),
                             "arguments": args if isinstance(args, str) else json.dumps(args or {})},
            })
        messages.append({"role": "assistant", "content": content or None, "tool_calls": clean_calls})

        for call in clean_calls:
            name, args = call["function"]["name"], call["function"]["arguments"]
            print(f"-> model called {name}({args[:200]})")
            output, failed = run_tool(client, allowed, name, args)
            print(f"   <- {len(output)} characters" + (" (error, sent back to the model)" if failed else ""))
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": output})

    raise SystemExit(f"-> stopped: no final answer after MAX_TURNS={cfg['max_turns']} model calls.")


if __name__ == "__main__":
    main()
