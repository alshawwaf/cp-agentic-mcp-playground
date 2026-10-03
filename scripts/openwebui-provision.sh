#!/usr/bin/env sh
# openwebui-provision.sh: creates the Open WebUI admin account on the first deploy, so the chat UI
# never sits in the "first person to sign up becomes admin" state. Run by the openwebui-provision
# one-shot service (curlimages/curl image). POSIX sh.
#
# Account: OPEN_WEBUI_ADMIN_EMAIL / OPEN_WEBUI_ADMIN_PASSWORD, or the lab admin (N8N_ADMIN_EMAIL /
# N8N_ADMIN_PASSWORD) when those are blank. API (Open WebUI v0.11): POST /api/v1/auths/signup
# {name,email,password}; the first account becomes admin and Open WebUI then closes sign-up itself.
# Re-deploys: sign-up answers 403 (closed) or 400 (already registered); the script then signs in
# with the same account to prove the admin is the lab's, and fails loudly if it is not.
# Secrets never appear on a command line or in the log; request and response bodies live in a
# private temporary directory that is removed on exit (no session token is left in the container).

set -eu

WEBUI_URL="${OPEN_WEBUI_URL:-http://open-webui:8080}"
ADMIN_EMAIL="${OPEN_WEBUI_ADMIN_EMAIL:-${N8N_ADMIN_EMAIL:-}}"
ADMIN_PASS="${OPEN_WEBUI_ADMIN_PASSWORD:-${N8N_ADMIN_PASSWORD:-}}"
ADMIN_NAME="${OPEN_WEBUI_ADMIN_NAME:-Lab Admin}"

log() { printf '%s\n' "$*"; }
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

if [ -z "$ADMIN_EMAIL" ] || [ -z "$ADMIN_PASS" ]; then
  fail "no admin account is configured (set N8N_ADMIN_EMAIL and N8N_ADMIN_PASSWORD, or OPEN_WEBUI_ADMIN_*, in .env; ./setup.sh does this). Until an admin exists, the first person to sign up in Open WebUI becomes its admin."
fi
case "$ADMIN_EMAIL$ADMIN_PASS" in
  *op://*) fail "the admin account settings are unresolved 1Password references. Start the lab with: op run --env-file=.env -- docker compose up -d" ;;
esac

WORK=$(mktemp -d)
chmod 700 "$WORK"
trap 'rm -rf "$WORK"' EXIT INT TERM

json_escape() {
  # JSON string body: backslash and double quote escaped (values are single-line).
  printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'
}

# post PATH BODY_FILE: prints the HTTP status; the response body goes to $WORK/response (never logged).
post() {
  curl -s -o "$WORK/response" -w '%{http_code}' -H 'Content-Type: application/json' \
    --data-binary "@$2" "${WEBUI_URL}$1" 2>/dev/null || printf '000'
}

log "Open WebUI admin provisioning (${WEBUI_URL})"

i=0
max=120
while [ "$i" -lt "$max" ]; do
  if curl -s -f -o /dev/null "${WEBUI_URL}/health" 2>/dev/null; then
    break
  fi
  i=$((i + 1))
  [ $((i % 10)) -eq 1 ] && log "Waiting for Open WebUI to start (${i}/${max}) ..."
  sleep 2
done
[ "$i" -lt "$max" ] || fail "Open WebUI did not become healthy. Check: docker compose logs open-webui"

e_email=$(json_escape "$ADMIN_EMAIL")
e_pass=$(json_escape "$ADMIN_PASS")
e_name=$(json_escape "$ADMIN_NAME")
printf '{"name":"%s","email":"%s","password":"%s"}' "$e_name" "$e_email" "$e_pass" > "$WORK/signup.json"
printf '{"email":"%s","password":"%s"}' "$e_email" "$e_pass" > "$WORK/signin.json"

tries=0
while :; do
  tries=$((tries + 1))
  status=$(post /api/v1/auths/signup "$WORK/signup.json")
  log "Sign-up request: HTTP ${status}"
  case $status in
    200)
      log "Admin account created."
      exit 0 ;;
    400 | 403)
      # 403: sign-up is closed (normal after the first deploy). 400 "already registered": the account
      # exists. Either way the lab admin must be able to sign in, or someone else holds the admin role.
      if grep -qi 'already registered' "$WORK/response" 2>/dev/null || [ "$status" = 403 ]; then
        signin=$(post /api/v1/auths/signin "$WORK/signin.json")
        if [ "$signin" = 200 ]; then
          log "Admin account already exists and signs in. Nothing to do."
          exit 0
        fi
        fail "sign-up is closed and the lab admin cannot sign in (HTTP ${signin}). Either an account was created before provisioning ran (check the users in Open WebUI: Admin Panel > Users), or the admin password was changed in Open WebUI. In that case update OPEN_WEBUI_ADMIN_PASSWORD in .env."
      fi
      fail "Open WebUI rejected the admin account (HTTP 400). Check that the email is valid and the password meets the lab rule." ;;
  esac
  [ "$tries" -lt 20 ] || fail "the admin account could not be created after 20 attempts (last HTTP ${status})."
  log "Open WebUI is not ready for sign-up yet; retrying in 3 s."
  sleep 3
done
