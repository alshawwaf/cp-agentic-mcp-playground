# Evals Harness: How Do You Know Your Agent Is Good?

An agent that worked once is not an agent you can trust. The model changes, someone edits a prompt, a tool returns something new, and quality slips without anyone noticing. Evals catch that. An eval is a fixed prompt, the facts a good answer must contain, and a result. Run the same set after every change and you see whether the agent got better or worse.

This harness is deliberately small: ten cases, one command, a scorecard you can compare between runs. It is not a benchmark of the model. It checks whether **this lab, right now** still does what you promised.

## What it does

`integrations/evals/run_evals.py` sends one real chat turn to each n8n agent and checks the answer for expected text:

```
POST {BASE_URL}/webhook/<webhookId>/chat
body: {"action":"sendMessage","chatInput":"<prompt>","sessionId":"<unique per case and run>"}
```

- The chat endpoints require the lab admin sign-in (HTTP Basic with `N8N_ADMIN_EMAIL` and `N8N_ADMIN_PASSWORD`). The harness sends it and never prints the password
- Every case gets its own session ID, so chat memory never carries one case into another
- Standard library only: nothing to install

## Two kinds of failure

| Result | Meaning | What to look at |
|---|---|---|
| `PASS` | The answer contains what the case expects | Nothing |
| `FAIL` (answer quality) | The agent answered, but not as the case expects | The model, the system prompt or the tools |
| `ERROR` (wiring or setup) | The agent could not answer at all: sign-in refused, workflow not published, a key or service missing, or the workflow's error branch replied | The deployment. These say nothing about answer quality |

Fix every `ERROR` before you read the `FAIL` lines. For an `ERROR`, the summary prints what the case needs (`<case> needs: ...`).

## The shipped cases

All cases live in `integrations/evals/evals_cases.json`. Each one needs the lab model (`lab-chat`) plus what the table lists.

| Case | Agent | Checks | Also needs |
|---|---|---|---|
| `rag_identity_awareness_cited` | Documentation RAG Agent | Cites `identity-awareness.md` | The `cp_docs` collection (filled by `rag-ingest`). No Check Point key: the baseline case |
| `reputation_8888_clean` | Reputation Service Agent (Direct) | `8.8.8.8` is clean or benign | `REPUTATION_API_KEY` |
| `management_access_layers` | Management Agent (Direct) | Three layers, including `Network` | `MANAGEMENT_HOST` and `MANAGEMENT_API_KEY` (or `S1C_URL`). Retune for your Management Server |
| `threat_prevention_profiles_optimized` | Threat Prevention Agent (Direct) | Lists the `Optimized` profile | `MANAGEMENT_HOST` and `MANAGEMENT_API_KEY` (or `S1C_URL`) |
| `documentation_identity_awareness` | Documentation Agent (Direct) | Finds Identity Awareness material | `DOC_CLIENT_ID` and `DOC_SECRET_KEY` |
| `documentation_identity_awareness_gateway` | Documentation Agent (MCP Gateway) | The same, through the MCP Gateway | `DOC_CLIENT_ID`, `DOC_SECRET_KEY` and a running gateway |
| `devhub_apps_count` | DevHub Operations Agent | Reports the app inventory | `DOMAIN`, `DEVHUB_MCP_TOKEN` and a reachable DevHub |
| `policypilot_network_layer_summary` | PolicyPilot Access Automation Agent (Pro) | Summarizes the `Network` layer | `DOMAIN`, `PILOT_MCP_TOKEN` and a reachable PolicyPilot portal |
| `guarded_chat_injection_blocked` | Guarded Agent (Lakera Guard) | A prompt injection is `Blocked` | `LAKERA_API_KEY` |
| `guarded_chat_safe_passes` | Guarded Agent (Lakera Guard) | A safe question is answered, not blocked | `LAKERA_API_KEY` |

The documentation pair shows a gateway problem on its own: when the Direct case passes and the MCP Gateway case fails, look at the gateway.

The guarded pair proves both sides of a safety control. It must block an attack **and** let a normal question through. A guard that blocks everything passes the first case and fails the second. Without `LAKERA_API_KEY`, both cases are `ERROR`, not `FAIL`: the guard is off, and the harness says so.

## Prerequisites

- The lab is running and `./scripts/doctor.sh --post-start` ends with `Result: no blockers`
- A model for `lab-chat`
- The keys of the cases you want to pass (table above). A case without its keys cannot pass

## Step 1: Run the evals

The harness runs as the one-shot service `evals-run` in the profile `evals`. It waits until `n8n-import` has finished and LiteLLM is healthy.

```sh
docker compose --profile evals run --rm evals-run
```

With 1Password:

```sh
op run --env-file=.env -- docker compose --profile evals run --rm evals-run
```

If `evals` is already in `COMPOSE_PROFILES`, `docker compose run --rm evals-run` is enough.

**Expected result:** one line per case (`PASS`, `FAIL` or `ERROR`), then a summary:

```
  answer-quality failures (FAIL):  ...  the agent answered, but not as the case expects
  wiring/setup errors     (ERROR): ...  the agent could not answer; fix the deployment first
```

`rag_identity_awareness_cited` needs no Check Point key, so it is the first case to get green. A case whose keys are missing cannot pass: read its reason and its `needs:` line before you change the agent.

The reports land in `./n8n/shared/` on the host:

- `evals_report.md`: the score, a table per case, and for each failure the prompt, what was expected, why it failed and a sample of the answer
- `evals_report.json`: the same data for scripts and trend tracking

Exit code: `0` when every case passed, `1` when any case failed or errored, `2` on a usage error. Use it as a gate in CI.

### Run against another lab

Set `EVALS_BASE_URL` in `.env` to the remote n8n, for example `https://n8n.<DOMAIN>`. The harness refuses plain `http` for anything but a lab service name or `localhost`, because the admin password would cross the network in clear text.

### Settings

`evals-run` sets these for you. When you run `run_evals.py` yourself, they are environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `BASE_URL` | `http://n8n:5678` | n8n root, without `/webhook` |
| `N8N_ADMIN_EMAIL`, `N8N_ADMIN_PASSWORD` | none (required) | Chat sign-in |
| `CASES_FILE` | `evals_cases.json` next to the script | Your cases |
| `OUT_DIR` | the current directory (`./n8n/shared` in `evals-run`) | Where the reports go |
| `TIMEOUT` | `90` seconds (`120` in `evals-run`) | Time per chat turn |
| `ONLY` | all cases | Comma-separated case names (substring match), for example `ONLY=rag,guarded` |
| `SESSION_ID` | `evals` | Prefix of the per-case session IDs |

To run only some cases in the container:

```sh
docker compose --profile evals run --rm -e ONLY=rag,guarded evals-run
```

## Step 2: Edit or add cases

Edit `integrations/evals/evals_cases.json`, never the code. A case looks like this:

```json
{
  "name": "reputation_8888_clean",
  "agent": "Reputation Service Agent (Direct)",
  "webhookId": "d9e1213c-ea8c-4482-aa48-36dffdb2e837",
  "prompt": "What's the reputation of 8.8.8.8?",
  "expect_any": ["clean", "benign", "safe", "no known", "low risk"],
  "requires": ["REPUTATION_API_KEY"]
}
```

| Field | Rule |
|---|---|
| `name` | Unique slug shown in the report |
| `agent` | Display name of the n8n workflow |
| `webhookId` | The chat trigger's webhook ID |
| `prompt` | The message sent to the agent |
| `expect` | Every string must appear (case-insensitive) |
| `expect_any` | At least one string must appear. Use it for wording that varies between answers |
| `must_not` | No string may appear. The safe guarded case uses it to assert `Blocked` is absent |
| `requires` | What the case needs besides `lab-chat`. Printed when the case cannot run |
| `note` | Free text, ignored by the scorer |

Substring checks are simple on purpose: transparent, repeatable, and free of a second model. Most regressions show up as "the answer no longer contains the fact it should".

### Find a webhook ID

The shipped IDs are the chat triggers committed in `n8n/backup/workflows/*.json`. For a new or re-created workflow, open it in n8n, select **When chat message received**, and copy the ID from the webhook URL (`.../webhook/<THIS-PART>/chat`). Or read it from the file:

```sh
jq -r '.nodes[]|select(.type|endswith("chatTrigger")).webhookId' n8n/backup/workflows/<file>.json
```

After you edit the cases, run the offline tests. They check every `webhookId` and agent name against the committed workflows:

```sh
python3 integrations/evals/test_evals.py
```

## Evals and the Nightly Agent Self-Check

n8n also ships **Nightly Agent Self-Check** (`n8n/backup/workflows/nightly-self-qa.json`). It is off by default. `n8n-import` publishes it only when `NIGHTLY_SELF_QA=1` is set in `.env`.

| | Nightly Agent Self-Check | Evals harness |
|---|---|---|
| Runs | Every night at 02:00 UTC, inside n8n | On demand, or in CI |
| Checks | Each published agent answered without an error | The answer contains the right facts |
| Output | A Markdown digest (add a Slack, Teams or email node) and a red execution on failure | `evals_report.md`, `evals_report.json` and an exit code |
| Edit | The workflow's probe list | `evals_cases.json` |

Use both. The nightly check tells you something is down. The evals tell you something got worse. Each nightly run makes one model call per agent, or more for agents that use tools, so leave it off on your own computer.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Every case is `ERROR` with HTTP 401 | Wrong or missing admin sign-in | `N8N_ADMIN_EMAIL` and `N8N_ADMIN_PASSWORD` in `.env` must match the n8n owner |
| A case is `ERROR` with HTTP 404 | The workflow is not published (missing prerequisites), or the `webhookId` is wrong | Read the `n8n-import` log (`not published: ... (needs ...)`), set what it needs, run `docker compose run --rm n8n-import` |
| `ERROR` with "the workflow's error branch answered" | The model, a tool server or a credential failed | Open the execution in n8n (Executions) |
| Guarded cases `ERROR` with "Lakera Guard is not configured" | No `LAKERA_API_KEY` | See the [Lakera Guard Screening Agent](Lakera_Guard_Screening_Agent_Guide.md) guide |
| `management_access_layers` is `FAIL` | Your Management Server has a different number of layers | Retune `expect_any` for your server |
| Exit code `2` | Missing cases file, or a plain-http remote `BASE_URL` | Fix the path, or use `https://` |
