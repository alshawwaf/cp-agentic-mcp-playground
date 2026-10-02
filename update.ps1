# update.ps1: update the Check Point AI agent lab to the latest repository version (Windows).
#
#   1. git pull --ff-only     stops, changing nothing, if local edits would conflict
#   2. setup.sh -y            adds new settings to .env and fills new blank secrets; your values are kept
#                             (needs sh from Git for Windows or WSL; skipped with a warning otherwise)
#   3. docker compose pull    newer images
#   4. docker compose up -d   through 1Password when .env holds op:// references
#
# Agents you changed in n8n, Flowise or Langflow are kept. To replace them once with the new
# repository versions, set N8N_SEED_OVERWRITE=1 and SEED_OVERWRITE=1 in .env for one run.

$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

function Step([string]$Title) { Write-Host ""; Write-Host "== $Title" }
function Fail([string]$Message) { Write-Host "Error: $Message" -ForegroundColor Red; exit 1 }
function Require([string]$Name) {
  if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) { Fail "$Name is not installed or not on PATH." }
}

Require git
Require docker

Step "Pull the latest lab version"
git pull --ff-only
if ($LASTEXITCODE -ne 0) { Fail "git pull did not complete; nothing else was changed. Keep local edits with 'git stash' (or commit them), then run update.ps1 again." }

Step "Update .env (new settings added, your values kept)"
if (Get-Command sh -ErrorAction SilentlyContinue) {
  sh ./setup.sh --non-interactive
  if ($LASTEXITCODE -ne 0) { Fail "the settings check found problems (see above). Fix them, then run: docker compose up -d" }
} else {
  Write-Warning "sh was not found, so .env was not updated. Run './setup.sh --non-interactive' from Git Bash or WSL."
}

Step "Pull newer images"
docker compose pull --ignore-buildable
if ($LASTEXITCODE -ne 0) { Fail "docker compose pull failed (network or registry problem). The running lab was not changed." }

Step "Start the updated lab"
$usesOp = (Test-Path .env) -and (Select-String -Path .env -Pattern "^[A-Za-z_][A-Za-z0-9_]*=['""]?op://" -Quiet)
if ($usesOp) {
  Require op
  op run --env-file=.env -- docker compose up -d
  $doctor = "op run --env-file=.env -- ./scripts/doctor.sh --post-start"
} else {
  docker compose up -d
  $doctor = "./scripts/doctor.sh --post-start"
}
if ($LASTEXITCODE -ne 0) { Fail "docker compose up failed (see above)." }

Step "Done"
Write-Host "The lab is updating in the background. In 5 to 10 minutes check it (Git Bash or WSL) with:"
Write-Host "  $doctor"
