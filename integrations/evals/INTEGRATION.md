# Evals harness: integration notes

The evals harness sends a fixed set of prompts to the n8n agents and scores the answers. It tells you
whether a change to a model, a prompt, a tool or the gateway made the agents better or worse. The
background and the method are in [docs/guides/Evals_Harness.md](../../docs/guides/Evals_Harness.md).

| File | What it is |
|------|------------|
| `run_evals.py` | The harness. Standard library only. |
| `evals_cases.json` | The 10 cases. Edit cases here, not in the code. |
| `test_evals.py` | Offline tests with a mock n8n. |

## How it is wired

The `evals-run` service in `docker-compose.yml` runs the harness once and exits. It is in the
`evals` profile, so it never starts with the default lab.

| Setting | Value |
|---------|-------|
| Image | the pinned `python:3.12.14-alpine` |
| Network | `lab` |
| Starts after | `n8n-import` completed, `litellm` healthy |
| Target | `BASE_URL`, from `EVALS_BASE_URL` (default `http://n8n:5678`) |
| Sign-in | HTTP Basic with `N8N_ADMIN_EMAIL` and `N8N_ADMIN_PASSWORD`: the n8n chat endpoints refuse calls without it |
| Reports | `./n8n/shared/evals_report.md` and `./n8n/shared/evals_report.json` on the host |
| Limits | 0.5 CPU, 256 MB, no new privileges, no published port |

Each case is one chat turn to `{BASE_URL}/webhook/<webhookId>/chat` with its own session ID, unique
per run, so no case sees the chat memory of another case or of an earlier run.

## Prerequisites

- The lab is running and `n8n-import` has published the agents.
- `lab-chat` has a provider (a cloud key, or the local Ollama model).
- Each case needs the product keys in its `requires` field. The cases cover the Documentation RAG
  Agent, the Reputation Service, Management, Threat Prevention and Documentation agents, the
  DevHub and PolicyPilot agents, and the Guarded Agent (Lakera Guard).

| Case | Agent | Needs |
|------|-------|-------|
| `rag_identity_awareness_cited` | Documentation RAG Agent | `rag-ingest` filled the `cp_docs` collection |
| `reputation_8888_clean` | Reputation Service Agent (Direct) | `REPUTATION_API_KEY` |
| `management_access_layers` | Management Agent (Direct) | A reachable Management server |
| `threat_prevention_profiles_optimized` | Threat Prevention Agent (Direct) | A reachable Management server |
| `documentation_identity_awareness` | Documentation Agent (Direct) | `DOC_CLIENT_ID`, `DOC_SECRET_KEY` |
| `documentation_identity_awareness_gateway` | Documentation Agent (MCP Gateway) | `DOC_CLIENT_ID`, `DOC_SECRET_KEY` |
| `devhub_apps_count` | DevHub Operations Agent | `DOMAIN`, `DEVHUB_MCP_TOKEN` |
| `policypilot_network_layer_summary` | PolicyPilot Access Automation Agent (Pro) | `DOMAIN`, `PILOT_MCP_TOKEN` |
| `guarded_chat_injection_blocked` | Guarded Agent (Lakera Guard) | `LAKERA_API_KEY` |
| `guarded_chat_safe_passes` | Guarded Agent (Lakera Guard) | `LAKERA_API_KEY` |

## Run it

```sh
docker compose --profile evals run --rm evals-run
cat n8n/shared/evals_report.md
```

With 1Password references in `.env`:

```sh
op run --env-file=.env -- docker compose --profile evals run --rm evals-run
```

Run some cases only (substring match on the case name):

```sh
docker compose --profile evals run --rm -e ONLY=documentation evals-run
```

To evaluate a remote lab, set `EVALS_BASE_URL=https://n8n.<DOMAIN>` in `.env`. The harness refuses
plain `http://` for anything but a lab service name or `localhost`, because the admin password would
cross the network in clear text.

## Reading the result

| Result | Meaning | Where to look |
|--------|---------|---------------|
| PASS | The answer has every expected phrase and none of the forbidden ones | |
| FAIL | The agent answered, but not as the case expects (answer quality) | The model, the system prompt or the tools |
| ERROR | The agent could not answer (wiring or setup): sign-in refused, workflow not published, a key or service missing, the error branch replied | Fix the deployment first. The report lists what the case `requires` |

Exit status: 0 every case passed, 1 a case failed or had an error, 2 usage error (no cases file,
unsafe `BASE_URL`). The console output and the reports never contain the admin password.

The acceptance check `EVALS` reads the scorecard `n8n/shared/evals_report.json`. It fails on wiring
errors and reports answer-quality misses without failing. It is `SKIP` while `evals` is not in
`COMPOSE_PROFILES`. After a run with `--profile evals`, check the scorecard with:

```sh
tests/acceptance/run.sh --only EVALS --no-profile-aware
```

## Add or change a case

Edit `evals_cases.json`. Each case has `name`, `agent` (the n8n workflow name), `webhookId` (the
chat trigger's `webhookId` in `n8n/backup/workflows/*.json`), `prompt`, `expect` (all must appear),
optional `expect_any` (one must appear) and `must_not`, `requires` and `note`. Then run the offline
tests, which also check that every `webhookId` and agent name match a committed chat trigger:

```sh
.github/scripts/py-isolated.sh -- python3 integrations/evals/test_evals.py
```

## The nightly check in n8n

The n8n workflow **Nightly Agent Self-Check** probes every chat agent at 02:00 with a fresh session
and fails its execution when an agent does not answer. It skips the MCP Security Lab agent. It is off
by default: set `NIGHTLY_SELF_QA=1` in `.env` and run `docker compose run --rm n8n-import` to
publish it. n8n reads the hour in `GENERIC_TIMEZONE` (default UTC). The evals harness checks answer
quality on demand; the nightly check watches that the agents keep answering.
