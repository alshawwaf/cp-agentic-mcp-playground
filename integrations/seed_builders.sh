#!/bin/sh
# seed_builders.sh — thin wrapper for manual runs. The implementation lives in seed_builders.py
# (stdlib-only Python), which ALSO runs automatically on every deploy as the one-shot
# `builders-import` compose service (parity with n8n-import). See README.md in this directory.
#
# Env (all optional — the deploy path wires these from .env automatically):
#   ADMIN_EMAIL / ADMIN_PASSWORD  stack admin creds (Flowise account + Langflow superuser)
#   FLOWISE_API_KEY               override: authenticate Flowise with an API key instead
#   LANGFLOW_API_KEY              override: authenticate Langflow with an API key instead
#   MCP_GATEWAY_TOKEN             bearer for http://mcp-gateway:8080/mcp
#   OPENAI_API_KEY / AZURE_OPENAI_API_KEY / AZURE_OPENAI_ENDPOINT / AZURE_OPENAI_DEPLOYMENT
#   FLOWISE_URL                   default http://flowise:3020 (compose FLOWISE_PORT)
#   LANGFLOW_URL                  default http://langflow:7860
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
command -v python3 >/dev/null 2>&1 || { echo "ERROR: python3 is required." >&2; exit 1; }
exec python3 "$SCRIPT_DIR/seed_builders.py" "$@"
