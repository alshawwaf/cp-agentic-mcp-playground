# Seeing Inside the Agents — Tracing with Langfuse

*Agents feel like magic until they misbehave. Then you want a flight recorder.
That's tracing, and in this playground it's [Langfuse](https://langfuse.com) —
self-hosted, so nothing leaves the box.*

---

## 1. What tracing is

When an agent answers a question it does a lot you never see: it builds a prompt,
sends it to a model, gets back a decision, maybe **calls a tool** (here, a Check
Point MCP tool through the gateway), feeds the result back to the model, and loops
until it has an answer. A **trace** is the recording of that whole run — nested,
timed, and labeled:

- the **prompts** actually sent (system + user + tool results),
- every **tool call** with its arguments and what came back,
- **tokens** in and out (and, when the model reports pricing, **cost**),
- **latency** per step and for the run as a whole,
- and any **error** that stopped it.

A trace is a tree. The top node is the run; children are the model generations and
tool calls, in order, each with its own timing. Langfuse is the UI that collects
those trees and lets you click into them.

---

## 2. Why it matters for teaching

The gateway guide (`MCP_Gateway_Explained.md`) makes the point that the gateway
exposes **~180 tools at once**, and that a big flat catalog can confuse the model
into picking the wrong tool. Tracing is how you *prove* that in a lab instead of
hand-waving it. With a trace open, a learner can see, concretely:

- **Did the agent call the tool I expected?** Wrong tool, right answer-by-luck is a
  teachable moment you can only spot in the trace.
- **How many tokens did that cost?** The gateway's flat tool list shows up as a fat
  input-token count — visible, not theoretical.
- **Where did the time go?** A slow run is usually one slow tool or a model retry;
  the waterfall tells you which.
- **Why did it fail?** A 401 from the gateway (missing Bearer), an empty tool list
  (skipped MCP handshake), a malformed argument — the trace shows the exact call.

It turns "the agent did something" into "here is exactly what it did, step by step."
That is the difference between a demo and understanding.

---

## 3. Opening the trace UI

Langfuse runs as a service in the stack and is published at:

```
https://trace.<your-domain>
```

Sign in with the stack admin account (the same `N8N_ADMIN_EMAIL` /
`N8N_ADMIN_PASSWORD` used across the lab, if headless init was enabled) or the
account you created on first boot. Pick the project (default **Agents**), then open
**Traces** in the left nav.

> Setup / wiring lives in `integrations/observability/INTEGRATION.md` — the compose
> service, the `.env` keys, and how each builder is pointed at Langfuse. This guide
> is about *using* it.

Which builders show up automatically — all three, via two different paths:

| Builder | Traced out of the box? | How | What you see |
|---|---|---|---|
| **Flowise** | **Yes — every chatflow** | `builders-import` creates a `CP Langfuse (auto)` credential and switches **Analyse Chatflow → Langfuse ON** for every flow it seeds (and re-asserts it on existing ones). | The **full agent tree**: prompts, MCP tool calls with arguments/results, tokens, latency. The richest traces in the stack — use Flowise runs for the section-4 walkthrough. |
| **n8n** | **Yes — every agent** | The imported **OpenAI** and **Azure OpenAI** credentials point at the internal **LiteLLM proxy** (`http://litellm:4000`), which forwards to the real provider and logs each call to Langfuse. | One `litellm-acompletion` trace **per model call** (prompt, completion, tokens, cost, latency). Tool calls appear as tool-use messages inside the prompts, not as separate spans. |
| **Langflow** | **Yes — model calls** | Every committed flow's OpenAI model routes through the same **LiteLLM proxy**. Native Langfuse is off: Langflow 1.10 bundles langfuse **SDK v3**, which cannot talk to the lean Langfuse **v2** server this stack runs (v3 needs ClickHouse/Redis/MinIO). | Same `litellm-acompletion` shape as n8n. |

Two wiring gotchas worth knowing (both already handled by the committed flows):
- Flowise's Langfuse credential fields are capital-F — `langFusePublicKey` / `langFuseSecretKey` / `langFuseEndpoint`.
- Langflow **ignores template fields with `show: false` at build time** — the OpenAI
  model's `openai_api_base` only takes effect because the committed flows ship it
  with `show: true`. If you clone a flow by hand and the base URL seems ignored,
  check that flag first.

(Details for all three are in INTEGRATION.md sections 4a–4c.)

---

## 4. Reading a trace — what to look at

Run the **CP MCP Gateway Agent** in Langflow (ask it something that needs a tool,
e.g. *"show the last 10 management logs"*), then refresh Langfuse and open the newest
trace. Walk it top-down:

1. **The tree / waterfall.** The root is the run. Read the children in order — you
   should see a model generation, then an MCP **tool call**, then another generation
   that uses the tool's result. This *is* the agent loop, made visible.
2. **The first generation's input.** Expand it and read the **system prompt** and the
   **tool list** the model was handed. On a gateway agent that list is large — this
   is where the "~180 tools" cost becomes real and where trimming pays off.
3. **The tool call.** Check the **name** (was it the tool you intended?), the
   **arguments** the model filled in, and the **output** that came back. Most "wrong
   answer" bugs are visible right here.
4. **Tokens and cost.** Each generation shows input/output tokens; the run totals
   them. Compare a **direct** agent vs a **via-gateway** agent on the same question —
   the gateway one usually has a bigger input count purely from the tool catalog.
5. **Latency.** The waterfall widths show where time went. A long bar on a tool call
   means a slow backend; a long bar on a generation means the model, not you.
6. **Errors.** A failed step is flagged red with its message — a 401 (bad/absent
   Bearer token), an empty tool list (the MCP handshake was skipped), or a bad
   argument. The trace points straight at the cause.

A good first exercise: run the **same prompt** through the direct agent and the
gateway agent, put the two traces side by side, and explain the token/latency
difference from what you see in the tool lists. That single comparison teaches more
about the gateway trade-off than any paragraph.

---

## 5. Self-hosted, and why v2

Langfuse here is **self-hosted** — traces (which include your prompts and tool I/O)
stay on the lab host and never go to a SaaS. Anonymous product telemetry is turned
**off** in this integration.

The stack runs Langfuse **v2**, which is a single container backed by the Postgres
you already have — light and easy. v3 exists and is newer, but it needs ClickHouse,
Redis, and S3/MinIO alongside Postgres; only worth it for high-volume or v3-only
features. For learning, v2 is the right call. (Rationale and the v3 checklist are in
INTEGRATION.md section 7.)

---

## 6. Quick troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| No traces after an n8n / Langflow run | LiteLLM proxy down or keys unset | `docker logs litellm`; confirm `LANGFUSE_PUBLIC_KEY`/`SECRET_KEY` are in `.env` and `LANGFUSE_HOST=http://langfuse:3000` (the **internal** name, not `trace.<domain>`). |
| n8n agent errors `401` on every model call | credential/master-key mismatch | The OpenAI + Azure n8n credentials must carry `LITELLM_MASTER_KEY` (compose default `sk-cp-litellm-training-key`) — re-run `n8n-import` after changing it. |
| Langflow model ignores the LiteLLM base URL | `openai_api_base` hidden | Langflow 1.10 drops `show: false` template fields at build; set `show: true` on the field (the committed flows already do). |
| Langflow log: `Cannot connect to Langfuse … ParsingModel[Projects]` | langfuse SDK v3 vs server v2 | Expected — native Langflow tracing needs a Langfuse v3 server. Harmless; the LiteLLM path covers Langflow. |
| Flowise flow not traced | Analytics off for that chatflow | Re-run `builders-import` (it switches Langfuse ON for every chatflow), or toggle **Analyse Chatflow → Langfuse** by hand. |
| Can't reach `trace.<domain>` | routing / bind | Langfuse must have `HOSTNAME=0.0.0.0`; check Traefik picked up the `trace.` router. |
| Want to prove ingestion works | — | Run `integrations/observability/langfuse_smoke_trace.py` (stdlib, sends one test trace). |

---

*Related: `docs/guides/MCP_Gateway_Explained.md` (what the gateway and its ~180-tool
catalog actually do) and `integrations/observability/INTEGRATION.md` (the exact
compose/env wiring this guide assumes is in place).*
