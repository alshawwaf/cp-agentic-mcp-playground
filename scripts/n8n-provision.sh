#!/usr/bin/env sh
# n8n-provision.sh: owner setup and seeding of the lab's n8n.
#
# Modes (POSIX sh; secret values are never printed):
#   owner    (default; service n8n-provision, curlimages/curl image)
#            Creates the n8n owner from N8N_ADMIN_EMAIL / N8N_ADMIN_PASSWORD (first deploy) and checks that the
#            owner can sign in. The owner login is the only gate in front of the n8n editor and REST API.
#            Optional: N8N_COMMUNITY_PACKAGE installs one community package (none is needed by default).
#   import   (service n8n-import, n8n image: n8n CLI + node)
#            1. Credentials: copies n8n/backup/credentials_public and fills the placeholders from the environment
#               (.env is the source of truth: every run re-syncs them, so edits made in the n8n UI are replaced).
#               Required: LITELLM_MASTER_KEY, MCP_GATEWAY_TOKEN, N8N_ADMIN_EMAIL, N8N_ADMIN_PASSWORD.
#               Optional: LAKERA_API_KEY, LAKERA_PROJECT_ID, IDP_SCIM_TOKEN, DEVHUB_MCP_TOKEN, PILOT_MCP_TOKEN,
#               QDRANT_API_KEY.
#            2. Workflows: fills {{DOMAIN}} (DOMAIN, else N8N_HOST without its "n8n." prefix, else left as is) and the
#               Lakera Guard settings. A seeded workflow that was changed in n8n since the last import is kept and
#               named in the log; N8N_SEED_OVERWRITE=1 replaces it with the repo version. New and unchanged
#               workflows are imported (updated by id).
#            3. Publishes (activates) the workflows through the n8n REST API, so their chat URLs work at once
#               without an n8n restart. A workflow whose meta.labRequires is not met (for example DOMAIN and
#               DEVHUB_MCP_TOKEN for the DevHub agent, NIGHTLY_SELF_QA=1 for the Nightly Agent Self-Check, a running vuln-mcp
#               for the MCP Security Lab agent) is imported but not published, and the log says what it needs.
#            Temporary files (with the filled-in secrets) live in a private temp directory removed on exit.
#   publish  (n8n image) Step 3 only.
#
# Re-run after changing .env:  docker compose run --rm n8n-import

set -eu

MODE="${1:-owner}"
N8N_URL="${N8N_URL:-http://n8n:5678}"

log() { printf '%s\n' "$*"; }
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

json_escape() {
  # JSON string body: escape backslashes and double quotes (values must not contain newlines).
  printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'
}

require_env() {
  missing=""
  for name in "$@"; do
    eval "value=\${$name:-}"
    [ -n "$value" ] || missing="$missing $name"
  done
  [ -z "$missing" ] || fail "set$missing in .env (./setup.sh generates them), then run this step again."
}

###############################################################################
# owner mode
###############################################################################
owner_mode() {
  require_env N8N_ADMIN_EMAIL N8N_ADMIN_PASSWORD
  WORK=$(mktemp -d)
  trap 'rm -rf "$WORK"' EXIT INT TERM
  chmod 700 "$WORK"

  # /healthz/readiness answers only after the database is connected and migrated; an owner set up
  # while migrations still run is lost (n8n 2.41 opens its port before migrations finish).
  log "n8n owner setup: waiting for n8n to finish starting (${N8N_URL}/healthz/readiness) ..."
  i=0
  until curl -s -f -o /dev/null "${N8N_URL}/healthz/readiness"; do
    i=$((i + 1))
    [ "$i" -lt 150 ] || fail "n8n did not become ready within 5 minutes (docker compose logs n8n)."
    sleep 2
  done
  log "n8n is ready."

  email=$(json_escape "$N8N_ADMIN_EMAIL")
  pass=$(json_escape "$N8N_ADMIN_PASSWORD")
  first=$(json_escape "${N8N_ADMIN_FIRST_NAME:-Lab}")
  last=$(json_escape "${N8N_ADMIN_LAST_NAME:-Admin}")
  printf '{"email":"%s","firstName":"%s","lastName":"%s","password":"%s"}' "$email" "$first" "$last" "$pass" \
    > "$WORK/owner.json"
  printf '{"emailOrLdapLoginId":"%s","password":"%s"}' "$email" "$pass" > "$WORK/login.json"

  tries=0
  while :; do
    tries=$((tries + 1))
    status=$(curl -s -o "$WORK/owner.out" -w '%{http_code}' -H 'Content-Type: application/json' \
      --data-binary "@$WORK/owner.json" "${N8N_URL}/rest/owner/setup") || status=000
    if [ "$status" = "200" ]; then
      log "Owner account created for N8N_ADMIN_EMAIL."
      break
    fi
    if grep -qi "already setup" "$WORK/owner.out" 2>/dev/null; then
      log "Owner account already exists."
      break
    fi
    if [ "$status" = "400" ] && grep -qi "password" "$WORK/owner.out" 2>/dev/null; then
      reason=$(sed -n 's/.*"message":"\([^"]*\)".*/\1/p' "$WORK/owner.out" | head -n 1)
      fail "n8n rejected N8N_ADMIN_PASSWORD (${reason:-password policy}). Use 8 to 64 characters with at least one number and one uppercase letter, then run docker compose up -d n8n-provision."
    fi
    [ "$tries" -lt 60 ] || fail "owner setup did not succeed (last HTTP status ${status}). See docker compose logs n8n."
    log "Owner setup: n8n answered HTTP ${status}, retrying in 3 seconds."
    sleep 3
  done

  tries=0
  while :; do
    tries=$((tries + 1))
    status=$(curl -s -o "$WORK/login.out" -c "$WORK/cookies" -w '%{http_code}' -H 'Content-Type: application/json' \
      --data-binary "@$WORK/login.json" "${N8N_URL}/rest/login") || status=000
    case "$status" in
      200)
        log "Owner sign-in check passed."
        break ;;
      401)
        fail "the owner cannot sign in with N8N_ADMIN_EMAIL / N8N_ADMIN_PASSWORD. n8n keeps the password it was first created with: sign in with the original values, or see docs/REFERENCE.md (Admin password)." ;;
      429)
        [ "$tries" -lt 10 ] || fail "sign-in was rate limited repeatedly."
        log "Sign-in rate limited (HTTP 429), retrying in 30 seconds."
        sleep 30 ;;
      *)
        [ "$tries" -lt 60 ] || fail "sign-in check did not succeed (last HTTP status ${status})."
        log "Sign-in check: n8n answered HTTP ${status}, retrying in 3 seconds."
        sleep 3 ;;
    esac
  done

  PKG="${N8N_COMMUNITY_PACKAGE:-}"
  if [ -z "$PKG" ]; then
    log "No community package requested (the lab workflows use n8n's built-in nodes)."
    return 0
  fi
  log "Installing community package ${PKG} ..."
  printf '{"name":"%s"}' "$(json_escape "$PKG")" > "$WORK/pkg.json"
  tries=0
  while :; do
    tries=$((tries + 1))
    status=$(curl -s -o "$WORK/pkg.out" -b "$WORK/cookies" -w '%{http_code}' -H 'Content-Type: application/json' \
      --data-binary "@$WORK/pkg.json" "${N8N_URL}/rest/community-packages") || status=000
    if [ "$status" = "200" ]; then
      log "Community package ${PKG} installed."
      return 0
    fi
    if [ "$status" = "400" ] && grep -qi "already installed" "$WORK/pkg.out" 2>/dev/null; then
      log "Community package ${PKG} is already installed."
      return 0
    fi
    [ "$status" != "401" ] || fail "n8n refused the package install (HTTP 401). Check that N8N_COMMUNITY_PACKAGES_ENABLED=true is set on the n8n service."
    [ "$tries" -lt 20 ] || fail "community package ${PKG} was not installed (last HTTP status ${status})."
    log "Package install: n8n answered HTTP ${status}, retrying in 3 seconds."
    sleep 3
  done
}

###############################################################################
# import / publish modes (n8n image)
###############################################################################
SEED_DIR="${N8N_SEED_DIR:-/backup}"
STATE_FILE="${N8N_SEED_STATE:-/home/node/.n8n/lab-seed-state.json}"

wait_for_services() {
  i=0
  until nc -z -w 3 "${DB_POSTGRESDB_HOST:-postgres}" "${DB_POSTGRESDB_PORT:-5432}"; do
    i=$((i + 1)); [ "$i" -lt 100 ] || fail "Postgres is not reachable."; sleep 3
  done
  i=0
  until nc -z -w 3 n8n 5678; do
    i=$((i + 1)); [ "$i" -lt 100 ] || fail "n8n is not reachable."; sleep 3
  done
  # The port opens before n8n has migrated its database: wait for readiness before signing in.
  i=0
  until wget -q -O /dev/null "${N8N_URL}/healthz/readiness" 2>/dev/null; do
    i=$((i + 1))
    [ "$i" -lt 150 ] || fail "n8n did not become ready within 5 minutes (docker compose logs n8n)."
    sleep 2
  done
}

prepare() {
  # $1 = work dir. Fills credentials and workflows from the environment and decides what to import.
  node - "$1" "$SEED_DIR" "$STATE_FILE" <<'JS'
const fs = require('fs');
const path = require('path');
const [work, seed, stateFile] = process.argv.slice(2);
const env = (n) => (process.env[n] || '').trim();
const say = (s) => console.log(s);
const PUBLIC_DEFAULTS = { LITELLM_MASTER_KEY: 'sk-cp-litellm-training-key', MCP_GATEWAY_TOKEN: 'cp-mcp-gateway-training-token' };
for (const [name, value] of Object.entries(PUBLIC_DEFAULTS)) {
  if (env(name) === value) say(`WARNING: ${name} is the public training default. Run ./setup.sh to generate a private value.`);
}

// 1. Credentials: every placeholder is filled from the environment on every run (.env is the source of truth).
const REQUIRED = ['LITELLM_MASTER_KEY', 'MCP_GATEWAY_TOKEN', 'N8N_ADMIN_EMAIL', 'N8N_ADMIN_PASSWORD'];
const OPTIONAL = { LAKERA_API_KEY: 'the Lakera Guard agents block every prompt with setup steps',
                   IDP_SCIM_TOKEN: 'the identity provisioning agent is not published',
                   DEVHUB_MCP_TOKEN: 'the DevHub agent is not published',
                   PILOT_MCP_TOKEN: 'the PolicyPilot agents are not published' };
const EMPTY_OK = ['QDRANT_API_KEY'];   // empty = Qdrant without API-key auth
const fill = (text, name) => text.split(`__${name}__`).join(JSON.stringify(env(name)).slice(1, -1));
fs.mkdirSync(path.join(work, 'credentials'), { recursive: true, mode: 0o700 });
let creds = 0;
for (const f of fs.readdirSync(path.join(seed, 'credentials_public')).filter((x) => x.endsWith('.json')).sort()) {
  let text = fs.readFileSync(path.join(seed, 'credentials_public', f), 'utf8');
  for (const name of REQUIRED.concat(EMPTY_OK)) text = fill(text, name);
  for (const name of Object.keys(OPTIONAL)) if (env(name)) text = fill(text, name);
  JSON.parse(text);
  fs.writeFileSync(path.join(work, 'credentials', f), text, { mode: 0o600 });
  creds += 1;
}
say(`Credentials: ${creds} prepared from .env (they replace any edits made in the n8n UI).`);
for (const [name, effect] of Object.entries(OPTIONAL)) {
  if (!env(name)) say(`  ${name} is not set: ${effect}.`);
}

// 2. Workflows: DOMAIN and Lakera Guard settings.
let domain = env('DOMAIN');
if (!domain) {
  const host = env('N8N_HOST');
  domain = host.startsWith('n8n.') && host.length > 4 ? host.slice(4) : '';
}
const lakera = env('LAKERA_API_KEY') ? 'true' : 'false';
let project = env('LAKERA_PROJECT_ID');
if (project && !/^[A-Za-z0-9_.:-]+$/.test(project)) {
  say('WARNING: LAKERA_PROJECT_ID contains unexpected characters and is ignored.');
  project = '';
}
say(domain ? `DOMAIN: ${domain}` : 'DOMAIN is not set (and N8N_HOST is not n8n.<domain>): agents that need it keep the {{DOMAIN}} placeholder and are not published.');

let state = {};
try { state = JSON.parse(fs.readFileSync(stateFile, 'utf8')).workflows || {}; } catch (e) { state = {}; }
const current = {};
const curDir = path.join(work, 'current');
if (fs.existsSync(curDir)) {
  for (const f of fs.readdirSync(curDir).filter((x) => x.endsWith('.json'))) {
    try {
      const w = JSON.parse(fs.readFileSync(path.join(curDir, f), 'utf8'));
      for (const x of (Array.isArray(w) ? w : [w])) current[x.id] = { versionId: x.versionId, name: x.name };
    } catch (e) { /* ignore unreadable exports */ }
  }
}
const overwrite = /^(1|true|yes)$/i.test(env('N8N_SEED_OVERWRITE'));
fs.mkdirSync(path.join(work, 'workflows'), { recursive: true });
fs.mkdirSync(path.join(work, 'all'), { recursive: true });
const plan = { imported: [], kept: [], workflows: [] };
for (const f of fs.readdirSync(path.join(seed, 'workflows')).filter((x) => x.endsWith('.json')).sort()) {
  let text = fs.readFileSync(path.join(seed, 'workflows', f), 'utf8');
  if (domain) text = text.split('{{DOMAIN}}').join(JSON.stringify(domain).slice(1, -1));
  text = text.split('__LAKERA_CONFIGURED__').join(lakera);
  if (project) text = text.split('__LAKERA_PROJECT_ID__').join(project);
  const wf = JSON.parse(text);
  fs.writeFileSync(path.join(work, 'all', f), text);
  const isSub = (wf.nodes || []).some((n) => n.type === 'n8n-nodes-base.executeWorkflowTrigger');
  plan.workflows.push({ id: wf.id, name: wf.name, requires: (wf.meta || {}).labRequires || '', sub: isSub,
                        domainLeft: text.includes('{{DOMAIN}}') });
  const cur = current[wf.id];
  if (cur && !overwrite && state[wf.id] && state[wf.id] !== cur.versionId) {
    plan.kept.push(wf.id);
    say(`  kept (changed in n8n since the last import): ${cur.name}. To replace it with the repo version: docker compose run --rm -e N8N_SEED_OVERWRITE=1 n8n-import`);
    continue;
  }
  fs.writeFileSync(path.join(work, 'workflows', f), text);
  plan.imported.push(wf.id);
}
fs.writeFileSync(path.join(work, 'plan.json'), JSON.stringify(plan));
say(`Workflows: ${plan.imported.length} to import, ${plan.kept.length} kept${overwrite ? ' (N8N_SEED_OVERWRITE=1)' : ''}.`);
JS
}

publish() {
  # $1 = work dir with plan.json (import mode) or empty (publish mode: plan built from the seed files).
  node - "${1:-}" "$SEED_DIR" "$STATE_FILE" "$N8N_URL" <<'JS'
const fs = require('fs');
const net = require('net');
const path = require('path');
const [work, seed, stateFile, base] = process.argv.slice(2);
const env = (n) => (process.env[n] || '').trim();
const say = (s) => console.log(s);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

let plan;
if (work && fs.existsSync(path.join(work, 'plan.json'))) {
  plan = JSON.parse(fs.readFileSync(path.join(work, 'plan.json'), 'utf8'));
} else {
  plan = { imported: [], kept: [], workflows: [] };
  for (const f of fs.readdirSync(path.join(seed, 'workflows')).filter((x) => x.endsWith('.json')).sort()) {
    const text = fs.readFileSync(path.join(seed, 'workflows', f), 'utf8');
    const wf = JSON.parse(text);
    plan.workflows.push({ id: wf.id, name: wf.name, requires: (wf.meta || {}).labRequires || '',
                          sub: (wf.nodes || []).some((n) => n.type === 'n8n-nodes-base.executeWorkflowTrigger'),
                          domainLeft: false });
    plan.imported.push(wf.id);
  }
}
let domain = env('DOMAIN');
if (!domain) {
  const host = env('N8N_HOST');
  domain = host.startsWith('n8n.') && host.length > 4 ? host.slice(4) : '';
}

function reachable(host, port) {
  return new Promise((resolve) => {
    const s = net.connect({ host, port: Number(port) });
    const done = (ok) => { s.destroy(); resolve(ok); };
    s.setTimeout(3000, () => done(false));
    s.once('connect', () => done(true));
    s.once('error', () => done(false));
  });
}
async function unmet(requires) {
  const need = [];
  for (const r of requires.split(/\s+/).filter(Boolean)) {
    if (r.startsWith('service:')) {
      const [, host, port] = r.split(':');
      if (!(await reachable(host, port))) need.push(`the ${host} service (not running)`);
    } else if (r.includes('=')) {
      const [name, want] = r.split('=');
      if (env(name) !== want) need.push(`${name}=${want}`);
    } else if (r === 'DOMAIN') {
      if (!domain) need.push('DOMAIN');
    } else if (!env(r)) {
      need.push(r);
    }
  }
  return need;
}

let cookie = '';
async function api(method, p, body) {
  const r = await fetch(base + p, { method, headers: { 'content-type': 'application/json', cookie },
                                    body: body === undefined ? undefined : JSON.stringify(body) });
  const text = await r.text();
  let json = null;
  try { json = JSON.parse(text); } catch (e) { json = null; }
  return { status: r.status, json, headers: r.headers };
}
async function login() {
  for (let i = 0; i < 60; i += 1) {
    let r;
    try {
      r = await api('POST', '/rest/login', { emailOrLdapLoginId: env('N8N_ADMIN_EMAIL'), password: env('N8N_ADMIN_PASSWORD') });
    } catch (e) { r = { status: 0 }; }
    if (r.status === 200) {
      const set = (r.headers.getSetCookie ? r.headers.getSetCookie() : [r.headers.get('set-cookie') || '']);
      cookie = set.map((c) => c.split(';')[0]).filter((c) => c.startsWith('n8n-auth=')).join('; ');
      if (cookie) return;
    }
    if (r.status === 401) throw new Error('the owner cannot sign in with N8N_ADMIN_EMAIL / N8N_ADMIN_PASSWORD');
    await sleep(3000);
  }
  throw new Error('n8n sign-in did not succeed');
}

(async () => {
  await login();
  let state = {};
  try { state = JSON.parse(fs.readFileSync(stateFile, 'utf8')).workflows || {}; } catch (e) { state = {}; }
  const order = plan.workflows.slice().sort((a, b) => Number(b.sub) - Number(a.sub));
  let published = 0; let skipped = 0; let failed = 0;
  for (const wf of order) {
    if (plan.kept.includes(wf.id)) continue;
    const got = await api('GET', `/rest/workflows/${encodeURIComponent(wf.id)}`);
    const data = got.json && (got.json.data || got.json);
    if (got.status !== 200 || !data || !data.versionId) {
      say(`  NOT FOUND ${wf.name} (HTTP ${got.status})`); failed += 1; continue;
    }
    if (plan.imported.includes(wf.id)) state[wf.id] = data.versionId;
    const need = await unmet(wf.requires);
    if (wf.domainLeft && !need.includes('DOMAIN')) need.unshift('DOMAIN');
    if (need.length) {
      if (data.active) await api('POST', `/rest/workflows/${encodeURIComponent(wf.id)}/deactivate`, {});
      say(`  not published: ${wf.name} (needs ${need.join(', ')})`); skipped += 1; continue;
    }
    const act = await api('POST', `/rest/workflows/${encodeURIComponent(wf.id)}/activate`, { versionId: data.versionId });
    if (act.status === 200) { published += 1; continue; }
    const msg = (act.json && (act.json.message || (act.json.data && act.json.data.message))) || `HTTP ${act.status}`;
    say(`  FAILED to publish ${wf.name}: ${msg}`); failed += 1;
  }
  fs.mkdirSync(path.dirname(stateFile), { recursive: true });
  fs.writeFileSync(stateFile, JSON.stringify({ note: 'Written by scripts/n8n-provision.sh: versionId of each workflow after its last import.', workflows: state }, null, 1));
  say(`Published ${published} workflows; ${skipped} not published (prerequisites); ${plan.kept.length} kept as changed in n8n; ${failed} failed.`);
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error(`ERROR: ${e.message}`); process.exit(1); });
JS
}

run_cli() {
  # n8n CLI with its output shown (minus the known Postgres 16 notice); stops the import when the CLI fails.
  what="$1"; shift
  if n8n "$@" > "$WORK/cli.log" 2>&1; then rc=0; else rc=$?; fi
  grep -v -i "postgres 16\|compatibility support" "$WORK/cli.log" || true
  [ "$rc" -eq 0 ] || fail "the n8n ${what} failed (exit ${rc})."
}

import_mode() {
  require_env LITELLM_MASTER_KEY MCP_GATEWAY_TOKEN N8N_ADMIN_EMAIL N8N_ADMIN_PASSWORD
  WORK=$(mktemp -d /tmp/n8n-seed.XXXXXX)
  trap 'rm -rf "$WORK"' EXIT INT TERM
  chmod 700 "$WORK"
  log "n8n-import: waiting for Postgres and n8n ..."
  wait_for_services
  mkdir -p "$WORK/current"
  n8n export:workflow --all --separate --output="$WORK/current/" >/dev/null 2>&1 || true
  prepare "$WORK"
  log "Importing credentials ..."
  run_cli "credential import" import:credentials --separate --input="$WORK/credentials"
  if [ -n "$(ls "$WORK/workflows" 2>/dev/null)" ]; then
    log "Importing workflows ..."
    run_cli "workflow import" import:workflow --separate --input="$WORK/workflows"
  fi
  log "Publishing workflows through the n8n REST API ..."
  publish "$WORK"
  log "n8n-import completed."
}

case "$MODE" in
  owner) owner_mode ;;
  import) import_mode ;;
  publish) require_env N8N_ADMIN_EMAIL N8N_ADMIN_PASSWORD; publish "" ;;
  *) fail "unknown mode '$MODE' (use owner, import, or publish)" ;;
esac
