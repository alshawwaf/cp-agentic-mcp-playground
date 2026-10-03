#!/bin/sh
# scripts/validate-env.sh: checks .env before the lab starts (no change is made).
#
#   ./scripts/validate-env.sh [--online] [--env-file FILE]      (ENV_FILE=FILE also works)
#
# Reports every problem at once: required settings missing, placeholders and published training
# values, the admin password rule, Langfuse and LiteLLM key formats, model provider settings,
# 1Password references, Check Point settings that are only partly set, Docker networks and resources.
# Secret values are never printed. Same as: ./scripts/doctor.sh --preflight
# Exit: 0 no blockers (warnings allowed), 1 blockers found, 2 the check could not run.
case "${1:-}" in
  -h | --help)
    sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'
    exit 0 ;;
esac
if [ -n "${ENV_FILE:-}" ]; then
  set -- --env-file "$ENV_FILE" "$@"
fi
exec sh "$(dirname "$0")/doctor.sh" --preflight "$@"
