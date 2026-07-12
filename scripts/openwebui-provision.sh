#!/usr/bin/env sh
# This script provisions the first (admin) user in Open WebUI so the publicly
# exposed chat UI never sits in the "first signup becomes admin" state.
# Endpoint contract verified against the pinned image's upstream open-webui
# auths router: POST /api/v1/auths/signup {name,email,password} — the first user
# is auto-promoted to the admin role. Re-deploys are idempotent via the signup
# status alone: 400 EMAIL_TAKEN ("already registered") once the admin exists, or
# 403 ACCESS_PROHIBITED if signups have since been disabled — both no-ops here.
# Author: Khalid Alshawwaf

set -e

WEBUI_URL="${OPEN_WEBUI_URL:-http://open-webui:8080}"

ADMIN_EMAIL="${OPEN_WEBUI_ADMIN_EMAIL:-}"
ADMIN_PASS="${OPEN_WEBUI_ADMIN_PASSWORD:-}"
ADMIN_NAME="${OPEN_WEBUI_ADMIN_NAME:-Admin}"

echo "== openwebui-provision start =="
echo "container will call: ${WEBUI_URL}"

###############################################################################
# 0) fail soft when no creds are configured (nothing to provision)
###############################################################################
if [ -z "$ADMIN_EMAIL" ] || [ -z "$ADMIN_PASS" ]; then
  echo "OPEN_WEBUI_ADMIN_EMAIL / OPEN_WEBUI_ADMIN_PASSWORD not set — skipping admin provisioning."
  echo "WARNING: Open WebUI will hand admin to the FIRST person who signs up. Set both vars to pre-seed the admin."
  exit 0
fi

###############################################################################
# 1) wait for Open WebUI /health
###############################################################################
i=0
max=120
echo "1) wait for Open WebUI /health ..."
while [ "$i" -lt "$max" ]; do
  if curl -s -f "${WEBUI_URL}/health" >/dev/null 2>&1; then
    echo "health OK"
    break
  fi
  i=$((i+1))
  echo "Open WebUI not ready yet (${i}/${max}) ..."
  sleep 2
done
if [ "$i" -ge "$max" ]; then
  echo "Open WebUI never became healthy"
  exit 1
fi

###############################################################################
# 2) create the admin via /api/v1/auths/signup (idempotent)
###############################################################################
echo "2) /api/v1/auths/signup ..."
signup_tries=0
while :; do
  signup_tries=$((signup_tries+1))

  # Capture HTTP code and body separately, but don't log body to avoid leaking creds
  curl -s -w "\n%{http_code}" \
    -H "Content-Type: application/json" \
    -d "{\"name\":\"${ADMIN_NAME}\",\"email\":\"${ADMIN_EMAIL}\",\"password\":\"${ADMIN_PASS}\"}" \
    "${WEBUI_URL}/api/v1/auths/signup" > /tmp/signup.raw

  SIGNUP_STATUS=$(tail -n 1 /tmp/signup.raw)
  SIGNUP_BODY=$(sed '$ d' /tmp/signup.raw 2>/dev/null || true)

  echo "→ SIGNUP HTTP: ${SIGNUP_STATUS}"
  # echo "→ SIGNUP BODY: ${SIGNUP_BODY}" # HIDDEN FOR SECURITY (success body carries a session token)

  # 200 → first user created and auto-promoted to admin
  [ "$SIGNUP_STATUS" = "200" ] && { echo "admin created!!!!!"; exit 0; }

  # 400 "already registered" → the admin exists from a previous deploy — no-op
  if [ "$SIGNUP_STATUS" = "400" ] && echo "$SIGNUP_BODY" | grep -qi "already registered"; then
    echo "admin already exists — nothing to do."
    exit 0
  fi

  # 403 → the signup route is disabled (an operator set ENABLE_SIGNUP=false; it is
  # NOT disabled by default, and Open WebUI does not auto-disable it after the first
  # user). If signups were turned off, an admin was necessarily created earlier (or
  # the operator will create one another way), so provisioning has nothing to do —
  # treat as a no-op rather than failing the deploy.
  [ "$SIGNUP_STATUS" = "403" ] && { echo "signup route disabled (ENABLE_SIGNUP=false) — assuming admin already provisioned. Nothing to do."; exit 0; }

  # any other 400 is NOT idempotent-success (e.g. password policy reject) — surface it
  if [ "$SIGNUP_STATUS" = "400" ]; then
    echo "signup rejected with 400: ${SIGNUP_BODY}"
    exit 1
  fi

  [ "$signup_tries" -ge 20 ] && { echo "admin could not be created after 20 tries (last HTTP ${SIGNUP_STATUS})"; exit 1; }
  echo "signup: got ${SIGNUP_STATUS}, retry in 3s..."; sleep 3
done
