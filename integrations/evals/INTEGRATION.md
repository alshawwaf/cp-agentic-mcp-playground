# Evals Harness — Integration Notes

Everything an integrator must add to the **shared** repo files to wire up the
evals harness lives here, so the feature stays add-files-only and merges cleanly
alongside the other parallel features. Copy the blocks below verbatim.

**Feature dir:** `integrations/evals/`
- `run_evals.py` — the stdlib-only harness (no pip, no deps).
- `evals_cases.json` — the 8 shipped cases; edit this, not the code.
- `docs/guides/Evals_Harness.md` — the why/how guide.

---

## 0. TL;DR — the fastest way to run it

You do **not** need to touch compose at all to use this. From a laptop against a
live deployment:

```sh
BASE_URL=https://n8n.<your-domain> python3 integrations/evals/run_evals.py
```

It prints a pass/fail table and writes `evals_report.md` + `evals_report.json` to
the current directory. Exit code is 0 (all pass) or 1 (any fail) for CI. The
compose service in §2 is **optional** — it's only for running the suite
automatically inside the stack.

---

## 1. `.env-example` — keys to add

**None required.** The harness has zero required configuration and reads
everything from `evals_cases.json` + optional env at call time. If you want the
optional compose one-shot (below) to target a custom hostname, add this one
documented, optional line to `.env-example`:

```dotenv
# ---------------- Evals harness (optional) ----------------
# Where run_evals.py sends chat turns. Inside the demo network the default
# http://n8n:5678 is correct; set this only to point the one-shot elsewhere
# (e.g. https://n8n.${YOUR_DOMAIN}). See docs/guides/Evals_Harness.md.
EVALS_BASE_URL=http://n8n:5678
```

No secrets are involved. The harness does not authenticate to anything: n8n chat
webhooks are unauthenticated POST endpoints on the internal `demo` network.

> Note: the `guarded_chat_injection_blocked` case only passes when **`LAKERA_API_KEY`**
> is already set (that key is defined in `.env-example` for the guarded-chat
> feature). Without it the guard is skipped by design and the case fails "Guard
> not configured" — expected, and the eval is telling you the control is off.

---

## 2. `docker-compose.yml` — optional one-shot service

Paste this service block into `docker-compose.yml` (it mirrors the existing
`builders-import` one-shot: same `python:3.12-alpine`, same `./integrations`
read-only mount, same `demo` network). It runs the suite once on `docker compose
up` and writes the reports to `n8n/shared` (already a mounted volume in this repo)
so you can read them from the host at `./n8n/shared/evals_report.md`.

```yaml
  # One-shot eval run (parity with builders-import): on `docker compose up` it
  # POSTs the evals_cases.json cases to each agent's n8n chat webhook, scores the
  # answers, and writes evals_report.md/.json to ./n8n/shared. Exit code is 0 if
  # every case passes, 1 otherwise. Purely opt-in — start it explicitly with
  # `docker compose up evals-run`; it never blocks the rest of the stack.
  evals-run:
    image: python:3.12-alpine
    container_name: evals-run
    networks: [ "demo" ]
    depends_on:
      - n8n
    volumes:
      - ./integrations:/integrations:ro
      - ./n8n/shared:/out
    environment:
      # Inside the network n8n is reachable by service name; override to a public
      # host (e.g. https://n8n.${YOUR_DOMAIN}) to eval a remote deployment.
      - BASE_URL=${EVALS_BASE_URL:-http://n8n:5678}
      - CASES_FILE=/integrations/evals/evals_cases.json
      - OUT_DIR=/out
      - TIMEOUT=120
    entrypoint: [ "python3", "/integrations/evals/run_evals.py" ]
    restart: "no"
```

There are **no Traefik labels** and **no published port**: the harness is a
command-line tool with no web UI, and it reaches n8n over the internal `demo`
network. (If you ever wanted a web report you'd serve `n8n/shared/evals_report.md`
from an existing UI — not add ingress here.)

---

## 3. Filling in the `webhookId`s

The cases ship with the **real** chat-trigger `webhookId`s already committed in
`n8n/backup/workflows/*.json` — the same ids the shipped `CP Agents — Nightly
Self-QA` workflow probes — so the suite works against a stock deployment with no
edits. You only need to touch them if you re-create a workflow and n8n assigns a
new id. To read a current id:

- **From the n8n UI:** open the workflow → click **When chat message received** →
  the id is the middle segment of the webhook URL `…/webhook/<THIS>/chat`.
- **From the repo:**
  ```sh
  jq -r '.name + "  " + (.nodes[]|select(.type|endswith("chatTrigger")).webhookId)' \
    n8n/backup/workflows/reputation-service-mcp-agent.json
  ```

Paste the id into the matching case in `integrations/evals/evals_cases.json`. No
code change, no redeploy of the harness.

---

## 4. One-time live-deploy commands

Nothing to build. Pick whichever fits:

```sh
# A) From a laptop against the live deployment (recommended first run):
BASE_URL=https://n8n.<your-domain> python3 integrations/evals/run_evals.py

# B) From inside the running stack, no compose edit needed:
docker compose exec n8n python3 - < integrations/evals/run_evals.py
#   (defaults to BASE_URL=http://n8n:5678; reports land in the container CWD)

# C) As the optional one-shot service from §2 (reports -> ./n8n/shared):
docker compose up evals-run
cat ./n8n/shared/evals_report.md
```

Run it before every change and in CI on every PR; let the nightly self-QA
workflow watch the deployed stack in between (see the guide's §6 for how the two
complement each other).
