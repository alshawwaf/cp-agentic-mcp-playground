# Scripts

Operator scripts for the Check Point AI agent lab. Run them from the repository root.

| Script | What it does | Who runs it |
|--------|--------------|-------------|
| `../setup.sh` | Guided setup: writes `.env` with every key and generated secret | You, once (and after updates) |
| `doctor.sh` | Checks settings before the start and the running lab after it; prints what works | You |
| `health-check.sh` | Quick health check of the running lab (`doctor.sh --health`) | You, or a scheduler |
| `validate-env.sh` | Checks `.env` before the start (`doctor.sh --preflight`) | You, or CI |
| `backup-volumes.sh` | Encrypted backup of the lab's data and `.env` | You, or a scheduler |
| `restore-volumes.sh` | Restores a backup, with safety checks | You |
| `../update.sh`, `../update.ps1` | Updates the lab to the latest repository version | You |
| `n8n-provision.sh` | n8n owner setup and the n8n import | The `n8n-provision` and `n8n-import` services |
| `openwebui-provision.sh` | Creates the Open WebUI admin | The `openwebui-provision` service |
| `aig-provision.sh` | Registers `lab-chat` in AI-Infra-Guard | The `aig-provision` service |
| `check_workflow_uniqueness.py` | Audits the n8n import set for duplicate ids and broken references | You, and CI |
| `convert_guides_to_docx.py` | Converts the Markdown guides to Word files | Maintainers |
| `flows/langflow_fix.py`, `flows/flowise_fix.py`, `flows/n8n_fix.py` | Generate and check the builder flows | Maintainers, and CI (`check`) |
| `lib/labenv.sh`, `lib/stack_probe.py` | Shared helpers | The scripts above |

What every shell script here has in common:

- POSIX `sh`: it runs with `/bin/sh` on macOS and Linux. On the host it needs only `sh` and
  `docker` (`backup-volumes.sh` and `restore-volumes.sh` also use `openssl`).
- Network checks run in one throwaway container at a time, with capped CPU and memory, removed
  afterwards.
- No secret value is ever printed. Results name variables and services only.
- Exit status: `0` no problem, `1` a problem was found, `2` the script could not run.
- With 1Password references in `.env`, run the script the way you start the lab:
  `op run --env-file=.env -- ./scripts/doctor.sh --post-start`.

## setup.sh (repository root)

```sh
./setup.sh                    # guided: lab components, model provider, Check Point keys
./setup.sh --non-interactive  # no questions: keys from the environment (aliases -y, --ci)
./setup.sh --1password        # guided, writes op://vault/item/field references instead of keys
```

It shows the CPUs and memory Docker can use and offers the **Standard lab** (default, Docker with
4 CPUs and 8 GB) or the **Complete lab** (`COMPOSE_PROFILES=complete`, Docker with 6 CPUs and 16 GB).
It generates every lab secret, writes `.env` with mode 600, creates the `dokploy-network` Docker
network when it is missing and finishes with `doctor.sh --preflight`. Re-running keeps every value
you set.

## doctor.sh

```sh
./scripts/doctor.sh --preflight             # before the first start
./scripts/doctor.sh --preflight --online    # also validate each model key and reach the Check Point hosts
./scripts/doctor.sh --post-start            # 5 to 10 minutes after docker compose up -d
./scripts/doctor.sh --post-start --skip-chat
```

| Option | Meaning |
|--------|---------|
| `--preflight` (default) | `.env` complete and valid, 1Password references, the admin password rule, key formats, the model provider settings (`docker compose run --rm --no-deps litellm --check`), Docker networks, and the lab components against the CPUs and memory Docker can use |
| `--online` | With `--preflight`: one harmless model-list call per supplied model key, and TCP reachability of the Check Point hosts |
| `--post-start` | Every service healthy and every one-shot job exited 0; MCP Gateway: 401 without a token and with a wrong one, tools with `MCP_GATEWAY_TOKEN`; each MCP server directly; agents seeded in n8n, Flowise and Langflow; `lab-chat` answers one short prompt; Langfuse accepts the keys and received the trace; the RAG collection; the MCP Security Lab server when its profile is on |
| `--skip-chat` | With `--post-start`: do not send the test prompt |
| `--env-file FILE` | Another settings file (default `.env`) |
| `--project-dir DIR` | Another lab directory |
| `--verbose`, `-v` | Also list every healthy service |

Both modes end with a capability matrix, "What works with these settings": one line per path
(`ready`, `needs` with the missing variables, `off` for parts the lab components leave out, `check`).
Lab parts that are off, such as Langflow in the Standard lab, are never reported as failures. After a
clean `--post-start` it points to the acceptance tests: `tests/acceptance/run.sh`.

## health-check.sh

```sh
./scripts/health-check.sh [--verbose] [--project-dir DIR] [--env-file FILE]
```

Every service running and healthy (one-shot jobs exited 0, crash loops and unhealthy containers
fail), the MCP Gateway from inside the lab network (401 without a token and with a wrong token,
tools with `MCP_GATEWAY_TOKEN`) and each MCP server directly. It sends no prompt and checks no
seeded agent; use `doctor.sh --post-start` for that. `--profile` is accepted and ignored. Example
schedule (crontab):

```sh
*/15 * * * * cd /path/to/cp-agentic-mcp-playground && ./scripts/health-check.sh >> /var/log/lab-health.log 2>&1
```

## validate-env.sh

```sh
./scripts/validate-env.sh [--online] [--env-file FILE]      # ENV_FILE=FILE also works
```

The same as `doctor.sh --preflight`. It changes nothing and reports every problem at once.

## backup-volumes.sh and restore-volumes.sh

```sh
./scripts/backup-volumes.sh                                   # asks for a passphrase twice
./scripts/backup-volumes.sh --passphrase-file /root/.lab-backup-pass --retention-days 30
./scripts/restore-volumes.sh backups/lab-backup-<project>-<time>.tar.gz.enc
```

The backup holds every Docker volume of the lab's Compose project (except the Ollama models), the
`./flowise_data` and `./n8n/shared` folders, `.env` and a Postgres SQL dump, in one archive encrypted
with AES-256. The restore checks the whole archive first, refuses to run while the lab is running and
asks you to type the project name. Options, contents and procedures:
[docs/operations/BACKUP_RECOVERY.md](../docs/operations/BACKUP_RECOVERY.md).

## update.sh and update.ps1 (repository root)

```sh
./update.sh            # macOS and Linux
./update.ps1           # Windows (needs sh from Git for Windows or WSL for the .env step)
```

1. `git pull --ff-only`: stops, changing nothing, if local edits would conflict.
2. `./setup.sh --non-interactive`: adds new settings to `.env` and fills new blank secrets. Your values
   are kept.
3. `docker compose pull`: newer images.
4. `docker compose up -d`: recreates only what changed, through `op run` when `.env` holds 1Password
   references.

Agents you changed in n8n, Flowise or Langflow are kept. Check the lab afterwards with
`./scripts/doctor.sh --post-start`.

## n8n-provision.sh

Runs inside the lab; you do not start it directly. Modes:

| Mode | Service | What it does |
|------|---------|--------------|
| `owner` (default) | `n8n-provision` (curl image) | Waits until n8n is ready (`/healthz/readiness`), creates the n8n owner from `N8N_ADMIN_EMAIL` and `N8N_ADMIN_PASSWORD` on the first start, and checks that the owner can sign in |
| `import` | `n8n-import` (lab image) | Fills the credential placeholders from the environment (re-synced on every run), fills `{{DOMAIN}}` and the Lakera Guard settings, imports the workflows (changed ones are kept unless `N8N_SEED_OVERWRITE=1`) and publishes them through the n8n REST API. Workflows whose prerequisites are not met are imported but not published |
| `publish` | lab image | The publishing step only |

Required for `import`: `LITELLM_MASTER_KEY`, `MCP_GATEWAY_TOKEN`, `N8N_ADMIN_EMAIL`,
`N8N_ADMIN_PASSWORD`. Optional: `LAKERA_API_KEY`, `LAKERA_PROJECT_ID`, `IDP_SCIM_TOKEN`,
`DEVHUB_MCP_TOKEN`, `PILOT_MCP_TOKEN`, `QDRANT_API_KEY`, `NIGHTLY_SELF_QA`, `N8N_SEED_OVERWRITE`.
The filled-in credential files live only in a private temporary directory on a memory-backed `/tmp`.

```sh
docker compose run --rm n8n-import                              # after a change to .env
docker compose run --rm -e N8N_SEED_OVERWRITE=1 n8n-import      # also replace workflows changed in n8n
docker compose run --rm n8n-import /scripts/n8n-provision.sh publish
```

## openwebui-provision.sh

Run by the `openwebui-provision` service (profiles `local-chat` and `complete`). It creates the Open
WebUI admin from `OPEN_WEBUI_ADMIN_EMAIL` and `OPEN_WEBUI_ADMIN_PASSWORD`, or the lab admin when those
are blank, so Open WebUI never waits for "the first person to sign up becomes admin". On later starts
it signs in with the same account to prove the admin is the lab's, and fails if it is not (for
example after the password was changed in Open WebUI).

## aig-provision.sh

Run by the `aig-provision` service (profile `ai-red-team`), on the internal `ai-red-team` network
only. It registers `lab-chat` (`http://litellm:4000/v1`, key `LITELLM_MASTER_KEY`) as a model in
AI-Infra-Guard, so scans can select it. An existing `lab-chat` model is left as it is. It never
prints the key.

## check_workflow_uniqueness.py

```sh
python3 scripts/check_workflow_uniqueness.py
```

Checks `n8n/backup` for duplicate workflow ids and names, chat-trigger and webhook ids, webhook
paths, node ids and names inside a workflow, and credential ids, and that every credential a node
references exists in `credentials_public` with the same name. Exit 1 with every violation listed.
CI runs it on every pull request.

## convert_guides_to_docx.py

```sh
python3 scripts/convert_guides_to_docx.py [docs/guides/GUIDE.md ...]
```

Writes a `.docx` next to each Markdown guide (all of `docs/guides/*.md` without arguments). It needs
the `python-docx` package (MIT license) on the machine that runs it. Regenerate the Word files after
you change a guide.

## flows/: the builder flow generators

| Script | Files | Commands |
|--------|-------|----------|
| `langflow_fix.py` | `integrations/langflow/*.flow.json` | `snapshot --out DIR`, `apply [--snapshot DIR]`, `check` |
| `flowise_fix.py` | `integrations/flowise/*.flowdata.json` | `snapshot --out DIR`, `apply [--snapshot DIR]`, `check` |
| `n8n_fix.py` | `n8n/backup/workflows/*.json`, `n8n/backup/credentials_public/*.json` | `apply`, `check` |

`check` validates the files and exits 1 on any problem (CI runs all three). `apply` rewrites the
files in place; running it twice gives the same files. `snapshot` runs inside the lab network and
saves what the installed builder version needs for `apply --snapshot`. `langflow_fix.py` holds the
shared tables (`SERVER_TOOLS`, the curated tool lists, `RAG_MIN_SCORE`), which the other two import.
When and how to use them: [docs/development/DEVELOPER_GUIDE.md](../docs/development/DEVELOPER_GUIDE.md).

## lib/

| File | Used by | What it does |
|------|---------|--------------|
| `labenv.sh` | `setup.sh`, `doctor.sh`, backup and restore, the tests | Reads `.env` the way Docker Compose does, detects 1Password references and placeholders, checks the password rule, finds the Compose project name and Docker's CPUs and memory, makes private temporary directories |
| `stack_probe.py` | `doctor.sh` | The in-network checks. Runs in one throwaway `python:3.12-alpine` container, never on the host; secrets arrive as environment variables passed by name |
