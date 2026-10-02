#!/bin/sh
# tests/rag-threshold/run.sh: offline tests of the RAG relevance threshold (RAG_MIN_SCORE 0.5) in the
# committed builder files, each in ONE throwaway container with no network, no capabilities, a read-only
# repository mount and capped CPU / memory:
#   langflow  the Docs Retriever component of integrations/langflow/rag-cp-docs.flow.json, in the Langflow
#             image docker-compose.yml pins (lfx), against a mock Ollama + Qdrant
#   n8n       n8n/backup/workflows/rag-cp-docs-retriever.json imported into a throwaway n8n (the base image
#             docker/n8n/Dockerfile pins) and run with `n8n execute` against the same kind of mock
# (The Flowise retriever's threshold is checked statically by scripts/flows/flowise_fix.py check.)
#
#   tests/rag-threshold/run.sh [langflow] [n8n]      (default: both)
# Exit status: 0 = passed, 1 = a check failed, 2 = could not run.
set -u
HERE=$(cd "$(dirname "$0")" && pwd -P) || exit 2
REPO=$(cd "$HERE/../.." && pwd -P) || exit 2

LANGFLOW_IMAGE=$(sed -n 's/^ *image: *\(langflowai\/langflow:[^ ]*@sha256:[0-9a-f]*\).*/\1/p' "$REPO/docker-compose.yml" | head -n 1)
N8N_IMAGE=$(sed -n 's/^FROM \(n8nio\/n8n:[^ ]*@sha256:[0-9a-f]*\).*/\1/p' "$REPO/docker/n8n/Dockerfile" | head -n 1)
[ -n "$LANGFLOW_IMAGE" ] || { echo "rag-threshold: no pinned langflow image in docker-compose.yml" >&2; exit 2; }
[ -n "$N8N_IMAGE" ] || { echo "rag-threshold: no pinned n8n base image in docker/n8n/Dockerfile" >&2; exit 2; }

isolated() {
  docker run --rm --network none --cap-drop ALL --security-opt no-new-privileges --pids-limit 512 \
    --tmpfs /tmp:rw,nosuid,size=512m -v "$REPO":/repo:ro -v "$HERE":/rag:ro "$@" </dev/null
}

[ $# -gt 0 ] || set -- langflow n8n
rc=0
for t in "$@"; do
  case $t in
    langflow)
      echo "== Langflow retriever ($LANGFLOW_IMAGE)"
      isolated --read-only --cpus 1 -m 1g -e HOME=/tmp -e PYTHONDONTWRITEBYTECODE=1 \
        --entrypoint python "$LANGFLOW_IMAGE" /rag/langflow_retriever_test.py /repo/integrations/langflow/rag-cp-docs.flow.json ||
        rc=1 ;;
    n8n)
      echo "== n8n retriever workflow ($N8N_IMAGE)"
      # n8n keeps its database and settings under N8N_USER_FOLDER (/tmp); the root stays read-only.
      isolated --read-only --cpus 2 -m 1536m -e HOME=/tmp --entrypoint node "$N8N_IMAGE" /rag/n8n_retriever_test.mjs /repo ||
        rc=1 ;;
    *) echo "rag-threshold: unknown test $t (langflow, n8n)" >&2; rc=2 ;;
  esac
done
exit "$rc"
