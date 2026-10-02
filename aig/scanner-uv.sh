#!/bin/sh
# aig-agent: starts the AI-Infra-Guard scanners (mcp-scan, skill-scan, agent-scan, the jailbreak
# evaluation) through uv with the report language set to AIG_SCAN_LANGUAGE (default en).
#
# Why: AI-Infra-Guard v4.6.3 falls back to Chinese whenever a task carries no language, and its
# task API drops the "language" field of MCP scans (common/websocket/api.go, case "mcp_scan").
# The agent finds uv through AIG_UV_BIN (common/utils/runtime_paths.go), so the lab points that
# variable at this script. It changes only the value after --language / --lang; every other
# argument reaches uv unchanged. Mounted read-only (docker-compose.yml, service aig-agent).
set -eu

lang="${AIG_SCAN_LANGUAGE:-en}"
case "$lang" in en | zh) ;; *) lang=en ;; esac

n=$#
next_is_language=0
while [ "$n" -gt 0 ]; do
  arg=$1
  shift
  if [ "$next_is_language" = 1 ]; then
    next_is_language=0
    case "$arg" in -*) ;; *) arg=$lang ;; esac
  fi
  case "$arg" in --language | --lang) next_is_language=1 ;; esac
  set -- "$@" "$arg"
  n=$((n - 1))
done

exec /usr/local/bin/uv "$@"
