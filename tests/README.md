# Tests

Two kinds of tests protect the lab. The **lab tests** check a lab that is already running; they never
stop the lab and never remove containers, volumes or images. The **offline tests** need no running
lab and run with no network (only the `tests/mcp-src` build step downloads npm packages); CI runs
them on every pull request.

| What | Kind | Use it for |
|------|------|------------|
| [`scripts/doctor.sh`](../scripts/doctor.sh) | Lab | Quick check of settings (`--preflight`) and health (`--post-start`), with a capability matrix |
| [`tests/acceptance/run.sh`](acceptance/run.sh) | Lab | The acceptance tests: every promise of the lab, end to end |
| [`tests/integration-test.sh`](integration-test.sh) | Lab | Validates the compose file, optionally starts the lab, waits for it, then runs the acceptance tests |
| [`tests/mcp-src/run.sh`](mcp-src/run.sh) | Offline | Security regression tests of the vendored Check Point MCP servers |
| [`tests/rag-threshold/run.sh`](rag-threshold/run.sh) | Offline | The RAG relevance threshold in the committed n8n and Langflow retrievers |
| `integrations/*/test_*.py`, `exercises/build-your-own-mcp/test_byo_mcp.py` | Offline | Unit tests of the code-first agent, evals, RAG ingest, MCP Security Lab server and Build Your Own MCP exercise |
| `scripts/flows/*_fix.py check`, `scripts/check_workflow_uniqueness.py` | Offline | The builder flows match the design (see [scripts/README.md](../scripts/README.md)) |

## Acceptance tests

Run them after `docker compose up -d`, once `./scripts/doctor.sh --post-start` shows no blockers
(usually 5 to 10 minutes after the first start):

```sh
tests/acceptance/run.sh                                  # every check
op run --env-file=.env -- tests/acceptance/run.sh        # .env holds 1Password references
tests/acceptance/run.sh --only GW-AUTH,GW-TOOLS          # some checks
tests/acceptance/run.sh --no-model --json result.json    # CI without a model key
tests/acceptance/run.sh --mock-provider                  # also prove the provider key plumbing
```

How it runs:

- On the host it needs only `sh` and `docker`. The checks run in one throwaway
  `python:3.12-alpine` container (the digest `docker-compose.yml` pins; 1 CPU, 384 MB) on the lab
  network, and the container is removed afterwards.
- Secrets reach that container through a private env file (mode 600) that is deleted as soon as
  the container is created. Results never contain secret values: every line is redacted.
- Checks of profiles that are off are reported as `SKIP` with the profile name.
- Model calls (one short prompt per agent tested) run by default when `.env` has a cloud model
  key or a local Ollama chat model. `--no-model` turns them off; `--with-model` forces them.
- Side effects are limited to what a trainee does: chat sessions and executions in n8n, Flowise
  and Langflow, and traces in Langfuse. LANGFLOW-RUN creates a temporary Langflow API key and
  deletes it again (set `LANGFLOW_API_KEY` to use your own). RAG embeds one query with the local
  `nomic-embed-text` model. No AI-Infra-Guard scan is started.
- `--mock-provider` starts two more throwaway containers with no network at all: a mock model
  provider and a LiteLLM (the image `docker-compose.yml` pins, 1 CPU, 1 GB) that uses it. The test
  proves that the provider key from `.env` reaches the provider and the LiteLLM master key does
  not. The provider key cannot leave the machine during this test.

### Checks

| ID | Promise | Runs when |
|----|---------|-----------|
| STACK | Every service of the active profiles is healthy; one-shot jobs exited 0; no crash loops | always |
| GW-AUTH | MCP gateway: HTTP 401 without a token and with a wrong token, 200 with `MCP_GATEWAY_TOKEN` | always |
| GW-TOOLS | The gateway lists every server's tools: per-server counts equal `SERVER_TOOLS` (190 in total) | always |
| DIRECT | The 11 gateway-fronted MCP servers answer `tools/list` directly with their `SERVER_TOOLS`; Spark Management (`spark-management-mcp`) and SASE (`harmony-sase-mcp`) pass as "not configured" while their settings are blank | always |
| LITELLM | `lab-chat` is listed; HTTP 401 without a key and with a wrong key; a short completion and one tool-call round trip | always (completion with model calls) |
| KEYS | No builder container (n8n, n8n-import, Flowise, Langflow, builders-import) holds a provider key (variable names reported, never values); with `--mock-provider` the key from `.env` reaches the provider | always |
| N8N-SEED | 35 workflows and 8 credentials; the published set matches the prerequisites in `.env`; chat triggers return 401 without sign-in and 200 with the lab admin | always |
| N8N-RUN | The Documentation agents (MCP Gateway and Direct) answer, and their executions show an MCP tool call | model calls |
| FLOWISE-SEED | 33 agents seeded, credential "Lab Model (LiteLLM)", tool counts (Flowise node-load-method) match `integrations/builders_agents.json` | always |
| FLOWISE-RUN | The prediction API refuses calls without the "Lab Agents API" key; the Documentation agents use a tool | model calls |
| LANGFLOW-SEED | 33 flows, global variable `LITELLM_MASTER_KEY`, MCPTools tool counts match the catalog | profile `langflow` or `complete` |
| LANGFLOW-RUN | The Documentation flows answer with a tool call | profile `langflow` or `complete`, model calls |
| RAG | Qdrant `cp_docs` holds points with the `nomic-embed-text` dimension; retrieval returns hits with sources; the RAG agent cites a source | always (agent with model calls) |
| CODE-AGENT | `integrations/code-agent/mcp_gateway_client.py` handshake and `tools/list`; `agent_loop.py` makes a tool call through `lab-chat` | always (agent loop with model calls) |
| LANGFUSE | Langfuse is up, accepts the project keys, and received the traces of this run's model calls | model calls |
| OPENWEBUI | Admin sign-in, sign-up closed, the default model listed | profile `local-chat` or `complete` |
| SECLAB | `vuln-mcp` healthy on the isolated `security-lab` network only, tools listed, the simulated attack payload in a tool description, the n8n Security Lab agent published. No tool is called. | profile `security-lab` |
| AIG | AI-Infra-Guard web UI answers and its agent runs | profile `ai-red-team` |
| EXERCISES | The Build Your Own MCP server (`ips-cve-mcp`) answers `tools/list` | profile `exercises` |
| EVALS | The evals scorecard exists and has no wiring failures (model-quality misses are reported, not failed) | profile `evals` |

### Options

| Option | Meaning |
|--------|---------|
| `--only IDS`, `--skip IDS` | Comma-separated check IDs |
| `--profile-aware` (default), `--no-profile-aware` | SKIP checks of profiles that are off, or run them anyway |
| `--with-model`, `--no-model` | Force model calls on or off |
| `--mock-provider` | KEYS: prove the provider key plumbing with a mock provider |
| `--with-scan` | Accepted for AIG; the scan test is owner-supervised and not automated |
| `--json FILE` | Also write the results as JSON |
| `--env-file FILE`, `--project-dir DIR`, `--project-name NAME` | Another settings file, lab directory or Compose project |
| `--list` | List the checks |

`DOCKER_COMPOSE=/path/to/wrapper` runs every Compose command through a wrapper that adds `-p` / `-f`
options. `LAB_MODEL_TIMEOUT` (seconds per model call, default 180), `LAB_TRACE_WAIT` (default 90)
and `LAB_DETAIL_WIDTH` (table width, default 200) tune the run.

### Reading the result

One line per check: `PASS`, `FAIL` or `SKIP` and a one-line reason. Every `FAIL` has a `fix:` line
under it. The last line repeats the failed IDs for `--only`. Exit status: `0` no check failed, `1` a
check failed, `2` the suite could not run (Docker down, no lab found, unknown option).

The JSON result (`--json`) holds the same lines plus details per check (for example the per-server
tool counts and every unhealthy service).

## Integration test

```sh
tests/integration-test.sh                          # check the running lab
tests/integration-test.sh --up --no-model          # start or update the lab (CI), then check it
tests/integration-test.sh --up -- --json result.json
```

It runs `docker compose config --quiet`, with `--up` also `docker compose up -d`, waits until no
service is still starting (`--wait SECONDS`, default 900), then runs the acceptance tests with every
other option. It never stops the lab.

## Results of the last end-to-end runs

Fresh installs in October 2026:

| Run | Result |
|-----|--------|
| Standard lab, `--no-model --mock-provider` | 10 passed, 0 failed, 10 skipped (profiles off, model calls off). STACK 30 of 30 services, 190 gateway tools, 35 n8n workflows (29 published), 33 Flowise agents |
| Standard lab, model checks on the local Ollama model (`qwen3.5:4b`, no cloud key) | LITELLM, N8N-RUN, FLOWISE-RUN, RAG, CODE-AGENT and LANGFUSE passed |
| Complete lab, Azure OpenAI through 1Password, `--with-model --mock-provider` | 16 passed, 0 failed, 4 skipped (`security-lab`, `ai-red-team`, `exercises` and `evals` off). STACK 34 of 34 services |
| Complete lab with `security-lab` and `ai-red-team` | SECLAB and AIG passed |

## Offline tests

These need no running lab. The tests run in throwaway containers with no network and no
capabilities, and never write to the repository. On the host they need only `sh` and `docker`.

### Vendored MCP servers: `tests/mcp-src/`

Security regression tests for the patches in `docker/n8n/mcp-src` (listed in
[docker/n8n/mcp-src/PATCHES.md](../docker/n8n/mcp-src/PATCHES.md)).

```sh
tests/mcp-src/run.sh                                     # build, test, clean up
tests/mcp-src/run.sh --work DIR --build-only             # CI step 1: build into DIR
tests/mcp-src/run.sh --work DIR --skip-build             # CI step 2: test that build
tests/mcp-src/run.sh --work DIR --skip-build tls gaia    # some suites only
```

1. **Build.** It copies the sources to a work directory outside the repository and builds them in one
   throwaway Node container (the builder image `docker/n8n/Dockerfile` pins), exactly as the image
   build does. This step reaches the npm registry only.
2. **Test.** It runs the suites in one container with no network. Test certificates are generated
   for the run; fake Check Point APIs listen on loopback.

The nine suites: `tls` (certificate verification always on), `session` (idle timeout, session cap),
`delete` (clean session close), `gaia` (credentials only for configured gateways), `cpinfo` and `te`
(files only from the allowed folders), `spark` (starts without credentials), `policy-insights` (the
vendored server lists the same tools as upstream 0.3.5) and `static` (every server built, no TLS
switch-off, no telemetry, secrets redacted). Nothing is written to the repository. Options:
`--npm-cache DIR` reuses an npm cache, `--keep` keeps the work directory.

### RAG threshold: `tests/rag-threshold/`

```sh
tests/rag-threshold/run.sh            # both
tests/rag-threshold/run.sh langflow   # or: n8n
```

`langflow` runs the Check Point Docs Retriever component of `integrations/langflow/rag-cp-docs.flow.json` in the
pinned Langflow image; `n8n` imports `n8n/backup/workflows/rag-cp-docs-retriever.json` into a
throwaway n8n (the base image `docker/n8n/Dockerfile` pins). Both run against a mock Ollama and Qdrant
and check that snippets below the 0.5 threshold never reach the agent. `scripts/flows/flowise_fix.py
check` covers the Flowise retriever.

### Unit tests

Run each through `.github/scripts/py-isolated.sh`, which starts the Python image
`docker-compose.yml` pins with no network, a read-only repository mount and uid 65534:

```sh
.github/scripts/py-isolated.sh -- python3 integrations/code-agent/test_code_agent.py
.github/scripts/py-isolated.sh -- python3 integrations/evals/test_evals.py
.github/scripts/py-isolated.sh -- python3 integrations/rag-cp-docs/test_ingest.py
.github/scripts/py-isolated.sh -- python3 integrations/mcp-security-lab/test_vuln_mcp_server.py
.github/scripts/py-isolated.sh -C exercises/build-your-own-mcp/solution -- python3 ../test_byo_mcp.py ips_cve_mcp.py
.github/scripts/py-isolated.sh -C exercises/build-your-own-mcp -- python3 test_byo_mcp.py --scaffold-check scaffold/ips_cve_mcp.py
```

Never run `integrations/mcp-security-lab/vuln_mcp_server.py` on the host: its test runs it only
inside that isolated container.

### Flow and workflow checks

```sh
.github/scripts/py-isolated.sh -- python3 scripts/flows/langflow_fix.py check
.github/scripts/py-isolated.sh -- python3 scripts/flows/flowise_fix.py check
.github/scripts/py-isolated.sh -- python3 scripts/flows/n8n_fix.py check
.github/scripts/py-isolated.sh -- python3 scripts/check_workflow_uniqueness.py
```

## What CI runs

| Workflow | When | Tests |
|----------|------|-------|
| `.github/workflows/ci.yml` | Every pull request and push to `main` (merge gate) | Setup and compose checks, the compose policy check, the flow and workflow checks, shellcheck, actionlint, `py_compile`, every unit test, setup and preflight on macOS, `tests/mcp-src`, `tests/rag-threshold`, the lab image build with an MCP smoke test and a Trivy gate |
| `.github/workflows/live-stack.yml` | Nightly and on demand | The Standard lab from the commit, then `tests/integration-test.sh` (acceptance). Never starts `security-lab` or `ai-red-team`. Model calls only when a model key is stored as a repository secret |
| `.github/workflows/security-scan.yml` | Weekly and on demand | Trivy report of the published lab images and every pinned third-party image |

## Helpers

[`test-helpers.sh`](test-helpers.sh) (POSIX sh, sourced): `log_info`, `log_success`, `log_error`,
`log_warning`, `assert_equals EXPECTED ACTUAL DESCRIPTION`, `assert_true EXIT_STATUS DESCRIPTION`
and `print_test_summary`. The assertions count and print only, so they never stop a script that
runs under `set -e`.

## Troubleshooting

- **GW-AUTH and GW-TOOLS fail with "mcp-gateway is not running (crash loop)".** The gateway reads
  Docker through `docker-socket-proxy`. Start both: `docker compose up -d docker-socket-proxy mcp-gateway`.
- **GW-TOOLS shows a server with 0 tools.** The server was not ready when the gateway listed its
  tools: `docker compose restart mcp-gateway`.
- **Many checks fail with "... is not running".** Start the lab (`docker compose up -d`) and wait
  until `./scripts/doctor.sh --post-start` shows no blockers.
- **"unresolved 1Password references".** Run the tests the way you start the lab:
  `op run --env-file=.env -- tests/acceptance/run.sh`.
- **A check is SKIP with a profile name.** That part of the lab is off. Add the profile to
  `COMPOSE_PROFILES` in `.env`, then `docker compose up -d`.

Fix a failing check in the service it names. Never remove volumes (`docker compose down -v`) or
prune images to make a test pass: that deletes the lab's workflows, credentials and models.
