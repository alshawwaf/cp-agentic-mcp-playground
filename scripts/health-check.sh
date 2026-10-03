#!/bin/sh
# scripts/health-check.sh: quick health check of the running Check Point AI agent lab.
#
#   ./scripts/health-check.sh [--verbose] [--project-dir DIR] [--env-file FILE]
#
# Checks every service of the lab (running and healthy; one-shot jobs exited 0; crash loops and
# unhealthy containers fail), then the MCP gateway from inside the lab network: a request without a
# token and one with a wrong token must get 401, and MCP_GATEWAY_TOKEN (read from .env) must list
# tools. Each MCP server is also checked directly. Works with any Compose project name.
# For the full check (seeded agents, lab-chat, Langfuse) run: ./scripts/doctor.sh --post-start
# Exit: 0 all healthy, 1 a check failed, 2 the check could not run. --profile is accepted and ignored.
case "${1:-}" in
  -h | --help)
    sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
    exit 0 ;;
esac
exec sh "$(dirname "$0")/doctor.sh" --health "$@"
