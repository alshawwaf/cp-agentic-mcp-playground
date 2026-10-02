#!/bin/sh
# update.sh: update the Check Point AI agent lab to the latest repository version.
#
#   1. git pull --ff-only     stops, changing nothing, if local edits would conflict
#   2. ./setup.sh -y          adds new settings to .env and fills new blank secrets; every value you
#                             set is kept (nothing is regenerated that the running lab already uses)
#   3. docker compose pull    newer images
#   4. docker compose up -d   recreates only what changed (through 1Password when .env holds op:// references)
#
# Agents you changed in n8n, Flowise or Langflow are kept. To replace them once with the new
# repository versions, set N8N_SEED_OVERWRITE=1 and SEED_OVERWRITE=1 in .env for one run.
# Afterwards check the lab: ./scripts/doctor.sh --post-start

set -u
cd "$(dirname "$0")" || exit 2

step() { printf '\n== %s\n' "$*"; }
fail() { printf 'Error: %s\n' "$*" >&2; exit 1; }

command -v git >/dev/null 2>&1 || fail "git is not installed."
command -v docker >/dev/null 2>&1 || fail "Docker is not installed or not on PATH."

step "Pull the latest lab version"
git pull --ff-only || fail "git pull did not complete; nothing else was changed. If you edited files in the repository, keep your edits with 'git stash' (or commit them), then run ./update.sh again."

step "Update .env (new settings added, your values kept)"
sh ./setup.sh --non-interactive </dev/null || fail "the settings check found problems (see above). Fix them, then run: docker compose up -d"

step "Pull newer images"
docker compose pull --ignore-buildable </dev/null || fail "docker compose pull failed (network or registry problem). The running lab was not changed."

step "Start the updated lab"
if grep -Eq "^[A-Za-z_][A-Za-z0-9_]*=['\"]?op://" .env 2>/dev/null; then
  command -v op >/dev/null 2>&1 || fail ".env holds 1Password references, but the 1Password CLI (op) is not installed."
  op run --env-file=.env -- docker compose up -d || fail "docker compose up failed (see above)."
  doctor="op run --env-file=.env -- ./scripts/doctor.sh --post-start"
else
  docker compose up -d </dev/null || fail "docker compose up failed (see above)."
  doctor="./scripts/doctor.sh --post-start"
fi

step "Done"
printf 'The lab is updating in the background. In 5 to 10 minutes check it with:\n  %s\n' "$doctor"
