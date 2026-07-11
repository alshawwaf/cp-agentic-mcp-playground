# Integration — Visible RAG (`rag-cp-docs`)

Everything the integrator must paste into the **shared** files to wire up the
Visible RAG feature. This branch adds **new files only**; the blocks below are the
edits to `docker-compose.yml` and `.env-example` that are intentionally *not* made
here so five features can merge in parallel.

**What this feature is:** a one-shot ingester that embeds a small bundled corpus of
Check Point concept snippets into Qdrant, plus an n8n chat agent that retrieves +
cites them. See `docs/guides/Visible_RAG.md` for the full walkthrough.

**New files in this feature**

| File | Purpose |
|------|---------|
| `integrations/rag-cp-docs/ingest.py` | STDLIB-only one-shot ingester (embed → Qdrant upsert). |
| `integrations/rag-cp-docs/corpus/*.md` | 8 short, clearly-marked **demo** snippets (not official docs). |
| `n8n/backup/workflows/rag-cp-docs-agent.json` | Chat agent; `search_cp_docs` tool + Azure-default model. |
| `n8n/backup/workflows/rag-cp-docs-retriever.json` | Sub-workflow the tool runs: embed → Qdrant search → format. |
| `docs/guides/Visible_RAG.md` | Concept + run + demo script. |

---

## 1. Qdrant service (add if the stack does not already run one)

> The repo README notes Qdrant is **not** part of the compose stack today (only the
> leftover `quadrant/` backup dir exists). This feature needs a running Qdrant, so
> add the block below **if `docker compose ps` shows no `qdrant` service**. It is
> internal-only (demo network, no published host port) — same posture as the MCP
> gateway.

Paste under `services:` in `docker-compose.yml`:

```yaml
  # ──────────────── Qdrant (vector DB for RAG) ────────────────
  qdrant:
    image: qdrant/qdrant:v1.12.4
    pull_policy: missing
    container_name: qdrant
    networks: [ "demo" ]
    restart: unless-stopped
    volumes:
      - qdrant_storage:/qdrant/storage
    environment:
      # leave blank for no-auth on the private net; set QDRANT_API_KEY to require it
      - QDRANT__SERVICE__API_KEY=${QDRANT_API_KEY:-}
    healthcheck:
      # Qdrant has no shell/curl; TCP-probe the REST port from the Python base image is done
      # by dependents. This lightweight check keeps depends_on: service_healthy usable.
      test: [ "CMD-SHELL", "bash -c ':> /dev/tcp/127.0.0.1/6333' || exit 1" ]
      interval: 5s
      timeout: 5s
      retries: 20
```

Add the named volume under the top-level `volumes:` block:

```yaml
  qdrant_storage:
```

**Optional** — expose the Qdrant dashboard at `qdrant.{{DOMAIN}}` (keep it internal
unless you have a reason; if you expose it, set `QDRANT_API_KEY`). Add to the
`qdrant` service and put it on `dokploy-network` too (`networks: [ "demo", "dokploy-network" ]`):

```yaml
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.qdrant-web.rule=Host(`qdrant.{{DOMAIN}}`)"
      - "traefik.http.routers.qdrant-web.entrypoints=web"
      - "traefik.http.routers.qdrant-web.service=qdrant-svc"
      - "traefik.http.routers.qdrant-websecure.rule=Host(`qdrant.{{DOMAIN}}`)"
      - "traefik.http.routers.qdrant-websecure.entrypoints=websecure"
      - "traefik.http.routers.qdrant-websecure.tls.certresolver=letsencrypt"
      - "traefik.http.routers.qdrant-websecure.service=qdrant-svc"
      - "traefik.http.services.qdrant-svc.loadbalancer.server.port=6333"
      - "traefik.docker.network=dokploy-network"
```

---

## 2. `rag-ingest` one-shot service

The ingester. It runs `ingest.py` once, populates the `cp_docs` collection, and
exits. Paste under `services:` in `docker-compose.yml`:

```yaml
  # ──────────────── Visible RAG — one-shot corpus ingester ────────────────
  rag-ingest:
    image: python:3.12-alpine
    pull_policy: missing
    container_name: rag-ingest
    networks: [ "demo" ]
    restart: "no"
    working_dir: /app
    # ingest.py is STDLIB-only (urllib) — nothing to pip install.
    volumes:
      - ./integrations/rag-cp-docs:/app:ro
    environment:
      - OLLAMA_URL=http://ollama-cpu:11434
      - QDRANT_URL=http://qdrant:6333
      - EMBED_MODEL=nomic-embed-text
      - COLLECTION=cp_docs
      - QDRANT_API_KEY=${QDRANT_API_KEY:-}
    command: [ "python3", "ingest.py" ]
    depends_on:
      qdrant:
        condition: service_healthy
      ollama-cpu:
        condition: service_healthy
```

Notes:
- **Idempotent.** Each run DELETEs and recreates `cp_docs`, then upserts one point
  per snippet with stable ids — safe to re-run any time the corpus changes.
- **Self-tuning vector size.** It reads the embedding dimensionality from the model
  (`nomic-embed-text` = 768) and creates the collection to match — no hardcoded size.
- **Model auto-pull.** If Ollama doesn't have `nomic-embed-text`, the script pulls it
  once (POST `/api/pull`) and retries. First run can take a few minutes.

---

## 3. `.env-example` keys

`QDRANT_API_KEY` already exists in `.env-example` (leave blank for the no-auth
internal default). Add the embedding model to the pull list so it's fetched eagerly
instead of on first ingest — append `nomic-embed-text` to `OLLAMA_MODELS`:

```dotenv
# RAG embeddings (Visible RAG / rag-cp-docs). Small (~274MB), CPU-friendly.
# Append to your existing OLLAMA_MODELS list, e.g.:
OLLAMA_MODELS=gemma4:e2b,qwen3.5:4b,nomic-embed-text
```

No new secrets are introduced. If you enable Qdrant auth, set `QDRANT_API_KEY` and
it is passed to both Qdrant and the ingester.

---

## 4. Enable / run

```sh
# one-time (or after editing the corpus): pull the embedder + ingest
docker compose up -d qdrant ollama-cpu
docker exec -it ollama-cpu ollama pull nomic-embed-text   # skip if in OLLAMA_MODELS
docker compose up rag-ingest                              # runs once, then exits
```

Then in n8n, confirm the two workflows imported (they ship in
`n8n/backup/workflows/` and are picked up by the normal n8n import on deploy):

- **CP Docs — Visible RAG Agent** (the chat UI)
- **RAG · CP Docs Retriever (embed + Qdrant search)** (the tool sub-workflow)

**One manual check (untested-across-versions wiring):** open *CP Docs — Visible RAG
Agent* → the `search_cp_docs` tool node → verify its **Workflow** is set to *RAG · CP
Docs Retriever*. If the reference didn't survive import, re-select it and confirm the
input field `query` maps to `{{ $fromAI('query', ...) }}`. The retriever can be tested
on its own by pinning `{ "query": "How do I enable Identity Awareness?" }` on its
trigger and clicking Execute.

**Live-deploy summary:** add the `qdrant` + `rag-ingest` compose services and the
`qdrant_storage` volume, append `nomic-embed-text` to `OLLAMA_MODELS`, then
`docker compose up rag-ingest` once — the two RAG workflows import with the normal n8n
provisioning.
