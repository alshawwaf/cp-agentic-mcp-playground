# Evals Harness — "How Do You Know Your Agent Is Good?"

*A demo that worked once is not an agent you can trust. This is the small, honest
tool that turns "it felt right" into a number you can watch.*

---

## 1. Why evals matter (read this even if you skip the rest)

An agent is a moving target. The model changes under you, someone edits a system
prompt, a tool's output shifts, the MCP gateway trims its catalog — and any one
of those can quietly make the agent worse. You will not notice by clicking around,
because you'll test the happy path you already know works.

**Evals are the fix, and they're not complicated.** An eval is just: a fixed
prompt, plus what a good answer must contain, plus a score. Run a handful of them
on every change and you get a regression test suite for behavior instead of code.

The professional habit is **evals-first**: write the check *before* you polish the
agent, so "done" means "passes the evals," not "looked fine when I tried it." This
harness is the smallest possible version of that habit for this playground — eight
representative cases, one command, a scorecard you can diff.

> This is not a benchmark and not a grade of the LLM. It's a *guardrail for your
> deployment* — it tells you whether **this stack, right now** still does the
> things you promised a customer it would.

---

## 2. What it does

`integrations/evals/run_evals.py` POSTs one real chat turn to each agent's n8n
chat webhook and scores the answer against expected substrings:

```
POST {BASE_URL}/webhook/<webhookId>/chat
body: {"action":"sendMessage","chatInput":"<prompt>","sessionId":"evals-harness"}
```

It's **standard-library only** (urllib) — no pip, nothing to install — so it runs
from a laptop or as a compose one-shot on the demo network. The eight shipped
cases exercise a representative slice of the fleet:

| Case | Agent | Checks |
|------|-------|--------|
| `reputation_8888_clean` | Reputation | `8.8.8.8` comes back **clean/benign** |
| `quantum_mgmt_access_layers` | Quantum Management | reports **3** layers incl. **Network** |
| `threat_prevention_profiles_optimized` | Threat Prevention | lists the **Optimized** profile |
| `documentation_identity_awareness` | Documentation | surfaces **Identity Awareness** material |
| `devhub_apps_count` | DevHub Ops | reports the registered **app** inventory |
| `policypilot_network_layer_summary` | PolicyPilot | summarizes the **Network** layer |
| `guarded_chat_injection_blocked` | Guarded Agent | a prompt-injection is **Blocked** |
| `guarded_chat_safe_passes` | Guarded Agent | a benign question is **not** blocked |

The last two are the important pair: an eval suite should prove your safety
controls fire on attacks **and** that they don't nuke legitimate traffic. A guard
that blocks everything scores 100% on attacks and is still useless.

---

## 3. How to run it

### From a laptop, against your deployment

```sh
BASE_URL=https://n8n.<your-domain> python3 integrations/evals/run_evals.py
```

### From inside the demo network (default target `http://n8n:5678`)

```sh
docker compose exec n8n python3 - < integrations/evals/run_evals.py
# ...or run the optional one-shot compose service (see integrations/evals/INTEGRATION.md)
```

No arguments, no dependencies. Useful environment knobs (all optional):

| Env | Default | Purpose |
|-----|---------|---------|
| `BASE_URL` | `http://n8n:5678` | agent host root (use `https://n8n.<domain>` from a laptop) |
| `CASES_FILE` | `evals_cases.json` (beside the script) | your case definitions |
| `OUT_DIR` | current directory | where `evals_report.md` / `.json` land |
| `TIMEOUT` | `90` | per-request seconds (LLM turns can be slow) |
| `ONLY` | *(unset)* | comma-separated case-name filter, e.g. `ONLY=reputation,guarded` |
| `SESSION_ID` | `evals-harness` | chat session id sent with every turn |

Exit code is **0** when every case passes and **1** when any fails — so you can
drop it into CI or a pre-demo check and let the exit code gate the pipeline.

---

## 4. Editing the cases (you will, and that's the point)

All cases live in `integrations/evals/evals_cases.json` — **edit that file, never
the code.** Each case:

```json
{
  "name": "reputation_8888_clean",
  "webhookId": "d9e1213c-ea8c-4482-aa48-36dffdb2e837",
  "prompt": "What's the reputation of 8.8.8.8?",
  "expect": ["clean"],
  "expect_any": ["clean", "benign", "safe"],
  "must_not": ["error"]
}
```

- `expect` — every substring must appear (AND, case-insensitive).
- `expect_any` — at least one must appear (OR). Use this to tolerate the wording
  variance you get from an LLM ("clean" vs "benign" vs "no known threats").
- `must_not` — none may appear. This is how the *safe* guarded case asserts it was
  **not** `Blocked`.

**Grading substrings is deliberately simple.** It's transparent, deterministic,
and needs no second model or API key. When you outgrow it, the natural next step
is an *LLM-as-judge* case — but start here; most regressions are caught by "did
the answer still contain the fact it's supposed to contain?"

### Where the `webhookId`s come from

The shipped ids are the **real** chat-trigger `webhookId`s committed in this
repo's workflows (`n8n/backup/workflows/*.json`) — the same ones the nightly
self-QA workflow probes — so the suite works against a stock deployment as-is. If
you re-create a workflow and n8n assigns a new id, read it off the canvas (open
the workflow → **When chat message received** node → the id in the webhook URL
`…/webhook/<THIS>/chat`) and paste it into the case. There's a `jq` one-liner in
the JSON's `_readme` too.

> **One gotcha for the guarded cases:** the injection-blocked case only passes when
> **`LAKERA_API_KEY`** is configured in n8n. Without a Lakera key the guard is
> skipped by design and the agent replies "Lakera Guard is not configured" — so
> the case correctly fails until you wire the key. That's the eval doing its job:
> it's telling you the safety control isn't actually on.

---

## 5. How to read the scorecard

Every run writes two files to `OUT_DIR`:

- **`evals_report.md`** — human-readable: a headline `PASS/TOTAL`, a per-case
  table, and a detail section with each prompt, what was expected, why a case
  failed, and a sample of the actual answer. Paste it into a PR or a demo debrief.
- **`evals_report.json`** — the same data, machine-readable, for trend-tracking or
  a dashboard.

A failing case shows *why*: `missing expected: 'Optimized'`, or
`forbidden substring present: 'Blocked'`, or a transport error like
`HTTP 404 (is the workflow ACTIVE and is the webhookId right?)`. Read the reason
before you touch the agent — half the time it's a deactivated workflow or a stale
webhookId, not a quality regression.

---

## 6. How this complements the nightly self-QA workflow

This repo already ships **`CP Agents — Nightly Self-QA`**
(`n8n/backup/workflows/nightly-self-qa.json`) — an n8n workflow that, on a
schedule, sends one turn to every agent and builds a pass/fail digest you can wire
to Slack or email. The two are siblings, not rivals:

| | Nightly Self-QA (n8n workflow) | Evals Harness (this script) |
|---|---|---|
| Runs | on a schedule, **inside** n8n | on demand, from a laptop or CI |
| Checks | "did the agent answer without erroring?" | "did the answer contain the **right facts**?" |
| Assertions | liveness / no-error | per-case `expect` / `expect_any` / `must_not` |
| Output | Markdown digest → Slack/email | `evals_report.md` + `.json`, exit code for CI |
| Edit cases | in the workflow's Code node | in `evals_cases.json` (no code) |

Use them together: the nightly workflow is your **smoke alarm** (something is
down), the evals harness is your **regression gate** (something got *worse*). A
practical rhythm — run the harness locally before every change and in CI on every
PR; let the nightly workflow watch the deployed stack between changes.

---

*Related: `docs/guides/MCP_Gateway_Explained.md` (how agents reach the tools),
`docs/guides/n8n_Lakera_Playground_Guide.md` (the guarded-chat safety controls the
last two cases exercise).*
