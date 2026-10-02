#!/bin/sh
# Registers the lab model (lab-chat through LiteLLM) in AI-Infra-Guard, so scans started from its web UI
# can select it. One-shot job of the ai-red-team profile; it runs on the internal ai-red-team network only.
# Idempotent: an existing "lab-chat" model is left as it is. Never prints the key.
set -eu

AIG_URL="${AIG_URL:-http://aig-webserver:8088}"
MODEL_ID="${AIG_LAB_MODEL_ID:-lab-chat}"

log() { printf '%s\n' "aig-provision: $*"; }
fail() { log "ERROR: $*"; exit 1; }

[ -n "${LITELLM_MASTER_KEY:-}" ] || fail "LITELLM_MASTER_KEY is not set. Run ./setup.sh."
case "$LITELLM_MASTER_KEY" in *\"*|*\\*) fail "LITELLM_MASTER_KEY contains a quote or backslash." ;; esac

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT INT TERM
chmod 700 "$WORK"

log "waiting for AI-Infra-Guard at ${AIG_URL} ..."
i=0
until curl -s -f -o /dev/null "${AIG_URL}/api/v1/app/models"; do
  i=$((i + 1))
  [ "$i" -lt 60 ] || fail "AI-Infra-Guard did not answer within 3 minutes (docker compose logs aig-webserver)."
  sleep 3
done

if curl -s -f "${AIG_URL}/api/v1/app/models" -o "$WORK/models.json" && grep -q "\"model_id\":\"${MODEL_ID}\"" "$WORK/models.json"; then
  log "model ${MODEL_ID} is already registered."
  exit 0
fi

printf '{"model_id":"%s","model":{"model":"lab-chat","token":"%s","base_url":"http://litellm:4000/v1","note":"Lab model (LiteLLM lab-chat)","limit":1000}}' \
  "$MODEL_ID" "$LITELLM_MASTER_KEY" > "$WORK/model.json"
status=$(curl -s -o "$WORK/out.json" -w '%{http_code}' -H 'content-type: application/json' \
  --data-binary "@$WORK/model.json" "${AIG_URL}/api/v1/app/models") || status=000
if [ "$status" = 200 ] && grep -q '"status":0' "$WORK/out.json"; then
  log "registered model ${MODEL_ID} (lab-chat through LiteLLM). Select it when you start a scan."
  exit 0
fi
fail "could not register the model (HTTP ${status}). docker compose logs aig-webserver"
