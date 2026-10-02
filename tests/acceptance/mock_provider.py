#!/usr/bin/env python3
"""Mock model provider for the acceptance check KEYS (run.sh --mock-provider). Stdlib only.

run.sh starts it in a throwaway python:3.12-alpine container with NO network (--network none),
and a throwaway LiteLLM container joins that container's network namespace. LiteLLM therefore
reaches this mock at http://127.0.0.1:8099 and nothing else: a provider key can never leave the
machine during the test, even if a base URL were ignored.

It speaks just enough of five wire formats:
  OpenAI     POST .../chat/completions           (Azure OpenAI too: /openai/v1/... and /openai/deployments/...)
  Anthropic  POST /v1/messages
  Gemini     POST .../models/<model>:generateContent
  Ollama     POST /api/chat
A request that carries tools, and whose last message is the user's, gets a tool call to the
first tool; any other request gets a short text answer. No model runs.

Every request is appended to /tmp/mock/requests.jsonl with its path, its JSON body and the
SHA-256 of each credential header. The headers themselves are never stored or printed.
Adapted from the LiteLLM package test harness (lab-test/harness/litellm/mock_upstream.py).
"""
import hashlib
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlencode, urlsplit

LOG_DIR = os.environ.get("MOCK_LOG_DIR", "/tmp/mock")
LOG = os.path.join(LOG_DIR, "requests.jsonl")
LOCK = threading.Lock()
TOOL_ARGS = {"detail": "short"}
TOOL_ARGS_STR = json.dumps(TOOL_ARGS)
TOOL_CALL_ID = "call_acceptance_0001"
ANSWER = "Acceptance test answer from the mock provider."


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest() if value is not None else None


def last_user_text(messages):
    for message in reversed(messages or []):
        if message.get("role") == "user":
            content = message.get("content")
            if isinstance(content, list):
                return " ".join(p.get("text", "") for p in content if isinstance(p, dict))
            return str(content or "")
    return ""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # no access log
        pass

    def _send(self, code, obj, ctype="application/json"):
        data = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _record(self, body):
        parts = urlsplit(self.path)
        query = dict(parse_qsl(parts.query))
        goog = query.pop("key", None) or self.headers.get("x-goog-api-key")
        entry = {
            "path": parts.path,
            "query": urlencode(query),
            "auth_sha": sha(self.headers.get("Authorization")),
            "api_key_sha": sha(self.headers.get("api-key")),
            "x_api_key_sha": sha(self.headers.get("x-api-key")),
            "goog_key_sha": sha(goog),
            "body": body,
        }
        with LOCK:
            with open(LOG, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, {"ok": True})
        self._record(None)
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            body = {"_unparsed": True}
        self._record(body)
        path = urlsplit(self.path).path
        if path.endswith("/chat/completions"):
            return self.openai(body)
        if path.endswith("/v1/messages"):
            return self.anthropic(body)
        if ":generateContent" in path or ":streamGenerateContent" in path:
            return self.gemini(body, path)
        if path == "/api/chat":
            return self.ollama(body)
        return self._send(404, {"error": "unknown mock path", "path": path})

    # OpenAI and Azure OpenAI --------------------------------------------------------------
    def openai(self, body):
        messages = body.get("messages") or []
        tools = body.get("tools") or []
        if tools and messages and messages[-1].get("role") == "user":
            message = {"role": "assistant", "content": None, "tool_calls": [{
                "id": TOOL_CALL_ID, "type": "function",
                "function": {"name": tools[0]["function"]["name"], "arguments": TOOL_ARGS_STR}}]}
            finish = "tool_calls"
        else:
            message = {"role": "assistant", "content": ANSWER}
            finish = "stop"
        if body.get("stream"):
            return self.openai_stream(body, message, finish)
        return self._send(200, {
            "id": "chatcmpl-acceptance", "object": "chat.completion", "created": 1700000000,
            "model": body.get("model", "mock"),
            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}})

    def openai_stream(self, body, message, finish):
        base = {"id": "chatcmpl-acceptance", "object": "chat.completion.chunk", "created": 1700000000,
                "model": body.get("model", "mock")}
        chunks = [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}]
        if message.get("tool_calls"):
            call = message["tool_calls"][0]
            chunks.append({"index": 0, "delta": {"tool_calls": [{
                "index": 0, "id": call["id"], "type": "function",
                "function": {"name": call["function"]["name"], "arguments": TOOL_ARGS_STR}}]},
                "finish_reason": None})
        else:
            chunks.append({"index": 0, "delta": {"content": message["content"]}, "finish_reason": None})
        chunks.append({"index": 0, "delta": {}, "finish_reason": finish})
        out = b"".join(b"data: " + json.dumps(dict(base, choices=[c])).encode() + b"\n\n" for c in chunks)
        out += b"data: [DONE]\n\n"
        return self._send(200, out, "text/event-stream")

    # Anthropic ----------------------------------------------------------------------------
    def anthropic(self, body):
        messages = body.get("messages") or []
        tools = body.get("tools") or []
        last = messages[-1] if messages else {}
        answered = isinstance(last.get("content"), list) and any(
            isinstance(p, dict) and p.get("type") == "tool_result" for p in last["content"])
        if tools and not answered:
            content = [{"type": "tool_use", "id": "toolu_acceptance_0001", "name": tools[0]["name"],
                        "input": TOOL_ARGS}]
            stop = "tool_use"
        else:
            content = [{"type": "text", "text": ANSWER}]
            stop = "end_turn"
        return self._send(200, {"id": "msg_acceptance", "type": "message", "role": "assistant",
                                "model": body.get("model", "mock"), "content": content,
                                "stop_reason": stop, "stop_sequence": None,
                                "usage": {"input_tokens": 11, "output_tokens": 7}})

    # Gemini -------------------------------------------------------------------------------
    def gemini(self, body, path):
        tools = body.get("tools") or []
        decls = [d for t in tools for d in (t.get("functionDeclarations") or t.get("function_declarations") or [])]
        contents = body.get("contents") or []
        last_parts = (contents[-1].get("parts") if contents else []) or []
        answered = any("functionResponse" in p or "function_response" in p for p in last_parts)
        if decls and not answered:
            parts = [{"functionCall": {"name": decls[0]["name"], "args": TOOL_ARGS}}]
        else:
            parts = [{"text": ANSWER}]
        return self._send(200, {
            "candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": "STOP", "index": 0}],
            "usageMetadata": {"promptTokenCount": 11, "candidatesTokenCount": 7, "totalTokenCount": 18},
            "modelVersion": path.split("/models/")[-1].split(":")[0]})

    # Ollama -------------------------------------------------------------------------------
    def ollama(self, body):
        tools = body.get("tools") or []
        messages = body.get("messages") or []
        if tools and messages and messages[-1].get("role") == "user":
            message = {"role": "assistant", "content": "",
                       "tool_calls": [{"function": {"name": tools[0]["function"]["name"], "arguments": TOOL_ARGS}}]}
        else:
            message = {"role": "assistant", "content": ANSWER}
        return self._send(200, {"model": body.get("model"), "created_at": "2026-10-01T00:00:00Z",
                                "message": message, "done": True, "done_reason": "stop",
                                "prompt_eval_count": 11, "eval_count": 7})


def main():
    os.makedirs(LOG_DIR, exist_ok=True)
    open(LOG, "a", encoding="utf-8").close()
    host = os.environ.get("MOCK_HOST", "127.0.0.1")
    port = int(os.environ.get("MOCK_PORT", "8099"))
    print("mock provider listening on {0}:{1}".format(host, port), flush=True)
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
