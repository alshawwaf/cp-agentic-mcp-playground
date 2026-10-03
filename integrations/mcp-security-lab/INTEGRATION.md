# MCP Security Lab: integration notes

The MCP Security Lab teaches how MCP attacks work and how to detect and stop them. Its target is
`vuln-mcp`, an intentionally vulnerable MCP server. Every attack it carries is simulated and
labelled: no real file is read, nothing leaves the lab, no code runs. The hands-on walkthrough is
[docs/guides/MCP_Security_Lab.md](../../docs/guides/MCP_Security_Lab.md).

| File | What it is |
|------|------------|
| `vuln_mcp_server.py` | The intentionally vulnerable server: Streamable HTTP MCP, standard library only, four teaching tools. |
| `test_vuln_mcp_server.py` | Guards the "simulated only" promise (unit mode) and checks a running server (live mode). |

The lab is opt-in: `vuln-mcp` starts only with the `security-lab` profile, never with the Standard or
Complete lab, and never in CI's live-stack run.

## The four tools

| Tool | Attack class | What it does |
|------|--------------|--------------|
| `weather_lookup` | Tool poisoning | Its description hides an instruction to the model |
| `fetch_ticket` | Indirect prompt injection | Its result smuggles instructions back to the model |
| `read_local_file` | Over-permissioned tool | Claims to read any file; returns fake, labelled "secrets" |
| `currency_convert` | Rug pull | Benign at first; its description turns malicious after the first call |

Every simulated payload carries the label `[SIMULATED — MCP SECURITY LAB / training only]`. The rug
pull resets itself 600 seconds after the last `currency_convert` call (`RUG_PULL_RESET_SECONDS`;
`0` keeps it armed). `POST /reset` resets it at once; `GET /health` shows its state. Both endpoints
are reachable only from the `security-lab` network.

## Containment

Nothing from the security lab can reach the Docker host or the internet.

| Control | Setting in `docker-compose.yml` |
|---------|--------------------------------|
| Network | `security-lab` only, an internal network: no internet access and no route to the Docker host. No published port. |
| Process | User `65534:65534`, read-only root file system, `/tmp` in memory (8 MB), all capabilities dropped, `no-new-privileges` |
| Files | Only the code, mounted read-only. No host data, no Docker socket. |
| Limits | 0.25 CPU, 96 MB |
| Authentication | None, on purpose: the lab shows a direct, unauthenticated server next to the gateway's mandatory Bearer token |
| Gateway | Not in `mcp-gateway/catalog.yaml`. The vulnerable server stays off the MCP Gateway. |

Only the agent builders and the scanner share the `security-lab` network with it: `n8n`,
`n8n-import`, `flowise`, `langflow`, `builders-import` and `aig-agent`. Postgres, LiteLLM and the
Check Point MCP servers are not on it.

The MCP Security Lab agent in each builder holds only the four `vuln-mcp` tools: no HTTP request,
code or other tool that a poisoned description could misuse.

Verified on a live lab (October 2026): from inside the running `vuln-mcp`, `aig-webserver` and
`aig-agent` containers, `1.1.1.1:443`, the Docker host gateway and `github.com` were unreachable, and
`host.docker.internal` did not resolve. The CI policy check
(`.github/scripts/check_compose_policy.py`) fails a pull request that adds these services to the
default lab, puts them on a network that is not internal, or removes their hardening.

## AI-Infra-Guard (profile `ai-red-team`)

AI-Infra-Guard scans the vulnerable server from inside the lab. It is a separate opt-in profile.

| Service | Networks | Notes |
|---------|----------|-------|
| `aig-webserver` | `ai-red-team` (internal) | Web UI with no sign-in, so it has no route and no published port |
| `aig-agent` | `ai-red-team`, `security-lab` (both internal) | The scanner. No `SYS_ADMIN`, no `seccomp=unconfined`, `no-new-privileges` |
| `aig-provision` | `ai-red-team` | One-shot: registers `lab-chat` (through LiteLLM) as a model, so scans can select it |
| `aig-ui` | `aig-ui-access`, `ai-red-team` | Reverse proxy to `http://aig-webserver:8088` only (`aig/ui-nginx.conf`). No port by default; publish `127.0.0.1:8088:8088` in a local override to open the UI |

LiteLLM joins `ai-red-team` only to serve `lab-chat` to the scanner, so scan prompts reach the model
provider behind `lab-chat`. On the live test, an MCP scan of `vuln-mcp` with `lab-chat` on Azure
OpenAI flagged `read_local_file` (high risk, file read) and `weather_lookup` (prompt injection in its
description). The scan report came back in Chinese although English was requested.

## Turn it on

Start these profiles only while you teach the lab, and stop them afterwards.

1. Add the profiles to `COMPOSE_PROFILES` in `.env`, for example
   `COMPOSE_PROFILES=complete,security-lab` (add `ai-red-team` for the scanner).
2. Start them and publish the agents:

   ```sh
   docker compose up -d
   docker compose run --rm n8n-import
   docker compose run --rm builders-import
   ```

   `n8n-import` publishes the n8n MCP Security Lab agent only when `vuln-mcp` answers, so run it
   after `vuln-mcp` is healthy. With 1Password references, put `op run --env-file=.env --` in front of
   each command.
3. Open **MCP Security Lab Agent (Intentionally Vulnerable)** in n8n, Flowise or Langflow and follow
   the guide.

## Verify

```sh
tests/acceptance/run.sh --only SECLAB,AIG
```

`SECLAB` checks that `vuln-mcp` is healthy on the `security-lab` network only, lists its four tools,
finds the simulated payload in `weather_lookup`, reports the rug-pull state, and finds the n8n
Security Lab agent published. It calls no tool. `AIG` checks that the AI-Infra-Guard web UI answers and its
agent runs. Both passed on the live test. `./scripts/doctor.sh --post-start` also probes `vuln-mcp`
when the profile is on.

## Turn it off

Remove `security-lab` and `ai-red-team` from `COMPOSE_PROFILES`, then stop and remove their
containers (they hold no lab data):

```sh
docker compose --profile security-lab --profile ai-red-team stop vuln-mcp aig-agent aig-webserver aig-ui
docker compose --profile security-lab --profile ai-red-team rm -f vuln-mcp aig-agent aig-webserver aig-provision aig-ui
```

AI-Infra-Guard keeps its scan history in the `aig_*` volumes.

## Tests

Never run `vuln_mcp_server.py` on the host. The unit tests run it in a throwaway container with no
network, a read-only root, no capabilities and uid 65534:

```sh
.github/scripts/py-isolated.sh -- python3 integrations/mcp-security-lab/test_vuln_mcp_server.py
```

They fail if the server imports anything beyond a small standard-library allow-list, calls
`open()`, `exec()` or `eval()`, or opens a file, starts a process or makes an outbound connection
while it serves the handshake and every tool.
