#!/usr/bin/env python3
"""Send one test trace to the lab's Langfuse and confirm it can be read back.

Use it to prove that Langfuse is up and that LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY
work, before you look for agent traces.

Run it inside the lab network. The `litellm` container already has Python and the
Langfuse keys, so from the repository root:

    docker compose exec -T litellm python - < integrations/observability/langfuse_smoke_trace.py

(With 1Password nothing changes: the running container already holds the resolved keys.)

Settings, read from the environment:
  LANGFUSE_PUBLIC_KEY   pk-lf-...  (required)
  LANGFUSE_SECRET_KEY   sk-lf-...  (required)
  LANGFUSE_HOST         default http://langfuse:3000 (the service name inside the lab network)

What it does: POST /api/public/ingestion (HTTP Basic auth: public key as user name,
secret key as password) with one trace-create and one nested generation-create event,
then GET /api/public/traces/<id> until the trace is readable. Exit code 0 = the trace
landed, 1 = Langfuse refused it or never showed it, 2 = keys missing.
Stdlib only. The keys are never printed.
"""
import base64
import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

WAIT_SECONDS = 30


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _request(host, auth, path, payload=None):
    req = urllib.request.Request(
        host + path,
        data=None if payload is None else json.dumps(payload).encode("utf-8"),
        method="GET" if payload is None else "POST",
        headers={"Content-Type": "application/json", "Authorization": "Basic " + auth},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8", "replace") or "null")


def main():
    host = (os.environ.get("LANGFUSE_HOST") or "http://langfuse:3000").rstrip("/")
    public_key = (os.environ.get("LANGFUSE_PUBLIC_KEY") or "").strip()
    secret_key = (os.environ.get("LANGFUSE_SECRET_KEY") or "").strip()
    if not public_key or not secret_key:
        print("ERROR: LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY must both be set. Put them in .env "
              "(./setup.sh generates them), then run: docker compose up -d langfuse litellm", file=sys.stderr)
        return 2
    if public_key.startswith("op://") or secret_key.startswith("op://"):
        print("ERROR: the Langfuse keys are unresolved 1Password references. Start the stack with: "
              "op run --env-file=.env -- docker compose up -d", file=sys.stderr)
        return 2
    auth = base64.b64encode("{0}:{1}".format(public_key, secret_key).encode("utf-8")).decode("ascii")

    trace_id = str(uuid.uuid4())
    now = _now_iso()
    batch = {"batch": [
        {"id": str(uuid.uuid4()), "type": "trace-create", "timestamp": now, "body": {
            "id": trace_id,
            "name": "lab-smoke-test",
            "input": {"question": "Is Langfuse receiving traces?"},
            "output": {"answer": "Yes. This trace proves that ingestion works."},
            "tags": ["smoke-test"],
        }},
        {"id": str(uuid.uuid4()), "type": "generation-create", "timestamp": now, "body": {
            "id": str(uuid.uuid4()),
            "traceId": trace_id,
            "name": "smoke-test-generation",
            "model": "smoke-test-model",
            "startTime": now,
            "endTime": now,
            "input": [{"role": "user", "content": "ping"}],
            "output": {"role": "assistant", "content": "pong"},
            "usage": {"input": 3, "output": 1, "unit": "TOKENS"},
        }},
    ]}

    try:
        status, body = _request(host, auth, "/api/public/ingestion", batch)
    except urllib.error.HTTPError as exc:
        print("ERROR: Langfuse answered HTTP {0} to the ingestion call.".format(exc.code), file=sys.stderr)
        if exc.code in (401, 403):
            print("The keys were refused: check LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY in .env. "
                  "They must match the project keys Langfuse created on first start.", file=sys.stderr)
        return 1
    except (urllib.error.URLError, OSError) as exc:
        print("ERROR: could not reach Langfuse at {0} ({1}). Run this inside the lab network "
              "(see the command at the top of this file) and check: docker compose ps langfuse".format(
                  host, getattr(exc, "reason", exc)), file=sys.stderr)
        return 1

    # Langfuse answers 207 Multi-Status: each event is listed under "successes" or "errors".
    errors = (body or {}).get("errors") or []
    if errors:
        print("ERROR: Langfuse rejected {0} of {1} events: {2}".format(
            len(errors), len(batch["batch"]), json.dumps(errors)[:500]), file=sys.stderr)
        return 1
    print("Sent trace {0} (HTTP {1}). Waiting for Langfuse to show it...".format(trace_id, status))

    deadline = time.time() + WAIT_SECONDS
    while time.time() < deadline:
        try:
            status, trace = _request(host, auth, "/api/public/traces/" + trace_id)
            if status == 200 and (trace or {}).get("id") == trace_id:
                print("OK: trace 'lab-smoke-test' is visible in Langfuse. In the Langfuse UI open the "
                      "project, then Traces, and filter by the tag smoke-test.")
                return 0
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                print("ERROR: Langfuse answered HTTP {0} when reading the trace back.".format(exc.code),
                      file=sys.stderr)
                return 1
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(2)
    print("ERROR: Langfuse accepted the trace but did not show it within {0} s. Check: "
          "docker compose logs langfuse".format(WAIT_SECONDS), file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
