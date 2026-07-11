# Observability — Langfuse (self-hosted) tracing

**Goal:** stop treating the agents as black boxes. With Langfuse wired in, every
Langflow / Flowise run emits a **trace** — the prompts, each tool call, token
counts, latency, and (where the model reports pricing) cost. Learners open one URL
and *see* what the agent actually did.

This file is the **integrator's paste sheet**. It only ADDS things; it does not
edit any shared file in place. Copy the blocks below into `docker-compose.yml` and
`.env-example`, run the one-time commands, and you're done. Everything uses
`{{DOMAIN}}` / `${ENV}` placeholders — no tenant values, no secrets committed.

Why Langfuse **v2**: it is a single Next.js container backed by **Postgres only** —
light, and it slots straight onto the stack Postgres you already run. v3 is the
newer line but pulls in **ClickHouse + Redis + S3/MinIO**; only reach for it if you
specifically need v3 features (see the last section).

---

## 1. `.env-example` additions

Append this block (a good home is right after the
`# ---------------- Langflow / Flowise / Open-WebUI ----------------` section):

```dotenv
# ---------------- Observability: Langfuse (self-hosted, v2) ----------------
# Web UI is published at https://trace.${DOMAIN} via Traefik (see compose block).
# Langfuse v2 is Postgres-backed only — it reuses the stack Postgres by default.

# Session + hashing secrets. Generate BOTH (values are examples — replace):
#   openssl rand -base64 32   ->  NEXTAUTH_SECRET and SALT
#   openssl rand -hex 32      ->  LANGFUSE_ENCRYPTION_KEY (256-bit hex)
# Or run: python3 integrations/observability/gen_secrets.py
NEXTAUTH_SECRET=change_me_openssl_rand_base64_32
SALT=change_me_openssl_rand_base64_32
# Encrypts secrets Langfuse stores (LLM keys, integrations). Recommended on v2.
LANGFUSE_ENCRYPTION_KEY=change_me_openssl_rand_hex_32

# Project API keys the builders (Langflow/Flowise) send WITH their traces.
# Two ways to get them:
#   (a) leave blank, boot Langfuse, create a project in the UI, copy the keys here; OR
#   (b) pre-declare them here and let headless init bootstrap the project on first
#       boot (LANGFUSE_INIT_* in the compose block below wires these in).
# Public keys look like pk-lf-...  Secret keys look like sk-lf-...
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=

# Postgres database Langfuse uses. Default reuses the stack Postgres container
# (a DEDICATED `langfuse` database, created idempotently by langfuse-db-init).
# To point at a separate Postgres instead, just change this URL.
LANGFUSE_DATABASE_URL=postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/langfuse

# Disable Langfuse's anonymous usage telemetry (privacy-preserving default for a
# security lab). Set to true only if you want to share usage stats with Langfuse.
LANGFUSE_TELEMETRY_ENABLED=false
```

> All five secret-bearing keys ship **blank / placeholder** and must be filled per
> deployment. `NEXTAUTH_SECRET`, `SALT`, and `LANGFUSE_ENCRYPTION_KEY` are
> **generatable** — see section 2. Never commit real values.

---

## 2. Generate the secrets (one line each)

```bash
openssl rand -base64 32   # -> NEXTAUTH_SECRET
openssl rand -base64 32   # -> SALT
openssl rand -hex 32      # -> LANGFUSE_ENCRYPTION_KEY
```

Or, stdlib-only (no openssl, no pip), print all three as ready-to-paste `.env`
lines:

```bash
python3 integrations/observability/gen_secrets.py
```

If you use **headless init** (section 3), also decide your project keys up front.
They just have to start with the right prefix, e.g.:

```
LANGFUSE_PUBLIC_KEY=pk-lf-<random>
LANGFUSE_SECRET_KEY=sk-lf-<random>
```

`gen_secrets.py --with-keys` prints a matching `pk-lf-…` / `sk-lf-…` pair too.

---

## 3. `docker-compose.yml` additions

Paste **both** service blocks below into the `services:` map (a natural spot is
right after the `langflow:` service). Valid YAML, 2-space indent to match the file.

No new named volume is required — Langfuse v2 keeps **all** state in Postgres, so
the container is disposable.

```yaml
  # ──────────────── Langfuse DB bootstrap (one-shot) ────────────────
  # Creates a dedicated `langfuse` database inside the stack Postgres if it
  # doesn't exist yet. Idempotent; exits 0 on reruns. Uses the already-pulled
  # postgres:16-alpine image, so no extra pull. Skip this service entirely if
  # LANGFUSE_DATABASE_URL points at a Postgres where the DB already exists.
  langfuse-db-init:
    image: postgres:16-alpine
    container_name: langfuse-db-init
    networks: [ "demo" ]
    restart: "no"
    depends_on:
      postgres:
        condition: service_healthy
    environment:
      - PGPASSWORD=${POSTGRES_PASSWORD}
    entrypoint: /bin/sh
    command:
      - -lc
      - |
        echo ">> Ensuring 'langfuse' database exists ..."
        psql -h postgres -U "$${POSTGRES_USER}" -tc \
          "SELECT 1 FROM pg_database WHERE datname='langfuse'" | grep -q 1 \
          || psql -h postgres -U "$${POSTGRES_USER}" -c "CREATE DATABASE langfuse"
        echo ">> Done."

  # ──────────────── Langfuse (LLM observability / tracing) ────────────────
  langfuse:
    # v2 line — single Postgres-backed container. Pinned to the v2 major; bump to
    # a specific patch (e.g. langfuse/langfuse:2.95.4) for full reproducibility.
    image: langfuse/langfuse:2
    container_name: langfuse
    # demo = internal stack net (Langflow/Flowise reach http://langfuse:3000 here);
    # dokploy-network = Traefik can route trace.<domain> to it.
    networks: [ "demo", "dokploy-network" ]
    restart: unless-stopped
    depends_on:
      postgres:
        condition: service_healthy
      langfuse-db-init:
        condition: service_completed_successfully
    environment:
      - DATABASE_URL=${LANGFUSE_DATABASE_URL}
      - NEXTAUTH_URL=https://trace.{{DOMAIN}}
      - NEXTAUTH_SECRET=${NEXTAUTH_SECRET}
      - SALT=${SALT}
      - ENCRYPTION_KEY=${LANGFUSE_ENCRYPTION_KEY}
      # MUST bind all interfaces or Traefik can't reach it (default is localhost).
      - HOSTNAME=0.0.0.0
      - PORT=3000
      - TELEMETRY_ENABLED=${LANGFUSE_TELEMETRY_ENABLED:-false}
      # ── Optional headless bootstrap: create org/project/user + API keys on
      # first boot so the builders' keys work with NO manual UI step. Safe to
      # omit — if you leave these out, create the project in the UI instead.
      - LANGFUSE_INIT_ORG_ID=cp-playground
      - LANGFUSE_INIT_ORG_NAME=Check Point Playground
      - LANGFUSE_INIT_PROJECT_ID=agents
      - LANGFUSE_INIT_PROJECT_NAME=Agents
      - LANGFUSE_INIT_PROJECT_PUBLIC_KEY=${LANGFUSE_PUBLIC_KEY}
      - LANGFUSE_INIT_PROJECT_SECRET_KEY=${LANGFUSE_SECRET_KEY}
      - LANGFUSE_INIT_USER_EMAIL=${N8N_ADMIN_EMAIL}
      - LANGFUSE_INIT_USER_NAME=${N8N_ADMIN_FIRST_NAME}
      - LANGFUSE_INIT_USER_PASSWORD=${N8N_ADMIN_PASSWORD}
    labels:
      - "traefik.enable=true"
      # Two explicit routers (same pattern as flowise/langflow/open-webui): the
      # Cloudflare tunnel fronts every host to entrypoint web (:80), and a router
      # carrying TLS config never matches plain HTTP — Traefik 3.6 splits a
      # web,websecure router and propagates certresolver onto BOTH, so the
      # single-router form 404s through the tunnel.
      - "traefik.http.routers.langfuse-web.rule=Host(`trace.{{DOMAIN}}`)"
      - "traefik.http.routers.langfuse-web.entrypoints=web"
      - "traefik.http.routers.langfuse-web.service=langfuse-svc"
      - "traefik.http.routers.langfuse-websecure.rule=Host(`trace.{{DOMAIN}}`)"
      - "traefik.http.routers.langfuse-websecure.entrypoints=websecure"
      - "traefik.http.routers.langfuse-websecure.tls.certresolver=letsencrypt"
      - "traefik.http.routers.langfuse-websecure.service=langfuse-svc"
      - "traefik.http.services.langfuse-svc.loadbalancer.server.port=3000"
      - "traefik.docker.network=dokploy-network"
      # Let the dev-hub desktop embed Langfuse in an iframe (strip X-Frame-Options,
      # allow the hub in frame-ancestors) — same trick as the langflow service.
      - "traefik.http.routers.langfuse-websecure.middlewares=langfuse-hubframe"
      - "traefik.http.middlewares.langfuse-hubframe.headers.customresponseheaders.X-Frame-Options="
      - "traefik.http.middlewares.langfuse-hubframe.headers.customresponseheaders.Content-Security-Policy=frame-ancestors 'self' https://hub.{{DOMAIN}} https://*.{{DOMAIN}}"
```

### Database choice — reuse vs dedicated

| Option | What to set | When |
|---|---|---|
| **Reuse stack Postgres** (default) | `LANGFUSE_DATABASE_URL=postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/langfuse` + keep `langfuse-db-init` | Lab / PoV. Lightest. A dedicated **database** keeps Langfuse's tables out of n8n's. The stack `admin` user is a superuser, so migrations run cleanly and no `SHADOW_DATABASE_URL` is needed. |
| **Dedicated Postgres container** | Point `LANGFUSE_DATABASE_URL` at your own instance; drop `langfuse-db-init` if that DB already exists | Isolation, or if you don't want Langfuse sharing the n8n Postgres at all. |

---

## 4. Wire the builders to send traces

### 4a. Langflow — env on the existing `langflow:` service

Add these three lines to the **`environment:`** list of the **existing** `langflow`
service in `docker-compose.yml` (don't add a new service):

```yaml
      # ── Langfuse tracing (added) ──
      - LANGFUSE_PUBLIC_KEY=${LANGFUSE_PUBLIC_KEY}
      - LANGFUSE_SECRET_KEY=${LANGFUSE_SECRET_KEY}
      - LANGFUSE_HOST=http://langfuse:3000
```

Langflow auto-detects those and traces every flow run — no per-flow toggle. Verified
against **Langflow 1.10.1** (the pinned image), whose tracing service reads the
**unprefixed** `LANGFUSE_SECRET_KEY` / `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_HOST`
(newer builds also accept `LANGFUSE_BASE_URL`, which wins if both are set).

> Legacy note (only if you run an **older** Langflow): pre-1.x builds read the
> **prefixed** form `LANGFLOW_LANGFUSE_SECRET_KEY` / `LANGFLOW_LANGFUSE_PUBLIC_KEY`
> / `LANGFLOW_LANGFUSE_HOST=http://langfuse:3000`. On 1.10.1 use the unprefixed
> names above. If unsure, set both — the unused pair is simply ignored.

`http://langfuse:3000` resolves because both containers sit on the `demo` network.
Restart Langflow after adding the vars (`docker compose up -d langflow`).

### 4b. Flowise — per-chatflow Analytics (UI, one-time per flow)

Flowise attaches Langfuse per **chatflow**, not via a global env var:

1. Open the chatflow → top-right **Settings (gear)** → **Configuration** →
   **Analyse Chatflow**.
2. Pick **Langfuse** and toggle it **ON**.
3. Fill the credential fields (Flowise saves them as a reusable `langfuseApi`
   credential you can select on other flows):
   - **Langfuse Secret Key** — your `sk-lf-…` (`${LANGFUSE_SECRET_KEY}`)
   - **Langfuse Public Key** — your `pk-lf-…` (`${LANGFUSE_PUBLIC_KEY}`)
   - **Langfuse Endpoint** — `http://langfuse:3000` (both containers are on `demo`)
4. **Save**. Run the chat once; the trace appears in Langfuse under the project.

There is no compose change for Flowise — the config lives in the chatflow. (If you
later script it, Flowise stores it as an `analytic` block on the chatflow JSON with
a `langfuseApi` credential; out of scope here to keep the shared JSONs untouched.)

### 4c. n8n — the honest picture (partial tracing only)

Be straight with learners: **n8n's LangChain nodes have no first-class Langfuse
callback.** There is no "send traces to Langfuse" switch on the AI Agent / Chat
Model / MCP Client nodes, so n8n runs will **not** show up in Langfuse the way
Langflow and Flowise do out of the box. Two realistic paths, both with caveats:

1. **Trace the model calls via an OpenAI-compatible proxy that has a Langfuse
   callback.** Stand up an LLM gateway that supports Langfuse (e.g. LiteLLM proxy
   with its Langfuse callback enabled), then set the **Base URL** on n8n's OpenAI /
   Azure OpenAI Chat Model node to that proxy. Every completion the agent makes is
   then logged to Langfuse by the proxy. **Caveat:** this captures the *LLM calls*,
   not n8n's tool-call graph or the MCP gateway hops — you see prompts/tokens/cost,
   not the full agent tree.
2. **Emit spans yourself from a Code node** using Langfuse's ingestion API
   (`POST http://langfuse:3000/api/public/ingestion`, Basic auth = public:secret).
   `integrations/observability/langfuse_smoke_trace.py` is a stdlib reference for
   exactly that call — copy its body into an n8n Code node to log a custom
   trace/span. **Caveat:** it's manual instrumentation, only as complete as you make
   it.

Do not tell learners n8n has native Langfuse tracing — it doesn't. For a
**fully-traced, zero-code** experience in this stack, steer them to **Langflow**
(env, section 4a) or **Flowise** (per-flow, section 4b).

---

## 5. One-time live-deploy commands

From the repo root on the host, after pasting the blocks and filling `.env`:

```bash
# 1) generate + paste the three secrets (or edit .env by hand)
python3 integrations/observability/gen_secrets.py

# 2) create the DB and start Langfuse (langfuse-db-init runs first, idempotently)
docker compose up -d langfuse

# 3) restart Langflow so it picks up the LANGFUSE_* env
docker compose up -d langflow

# 4) (optional) smoke-test ingestion end to end — sends one trace, stdlib only
LANGFUSE_HOST=http://langfuse:3000 \
LANGFUSE_PUBLIC_KEY=pk-lf-... LANGFUSE_SECRET_KEY=sk-lf-... \
  docker compose exec -T n8n python3 - < integrations/observability/langfuse_smoke_trace.py
```

> The `{{DOMAIN}}` placeholder in the Traefik labels and `NEXTAUTH_URL` is resolved
> to your real domain by the same deploy step that resolves it for
> flowise/langflow (installer / Dokploy env `DOMAIN`). If you create the DB by hand
> instead of via `langfuse-db-init`:
> `docker compose exec postgres psql -U "$POSTGRES_USER" -c "CREATE DATABASE langfuse"`.

---

## 6. Verify

- Open **https://trace.{{DOMAIN}}** → sign in (headless-init user = your
  `N8N_ADMIN_EMAIL` / `N8N_ADMIN_PASSWORD`, or the account you create).
- Run the **CP MCP Gateway Agent** in Langflow → refresh Langfuse → the project's
  **Traces** list shows a new trace with the prompt, the tool call(s), tokens, and
  latency.
- No trace after a run? Check, in order: keys match the project, `LANGFUSE_HOST`
  is `http://langfuse:3000` (internal name, not `trace.<domain>`), and the
  containers share the `demo` network (`docker compose exec langflow \
  getent hosts langfuse`).

---

## 7. Want v3 instead? (heavier — read first)

v3 splits the async ingestion pipeline out of the web container and **requires**
extra infra: **ClickHouse** (analytics store), **Redis/Valkey** (queue), and
**S3/MinIO** (event + media blobs), on top of Postgres. That's four stateful
dependencies vs. v2's one. Only adopt v3 if you need its newer features
(high-volume async ingestion, the newer evals/dashboards). For a teaching lab, **v2
is the pragmatic choice** and what this integration targets.

---

**Files in this feature dir**

- `INTEGRATION.md` — this paste sheet.
- `gen_secrets.py` — stdlib generator for `NEXTAUTH_SECRET` / `SALT` /
  `LANGFUSE_ENCRYPTION_KEY` (and optional `pk-lf`/`sk-lf` key pair).
- `langfuse_smoke_trace.py` — stdlib (urllib) script that posts one test trace to
  the Langfuse ingestion API; verification tool **and** the reference for the n8n
  Code-node path (section 4c).

Learner-facing walkthrough: `docs/guides/Observability_Langfuse.md`.
