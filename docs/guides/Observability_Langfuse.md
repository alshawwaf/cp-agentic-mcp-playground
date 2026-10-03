# Tracing the Agents with Langfuse

Agents look simple until one misbehaves. Then you need a record of what it did. A trace is that record: the prompt the model received, the tool calls it asked for, the tokens it spent, how long each step took, and any error. The lab sends traces to Langfuse, which runs on the lab itself, so the traces (with their prompts and tool results) stay on your computer or lab host.

## How traces reach Langfuse

Every seeded agent calls one model, `lab-chat`, through LiteLLM (`http://litellm:4000/v1`). LiteLLM sends each call to Langfuse when `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` are set. So one path traces every builder and every provider:

| Source | Traced through LiteLLM | Extra native tracing |
|---|---|---|
| n8n agents | Yes, every model call | None |
| Flowise agents | Yes, every model call | Seeded flows also send their own agent trace tree (credential "Lab Tracing (Langfuse)") |
| Langflow agents (Complete lab) | Yes, every model call | None. Langflow 1.10 bundles the Langfuse v3 SDK, which does not work with the lab's Langfuse v2 |
| Code-first agent (`integrations/code-agent`) | Yes | None |
| Open WebUI (Complete lab) | No: it calls Ollama directly | None |

What you see:

- **LiteLLM traces.** One `litellm-acompletion` record per model call: the messages sent (system prompt, user message, earlier tool results), the reply (text or the tool call the model asked for), input and output tokens, latency, and cost when LiteLLM knows the model's price. A failed provider call shows as one error-level generation. A request with a wrong LiteLLM key is refused (HTTP 401) and never reaches Langfuse
- **Flowise native traces.** A tree per run: the agent, each model generation and each tool call with its arguments and result. `builders-import` turns this on for the flows it seeds, when the Langfuse keys are set and the flow has no analytics setting of its own. Flows you create yourself need it turned on (see Troubleshooting)

## Prerequisites

- The Standard lab. `./scripts/doctor.sh --post-start` shows `ready` on the `Langfuse tracing` line (`LiteLLM sends every lab-chat call`)
- `NEXTAUTH_SECRET`, `SALT`, `LANGFUSE_ENCRYPTION_KEY`, `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` in `.env`. `./setup.sh` generates all of them. If you made `.env` by hand, `python3 integrations/observability/gen_secrets.py` fills the blank ones and prints names only
- A model for `lab-chat`, so there is something to trace

## Step 1: Prove that Langfuse accepts traces

Send one test trace from inside the lab network. The `litellm` container already has Python and the Langfuse keys:

```sh
docker compose exec -T litellm python - < integrations/observability/langfuse_smoke_trace.py
```

**Expected result:** `OK: trace 'lab-smoke-test' is visible in Langfuse.` With 1Password nothing changes: the running container already holds the resolved keys.

Then confirm that LiteLLM has tracing on:

```sh
docker compose logs litellm | grep lab-litellm
```

**Expected result:** a line that ends with `Langfuse tracing on (http://langfuse:3000).`

## Step 2: Open Langfuse

| Where | Address |
|---|---|
| Lab host | `https://trace.<DOMAIN>` |
| Your own computer | `http://localhost:3100`, with the local port override from the README. `./setup.sh` sets `LANGFUSE_URL=http://localhost:3100` when `DOMAIN` is blank |

Sign in with the lab admin (`N8N_ADMIN_EMAIL`, `N8N_ADMIN_PASSWORD`). Sign-up is off. Open the organization **Check Point Agentic Lab** (a lab set up before this name keeps its earlier organization), the project **Agents**, then **Traces**. Filter by the tag `smoke-test` to find the test trace.

## Step 3: Read a trace

1. In n8n, open **Management Agent (MCP Gateway)** and ask a question that needs a tool, for example *Which gateways do I have?* (needs Management access). Without Check Point keys, use **Documentation RAG Agent** and ask *How do I enable Identity Awareness?*
2. Refresh **Traces** in Langfuse. One question produces several `litellm-acompletion` records: the model first asks for a tool, the agent runs it, and the model is called again with the tool result
3. Open the first record. Read the system prompt and the user message. Check the output: which tool did the model choose, and with which arguments?
4. Open the next record. The tool result is now part of the input. The output is the answer
5. Compare tokens and latency across the records. A long latency on a generation is the model; a long gap between records is the tool

Flowise gives the richest view. Ask the same question in Flowise and open its trace: the tree shows the tool calls as their own steps, with arguments and results.

### Exercise: what a tool list costs

Every tool an agent can use is described to the model on every call, and those descriptions are paid for as input tokens.

1. Ask **Reputation Service Agent (Direct)** (3 tools): *What is the reputation of 8.8.8.8?*
2. Ask **Fleet Commander Estate Agent** (117 tools) the same question
3. Compare the input tokens of the first generation of each run

The Fleet Commander run starts with far more input tokens before it does any work. That is the cost of a big tool list, and why the per-server agents are scoped to their own tools. The MCP Gateway twins use the same tool sets as the Direct agents, so a Direct agent and its MCP Gateway twin cost about the same per call. (Reputation Service needs `REPUTATION_API_KEY` to answer; the token counts show either way.)

## Self-hosted by design

- Langfuse 2.95.11 runs as one container on the lab's Postgres. Langfuse v3 needs ClickHouse, Redis and object storage, which is too heavy for the Standard lab
- Traces hold your prompts and tool results, for example policy data or CPInfo content. They stay on the lab, but anyone with the lab admin sign-in can read them. Treat Langfuse like the data that flows through it
- Langfuse product telemetry is off (`LANGFUSE_TELEMETRY_ENABLED=false`)
- On a shared lab host, everyone's traces land in the same project. Filter by time and by your prompt text

The acceptance check `LANGFUSE` proves that the traces of the run's own model calls arrive. It needs model calls in the same run, so run it with the `LITELLM` check: `tests/acceptance/run.sh --only LITELLM,LANGFUSE` (with model calls on). On its own, `LANGFUSE` finds no model calls and reports `SKIP`.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| The `lab-litellm` log line says `Langfuse tracing off (set LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY to turn it on)` | Keys missing | Run `./setup.sh` (or `gen_secrets.py`), then `docker compose up -d langfuse litellm` |
| The log line warns about unresolved 1Password references | The lab was started without `op run` | `op run --env-file=.env -- docker compose up -d` |
| The smoke trace says the keys were refused | The keys changed after Langfuse's first start. Langfuse creates its project keys only once | Put back the keys Langfuse first started with. Never change `SALT`, `LANGFUSE_ENCRYPTION_KEY` or the project keys after the first start |
| The smoke trace cannot reach Langfuse | Langfuse is down | `docker compose ps langfuse`, then `docker compose logs langfuse` |
| Sign-in loops back to the form on your own computer | `LANGFUSE_URL` is not the address you open | Set `LANGFUSE_URL=http://localhost:3100`, then `docker compose up -d langfuse` |
| A Flowise flow you created is not traced as a tree | Analytics are off for that flow | In the flow's settings, turn on Langfuse analytics with the credential "Lab Tracing (Langfuse)". Its model calls are traced through LiteLLM either way |
| An n8n agent fails with a LiteLLM key error | The "Lab Model (LiteLLM)" credential is out of sync with `.env` | `docker compose run --rm n8n-import` |
| No traces from Open WebUI | Expected: it calls Ollama directly, not LiteLLM | None |

Related: [MCP Gateway, explained](MCP_Gateway_Explained.md) for what the agents call, and the [Evals Harness](Evals_Harness.md) for checking answers at scale.
