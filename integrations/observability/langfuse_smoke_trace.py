#!/usr/bin/env python3
"""Post one test trace to a self-hosted Langfuse via its ingestion API.

Two jobs:
  1. Smoke-test that Langfuse is up and your keys work end to end (run it, then
     look for the trace in the UI at https://trace.<domain>).
  2. Reference for the n8n "Code node" path in INTEGRATION.md section 4c — n8n has
     no native Langfuse callback, so this is how you'd emit a span by hand.

Stdlib only (urllib) — no pip, no langfuse SDK. Reads config from env:

  LANGFUSE_HOST         default http://langfuse:3000  (internal container name)
  LANGFUSE_PUBLIC_KEY   pk-lf-...   (required)
  LANGFUSE_SECRET_KEY   sk-lf-...   (required)

Example (run inside the stack so `langfuse` resolves):
  LANGFUSE_PUBLIC_KEY=pk-lf-... LANGFUSE_SECRET_KEY=sk-lf-... \
    docker compose exec -T n8n python3 - < langfuse_smoke_trace.py

The Langfuse ingestion endpoint is POST /api/public/ingestion with HTTP Basic
auth (public key = username, secret key = password) and a {"batch": [...]} body of
typed events. Here we send one trace-create plus one nested generation-create so
the trace shows a prompt/response and token usage.
"""
import base64
import datetime
import json
import os
import sys
import urllib.error
import urllib.request
import uuid


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def main() -> int:
    host = os.environ.get("LANGFUSE_HOST", "http://langfuse:3000").rstrip("/")
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY", "")

    if not public_key or not secret_key:
        print("ERROR: set LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY in the env.",
              file=sys.stderr)
        return 2

    trace_id = str(uuid.uuid4())
    gen_id = str(uuid.uuid4())
    ts = _now_iso()

    # A batch of typed ingestion events. `id` on each event is an idempotency key
    # for the event itself; `body.id` is the trace/observation id in the UI.
    batch = {
        "batch": [
            {
                "id": str(uuid.uuid4()),
                "type": "trace-create",
                "timestamp": ts,
                "body": {
                    "id": trace_id,
                    "name": "smoke-test",
                    "input": {"question": "Is Langfuse receiving traces?"},
                    "output": {"answer": "Yes — this trace proves ingestion works."},
                    "tags": ["smoke-test", "cp-agentic-mcp-playground"],
                },
            },
            {
                "id": str(uuid.uuid4()),
                "type": "generation-create",
                "timestamp": ts,
                "body": {
                    "id": gen_id,
                    "traceId": trace_id,
                    "name": "example-generation",
                    "model": "smoke-test-model",
                    "input": [{"role": "user", "content": "ping"}],
                    "output": {"role": "assistant", "content": "pong"},
                    "usage": {"input": 3, "output": 1, "unit": "TOKENS"},
                },
            },
        ]
    }

    data = json.dumps(batch).encode("utf-8")
    token = base64.b64encode(
        "{0}:{1}".format(public_key, secret_key).encode("utf-8")
    ).decode("ascii")

    req = urllib.request.Request(
        "{0}/api/public/ingestion".format(host),
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": "Basic {0}".format(token),
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8", "replace")
            print("HTTP {0}".format(resp.status))
            print(body)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        print("HTTP {0} from Langfuse ingestion:".format(exc.code), file=sys.stderr)
        print(detail, file=sys.stderr)
        if exc.code in (401, 403):
            print("-> auth failed: check LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY.",
                  file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print("Could not reach Langfuse at {0}: {1}".format(host, exc.reason),
              file=sys.stderr)
        print("-> run this inside the stack so 'langfuse' resolves, or set "
              "LANGFUSE_HOST.", file=sys.stderr)
        return 1

    print("\nSent trace id: {0}".format(trace_id))
    print("Open https://trace.<your-domain> -> project 'Agents' -> Traces to see it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
