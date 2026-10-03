# Observability: integration notes

Langfuse shows what an agent did: the prompt, the model's answer, the tool calls it asked for, the
token counts and the latency. Langfuse is part of the Standard lab and needs no extra step. The
walkthrough for trainees is [docs/guides/Observability_Langfuse.md](../../docs/guides/Observability_Langfuse.md).

| File | What it is |
|------|------------|
| `gen_secrets.py` | Fills the Langfuse and LiteLLM secrets that are still blank in a hand-made `.env`. |
| `langfuse_smoke_trace.py` | Sends one test trace to Langfuse and reads it back. |
| `litellm_config.yaml` | Retired. A pointer to `integrations/litellm/render_config.py`, which renders the LiteLLM config at start. |

## How it is wired

| Service | Role |
|---------|------|
| `langfuse` | Langfuse 2.95.11, the last v2 release: one container, data in Postgres. Networks `lab` and `dokploy-network`. Sign-up is off and telemetry is off by default. |
| `langfuse-db-init` | One-shot: creates the database `langfuse` in the lab Postgres. |
| `litellm` | Sends every `lab-chat` call to Langfuse when `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` are set (`LANGFUSE_HOST`, default `http://langfuse:3000`). |
| `builders-import` | Creates the Flowise credential **Lab Tracing (Langfuse)** and turns on Langfuse analytics for the seeded Flowise flows. |

On its first start Langfuse creates, with no manual step:

- the organization `LANGFUSE_ORG_ID` (default `check-point-agentic-lab`, named "Check Point Agentic Lab")
- the project **Agents** with the key pair `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`
- the lab admin (`N8N_ADMIN_EMAIL`, `N8N_ADMIN_PASSWORD`) as its user

Langfuse never renames an organization. A lab set up before the current naming keeps its
organization: `./setup.sh` writes `LANGFUSE_ORG_ID=cp-playground` (its former ID) into that lab's
`.env`, so tracing keeps working.

## What is traced

| Source | What you see in Langfuse |
|--------|--------------------------|
| Every agent in n8n, Flowise and Langflow, the code-first agent and the evals | Each `lab-chat` call, traced by LiteLLM: the messages, the answer or the tool calls the model asked for, tokens, latency. A failed provider call shows as one ERROR generation. |
| Seeded Flowise flows | Also Flowise's own trace of the run (agent steps and tool calls), through the credential Lab Tracing (Langfuse) |
| Requests with a wrong LiteLLM key | Nothing. LiteLLM refuses them with HTTP 401 and logs them in its container log only |

Langflow sends no traces of its own: Langflow 1.10 bundles the Langfuse v3 SDK, which does not work
with the lab's Langfuse v2. Its model calls are traced through LiteLLM. n8n has no Langfuse switch;
its model calls are traced through LiteLLM too.

A flow you build yourself in Flowise is traced through LiteLLM. For Flowise's own trace as well, turn
on Langfuse in the flow's analytics settings and pick the credential **Lab Tracing (Langfuse)**.

## Settings

`./setup.sh` generates every one of them. Do not change `SALT` or `LANGFUSE_ENCRYPTION_KEY` after the
first start: Langfuse keeps the values it started with.

| Variable | Rule |
|----------|------|
| `NEXTAUTH_SECRET`, `SALT` | Required. The lab refuses to start without them. |
| `LANGFUSE_ENCRYPTION_KEY` | Required, 64 hex characters. |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | `pk-lf-...` and `sk-lf-...`. Blank turns tracing off. |
| `LANGFUSE_URL` | The address you open Langfuse at; sign-in redirects go there. Blank = `https://trace.<DOMAIN>`. |
| `LANGFUSE_DATABASE_URL` | Blank = the lab Postgres. Set a `postgresql://` URL only for another server. |
| `LANGFUSE_ORG_ID` | Organization ID on first start. |
| `LANGFUSE_TELEMETRY_ENABLED` | `false` by default. |

For a `.env` made by hand (`cp .env-example .env`), fill the blanks and start the services:

```sh
python3 integrations/observability/gen_secrets.py --dry-run   # list what would be set
python3 integrations/observability/gen_secrets.py
docker compose up -d langfuse litellm
```

It never changes a value that is set, leaves 1Password references alone, keeps `.env` at mode 600 and
prints names only.

## Open Langfuse

- **Lab host:** `https://trace.<DOMAIN>`. The routes come from the Traefik labels of the `langfuse`
  service (a `web` and a `websecure` router, TLS from the `letsencrypt` resolver). Only
  `https://hub.<DOMAIN>` may embed it in a frame.
- **Your own computer:** `./setup.sh` sets `LANGFUSE_URL=http://localhost:3100` when `DOMAIN` is
  blank. Publish that port on the loopback address only, in a local `docker-compose.override.yml`
  (git-ignored):

  ```yaml
  services:
    langfuse:
      ports:
        - "127.0.0.1:3100:3000"
  ```

  Then `docker compose up -d langfuse` and open `http://localhost:3100`.

Sign in with the lab admin. Open the project **Agents**, then **Traces**.

## Verify

Send one test trace (the `litellm` container already has Python and the keys):

```sh
docker compose exec -T litellm python - < integrations/observability/langfuse_smoke_trace.py
```

Exit status 0 means the trace landed and could be read back, 1 that Langfuse refused it or never
showed it, 2 that the keys are missing. The test trace stays in Langfuse.

The acceptance check covers the whole path. With model calls on, it finds the traces of the run's own
model calls and of the builder runs:

```sh
tests/acceptance/run.sh --only LITELLM,LANGFUSE
```

The end-to-end runs of the lab passed it with the local Ollama model (13 traces, 5 builder runs) and with
Azure OpenAI (18 traces, 8 builder runs).

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| No traces at all | Check the LiteLLM start line: `docker compose logs litellm \| grep lab-litellm`. It must end with `Langfuse tracing on (http://langfuse:3000).` If it says off, set both Langfuse keys and run `docker compose up -d litellm`. |
| The start line warns about unresolved 1Password references | Start the lab through 1Password: `op run --env-file=.env -- docker compose up -d` |
| The smoke trace exits 1 | The keys do not match the project. Keep the keys Langfuse started with, or set them back in `.env`. |
| Sign-in redirects to the wrong address | Set `LANGFUSE_URL` to the address you open, then `docker compose up -d langfuse`. |
| Langfuse does not start | `docker compose logs langfuse`. `NEXTAUTH_SECRET`, `SALT` and `LANGFUSE_ENCRYPTION_KEY` must be set (`./setup.sh`). |
| A flow you built in Flowise has no Flowise trace | Turn on Langfuse in its analytics settings with the credential Lab Tracing (Langfuse). Its model calls are traced through LiteLLM either way. |
