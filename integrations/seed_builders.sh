#!/bin/sh
# seed_builders.sh — import the "CP MCP Gateway Agent" into Flowise and Langflow.
#
# Reads secrets from the environment, substitutes the __PLACEHOLDER__ tokens into
# temporary copies of the flow JSONs, and POSTs them to the two builders. Nothing is
# written back to the repo and no secret is ever echoed.
#
# Env (all optional except where a builder is targeted):
#   MCP_GATEWAY_TOKEN         bearer token for http://mcp-gateway:8080/mcp
#   OPENAI_API_KEY            OpenAI (or OpenAI-compatible) API key   [Langflow OpenAIModel]
#   AZURE_OPENAI_API_KEY      Azure OpenAI key                        [Langflow AzureOpenAIModel]
#   AZURE_OPENAI_ENDPOINT     e.g. https://res.openai.azure.com/
#   AZURE_OPENAI_DEPLOYMENT   Azure deployment name
#   FLOWISE_API_KEY           bearer for the Flowise REST API  (skip Flowise if unset)
#   LANGFLOW_API_KEY          x-api-key for the Langflow REST API (skip Langflow if unset)
#   FLOWISE_URL               default http://flowise:3001   (compose FLOWISE_PORT=3001)
#   LANGFLOW_URL              default http://langflow:7860
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
FLOWISE_URL="${FLOWISE_URL:-http://flowise:3001}"
LANGFLOW_URL="${LANGFLOW_URL:-http://langflow:7860}"
FLOWISE_JSON="$SCRIPT_DIR/flowise/cp-mcp-gateway-agent.flowdata.json"
LANGFLOW_JSON="$SCRIPT_DIR/langflow/cp-mcp-gateway-agent.flow.json"
FLOW_NAME="CP MCP Gateway Agent"

: "${MCP_GATEWAY_TOKEN:=}"
: "${OPENAI_API_KEY:=}"
: "${AZURE_OPENAI_API_KEY:=}"
: "${AZURE_OPENAI_ENDPOINT:=}"
: "${AZURE_OPENAI_DEPLOYMENT:=}"

TMPDIR_WORK=$(mktemp -d)
trap 'rm -rf "$TMPDIR_WORK"' EXIT INT TERM

command -v python3 >/dev/null 2>&1 || { echo "ERROR: python3 is required." >&2; exit 1; }
command -v curl    >/dev/null 2>&1 || { echo "ERROR: curl is required." >&2; exit 1; }

# subst <infile> <outfile> — literal replacement of the __PLACEHOLDER__ tokens.
subst() {
  IN="$1" OUT="$2" python3 - <<'PY'
import os
data = open(os.environ["IN"], encoding="utf-8").read()
repl = {
    "__MCP_GATEWAY_TOKEN__":      os.environ.get("MCP_GATEWAY_TOKEN", ""),
    "__OPENAI_API_KEY__":         os.environ.get("OPENAI_API_KEY", ""),
    "__AZURE_OPENAI_API_KEY__":   os.environ.get("AZURE_OPENAI_API_KEY", ""),
    "__AZURE_OPENAI_ENDPOINT__":  os.environ.get("AZURE_OPENAI_ENDPOINT", ""),
    "__AZURE_OPENAI_DEPLOYMENT__":os.environ.get("AZURE_OPENAI_DEPLOYMENT", ""),
}
for k, v in repl.items():
    data = data.replace(k, v)
open(os.environ["OUT"], "w", encoding="utf-8").write(data)
PY
}

# ─────────────────────────── Flowise ───────────────────────────
seed_flowise() {
  if [ -z "${FLOWISE_API_KEY:-}" ]; then
    echo "• Flowise: FLOWISE_API_KEY not set — skipping."
    return 0
  fi
  echo "• Flowise: target $FLOWISE_URL"

  # idempotency: skip if a chatflow with this name already exists
  EXISTING=$(curl -fsS -H "Authorization: Bearer $FLOWISE_API_KEY" \
      "$FLOWISE_URL/api/v1/chatflows" 2>/dev/null || echo "[]")
  if FW_NAME="$FLOW_NAME" FW_LIST="$EXISTING" python3 - <<'PY'
import os, json, sys
try:
    rows = json.loads(os.environ["FW_LIST"])
except Exception:
    rows = []
name = os.environ["FW_NAME"]
sys.exit(0 if any(r.get("name") == name for r in rows) else 1)
PY
  then
    echo "  ↳ already present (name: $FLOW_NAME) — skipping."
    return 0
  fi

  subst "$FLOWISE_JSON" "$TMPDIR_WORK/flowise.graph.json"
  # Build the POST body: flowData must be a STRINGIFIED graph.
  IN="$TMPDIR_WORK/flowise.graph.json" NAME="$FLOW_NAME" \
    python3 - > "$TMPDIR_WORK/flowise.body.json" <<'PY'
import os, json
graph = json.load(open(os.environ["IN"], encoding="utf-8"))
body = {"name": os.environ["NAME"], "type": "CHATFLOW",
        "deployed": True, "flowData": json.dumps(graph)}
print(json.dumps(body))
PY

  HTTP=$(curl -fsS -o "$TMPDIR_WORK/flowise.resp" -w '%{http_code}' \
      -X POST "$FLOWISE_URL/api/v1/chatflows" \
      -H "Authorization: Bearer $FLOWISE_API_KEY" \
      -H "Content-Type: application/json" \
      --data-binary "@$TMPDIR_WORK/flowise.body.json") || {
        echo "  ↳ POST failed (see error above)."; return 1; }
  echo "  ↳ created chatflow (HTTP $HTTP)."
}

# ─────────────────────────── Langflow ──────────────────────────
seed_langflow() {
  if [ -z "${LANGFLOW_API_KEY:-}" ]; then
    echo "• Langflow: LANGFLOW_API_KEY not set — skipping."
    return 0
  fi
  echo "• Langflow: target $LANGFLOW_URL"

  EXISTING=$(curl -fsS -H "x-api-key: $LANGFLOW_API_KEY" \
      "$LANGFLOW_URL/api/v1/flows/?get_all=true&header_flows=true" 2>/dev/null || echo "[]")
  if LF_NAME="$FLOW_NAME" LF_LIST="$EXISTING" python3 - <<'PY'
import os, json, sys
try:
    data = json.loads(os.environ["LF_LIST"])
    rows = data if isinstance(data, list) else data.get("flows", [])
except Exception:
    rows = []
name = os.environ["LF_NAME"]
sys.exit(0 if any(isinstance(r, dict) and r.get("name") == name for r in rows) else 1)
PY
  then
    echo "  ↳ already present (name: $FLOW_NAME) — skipping."
    return 0
  fi

  subst "$LANGFLOW_JSON" "$TMPDIR_WORK/langflow.flow.json"
  HTTP=$(curl -fsS -o "$TMPDIR_WORK/langflow.resp" -w '%{http_code}' \
      -X POST "$LANGFLOW_URL/api/v1/flows/" \
      -H "x-api-key: $LANGFLOW_API_KEY" \
      -H "Content-Type: application/json" \
      --data-binary "@$TMPDIR_WORK/langflow.flow.json") || {
        echo "  ↳ POST failed (see error above)."; return 1; }
  echo "  ↳ imported flow (HTTP $HTTP)."
}

echo "Seeding the CP MCP Gateway Agent into the org's agent builders…"
[ -n "$MCP_GATEWAY_TOKEN" ] || echo "  (warning: MCP_GATEWAY_TOKEN is empty — the agent won't reach the gateway)"
seed_flowise
seed_langflow
echo "Done. Open each builder and Save once to fully hydrate the MCP / model node templates."
