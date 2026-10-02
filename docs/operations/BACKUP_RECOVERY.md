# Backup and recovery

Two scripts back up and restore the lab: `scripts/backup-volumes.sh` and
`scripts/restore-volumes.sh`. A backup is one encrypted archive that holds everything the lab
needs to come back: the data, the builder settings and the `.env` whose keys decrypt them.

## What a backup holds

| Item | Contents | Notes |
|------|----------|-------|
| Docker volumes of the lab's Compose project | `n8n_storage` (n8n), `postgres_storage` (n8n, Flowise and Langfuse databases), `qdrant_data`, and, once their services have run, `langflow` and `open-webui` (Complete lab), the `aig_*` volumes (`ai-red-team`) and `policypilot_data` (`policypilot`) | Found by their Compose label, so any project name works |
| Ollama models (`ollama_storage`) | Skipped by default | The lab downloads them again on start. `--include-models` adds them |
| `./flowise_data` | Flowise's encryption key, session secrets and uploads | |
| `./n8n/shared` | Files shared with the MCP servers (CPInfo files, files to scan) and the evals reports | |
| `.env` | Every setting and secret | n8n can read its stored credentials only with the `N8N_ENCRYPTION_KEY` in it |
| Postgres SQL dump | `pg_dumpall` of the lab Postgres | Only when `postgres` is running: a consistent copy even while the lab runs |

The archive holds secrets and lab data, so it is encrypted with AES-256 (`openssl enc -aes-256-cbc`,
PBKDF2 with 200,000 iterations) and written with mode 600. It carries no authentication code: store
it where only administrators can write.

## Prerequisites

- `sh`, `docker` and `openssl` on the host (macOS and Linux include `openssl`).
- A passphrase of at least 12 characters, kept in your password manager. Without it the backup
  cannot be restored.

## Back up

```sh
./scripts/backup-volumes.sh
```

It asks for the passphrase twice (hidden). For scheduled runs, give it a passphrase file (first line,
mode 600) or the variable `BACKUP_PASSPHRASE`:

```sh
./scripts/backup-volumes.sh --passphrase-file /root/.lab-backup-pass
```

| Option | Meaning |
|--------|---------|
| `--output-dir DIR` | Where to write the archive (default `./backups`, git-ignored) |
| `--volumes NAME,...` | Only these items: volume names (`n8n_storage` or `<project>_n8n_storage`), folders (`flowise_data`, `n8n/shared`) and `postgres-dump` |
| `--include-models` | Also save the Ollama models |
| `--passphrase-file FILE` | Read the passphrase from the first line of FILE |
| `--no-encrypt` | Write a plain archive. It holds secrets: store it encrypted |
| `--retention-days N` | Remove this project's backups older than N days from the output folder (default 30; 0 keeps every backup) |
| `--project-dir DIR` | Back up the lab in another directory |

Expected result:

```
Backup written: <dir>/lab-backup-<project>-<YYYYmmdd-HHMMSS>.tar.gz.enc (<size>, mode 600)
Restore with: ./scripts/restore-volumes.sh "<archive>"
```

The script reads the encrypted archive back before it reports success. If any item fails, it writes
no archive.

**Consistency.** Databases that change during the copy can be inconsistent. While the lab runs, the
script warns and still includes the SQL dump, which is consistent. For a fully consistent volume
copy, stop the lab first:

```sh
docker compose stop
./scripts/backup-volumes.sh --passphrase-file /root/.lab-backup-pass
docker compose start
```

With the lab stopped there is no SQL dump; the volume copy is then consistent on its own.

### Schedule it

Example crontab line, daily at 02:00, keeping 30 days:

```sh
0 2 * * * cd /path/to/cp-agentic-mcp-playground && ./scripts/backup-volumes.sh --passphrase-file /root/.lab-backup-pass --retention-days 30 >> /var/log/lab-backup.log 2>&1
```

Copy the archives to storage your organization approves for lab data, off the lab host. Keep the
passphrase apart from the archives.

## Restore

```sh
docker compose stop
./scripts/restore-volumes.sh backups/lab-backup-<project>-<time>.tar.gz.enc
```

The restore is safe by design:

1. It decrypts and checks the whole archive before it changes anything. A wrong passphrase or a
   damaged file stops it with "Nothing was changed."
2. It refuses to run while containers of the lab are running.
3. It shows the plan (every volume it replaces or creates, every folder, what happens to `.env`) and
   asks you to type the project name. `--yes` skips the question.
4. It restores into the Compose project of this directory, whatever its name. A backup taken under
   another project name restores fine. Each volume is emptied completely and filled from the backup.
   Every volume is attempted and reported.
5. Folders are never deleted: the current `./flowise_data` and `./n8n/shared` are renamed to
   `<name>.before-restore-<time>` first.
6. `.env` is never replaced silently. The backup's copy is saved as `.env.from-backup` (mode 600) and
   your `.env` stays active. `--with-env` makes the backup's copy the active `.env` and keeps yours as
   `.env.before-restore`.
7. The SQL dump, when the backup holds one, is copied to `backups/postgres-dumpall-<time>.sql.gz`.
   It is not loaded automatically.

| Option | Meaning |
|--------|---------|
| `--volumes NAME,...` | Restore only these volumes or folders |
| `--with-env` | Make the backup's `.env` the active one |
| `--passphrase-file FILE` | Read the passphrase from FILE (or set `BACKUP_PASSPHRASE`) |
| `--yes`, `-y` | Do not ask for the project name |
| `--project-dir DIR` | Restore into the lab in another directory |

Then start the lab and check it:

```sh
docker compose up -d                                  # or: op run --env-file=.env -- docker compose up -d
./scripts/doctor.sh --post-start
```

**The encryption keys must match the data.** n8n reads its restored credentials only with the
`N8N_ENCRYPTION_KEY` the data was written with, and Postgres and Langfuse keep `POSTGRES_PASSWORD`,
`SALT` and `LANGFUSE_ENCRYPTION_KEY`. If you restore without `--with-env` and the restored n8n cannot
read its credentials, copy `N8N_ENCRYPTION_KEY` (and `POSTGRES_PASSWORD`) from `.env.from-backup` into
`.env`, then `docker compose up -d`.

## Recover on a new host

1. Install Docker and clone the repository.
2. Copy the archive to the new host.
3. Restore it with its `.env`:

   ```sh
   ./scripts/restore-volumes.sh /path/to/lab-backup-<project>-<time>.tar.gz.enc --with-env
   ```

4. Run `./setup.sh --non-interactive`. It keeps every value of the restored `.env`, adds settings that
   are new in this lab version and creates the `dokploy-network` network when it is missing. It
   generates no new key for a lab whose n8n and Postgres volumes exist.
5. Start the lab: `docker compose up -d`. The Ollama models download again on the first start.
6. Check it: `./scripts/doctor.sh --post-start`, then `tests/acceptance/run.sh`.

With 1Password, the restored `.env` holds `op://` references: start and check the lab through
`op run --env-file=.env --`.

## Test your backups

A backup you never restored is a hope, not a backup. Restore the latest archive on a second host
regularly and run the acceptance tests there. Do not restore a test copy on the lab host itself: the
lab's containers use fixed names, so a second copy of the lab cannot run next to the first.

## Troubleshooting

| Message | Fix |
|---------|-----|
| `openssl is required to encrypt the backup` | Install `openssl`, or pass `--no-encrypt` and encrypt the archive yourself |
| `the backup passphrase must be at least 12 characters` | Use a longer passphrase |
| `set BACKUP_PASSPHRASE, use --passphrase-file FILE, or run in a terminal` | The script runs without a terminal (for example from cron). Pass `--passphrase-file` |
| `N containers of this lab are running` (backup) | A warning. Stop the lab first for a fully consistent copy |
| `cannot decrypt or read ... Nothing was changed.` | Wrong passphrase or damaged file |
| `stop the lab first (docker compose stop) ... Nothing was changed.` | Run `docker compose stop`, then the restore again |
| `FAIL volume ...` during the restore | The restore reports every volume. Fix the cause (disk space, Docker) and run it again before you start the lab |
| n8n shows credential errors after a restore | `N8N_ENCRYPTION_KEY` in `.env` differs from the backup's: copy it from `.env.from-backup` |
