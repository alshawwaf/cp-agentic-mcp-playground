## ============== BUILD STAGE ==============
FROM node:20-alpine AS builder

# Install build tools
RUN apk add --no-cache git ca-certificates bash

# Copy MCP source and build
COPY mcp-src /opt/mcp-servers
WORKDIR /opt/mcp-servers

RUN set -eux; \
  export NODE_ENV=development npm_config_production=false; \
  npm install; \
  npx nx run-many --target=build --all --parallel=1; \
  # Pack all packages
  mkdir -p /opt/artifacts; \
  for pkg in packages/*; do \
  if [ -d "$pkg" ] && [ -f "$pkg/package.json" ]; then \
  cd "$pkg"; \
  npm pack; \
  mv *.tgz /opt/artifacts/; \
  cd ../..; \
  fi; \
  done

# Install MCP CLIs
WORKDIR /opt/mcp-doc
RUN npm init -y >/dev/null 2>&1 && \
  npm install --omit=dev /opt/artifacts/*.tgz

# ============== FINAL STAGE ==============
FROM n8nio/n8n:latest

USER root

# Copy built MCP packages from builder
COPY --from=builder /opt/mcp-doc /opt/mcp-doc
COPY --from=builder /opt/artifacts /opt/artifacts

# Create wrapper scripts for all MCP CLIs
RUN set -eux; \
  wrap() { \
  name="$1"; bin="$2"; \
  printf '%s\n' \
  '#!/bin/sh' \
  'set -eu' \
  'case "${1-}" in stdio|http|stdio:*|http:*) shift;; esac' \
  "exec /opt/mcp-doc/node_modules/.bin/${bin} \"\$@\"" \
  >"/usr/local/bin/${name}"; \
  chmod +x "/usr/local/bin/${name}"; \
  }; \
  wrap mcp-documentation documentation-mcp; \
  wrap mcp-https-inspection https-inspection-mcp; \
  wrap mcp-quantum-management quantum-management-mcp; \
  wrap mcp-management-logs management-logs-mcp; \
  wrap threat-emulation-mcp threat-emulation-mcp; \
  wrap threat-prevention-mcp threat-prevention-mcp; \
  wrap spark-management-mcp spark-management-mcp; \
  wrap reputation-service-mcp reputation-service-mcp; \
  wrap harmony-sase-mcp harmony-sase-mcp; \
  wrap quantum-gw-cli-mcp quantum-gw-cli-mcp; \
  wrap quantum-gw-connection-analysis-mcp quantum-gw-connection-analysis-mcp; \
  wrap quantum-gaia-mcp quantum-gaia-mcp; \
  wrap cpinfo-analysis-mcp cpinfo-analysis-mcp

# Fix ownership
RUN chown -R node:node /opt/mcp-doc /opt/artifacts

# Back to node user
USER node
