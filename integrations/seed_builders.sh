#!/bin/sh
# seed_builders.sh: thin wrapper around seed_builders.py (stdlib-only Python), which also runs on every
# deploy as the one-shot compose service `builders-import` (parity with n8n-import).
#
# Run it INSIDE the lab's Docker network. The builders' Docker names (flowise, langflow) do not resolve
# on the host and the stack publishes no builder ports, so the usual manual run is:
#
#   docker compose run --rm builders-import                     # re-seed from .env
#   docker compose run --rm -e SEED_OVERWRITE=1 builders-import # also replace flows changed in the builder
#
# Use this wrapper only from a container on that network, or with FLOWISE_URL / LANGFLOW_URL pointing at
# addresses you can reach. See the docstring of seed_builders.py for the full behaviour.
#
# Env (compose wires these from .env):
#   LITELLM_MASTER_KEY            required: key of the lab model (LiteLLM) for every seeded agent
#   ADMIN_EMAIL / ADMIN_PASSWORD  stack admin (Flowise account and Langflow superuser)
#   FLOWISE_API_KEY               optional: sign in to Flowise with an API key instead
#   LANGFLOW_API_KEY              optional: sign in to Langflow with an API key instead
#   MCP_GATEWAY_TOKEN             Bearer token of http://mcp-gateway:8080/mcp
#   LAKERA_API_KEY, IDP_SCIM_TOKEN, DEVHUB_MCP_TOKEN, PILOT_MCP_TOKEN, QDRANT_API_KEY   optional
#   LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY / LANGFUSE_HOST   optional: Flowise tracing
#   DOMAIN (or N8N_HOST=n8n.<domain>)                          external agent endpoints
#   SEED_OVERWRITE=1              replace seeded flows that were changed in the builder
#   FLOWISE_URL                   default http://flowise:3020 (compose passes http://flowise:$FLOWISE_PORT)
#   LANGFLOW_URL                  default http://langflow:7860
set -eu
SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
command -v python3 >/dev/null 2>&1 || { echo "ERROR: python3 is required." >&2; exit 1; }
exec python3 "$SCRIPT_DIR/seed_builders.py" "$@"
