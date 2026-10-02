## Lab image: n8n plus the Check Point MCP servers (one wrapper per server in /usr/local/bin).
## Base images are pinned by tag and digest; bump both together.

## ============== BUILD STAGE ==============
# Runs on the build machine's own platform: it only installs JavaScript packages and packs them
# (the runtime tree has no native modules), so a multi-arch build needs emulation only for the
# small final stage.
FROM --platform=$BUILDPLATFORM node:24.21.0-alpine@sha256:ebfe2f90462722a7a4de65e91990e97fe0d401c70e0e762c5b53302f905ec1c1 AS builder

# Install build tools
RUN apk add --no-cache git ca-certificates bash

# Copy MCP source and build
COPY mcp-src /opt/mcp-servers
WORKDIR /opt/mcp-servers

# npm ci installs exactly what package-lock.json records (fails if it is out of date).
# npm 11 (bundled with Node 24) re-resolves the lock's rimraf override and rejects it, so the
# build uses npm 10.9.4, which accepts the committed lock unchanged (no dependency changes).
RUN set -eux; \
  export NODE_ENV=development npm_config_production=false; \
  npx -y npm@10.9.4 ci --no-audit --no-fund; \
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
# Every CLI comes from the tarballs packed above, policy-insights-mcp included: it is vendored
# from upstream 0.3.5 in mcp-src/packages/policy-insights and built with the patched
# @chkp/mcp-utils and @chkp/quantum-infra (see mcp-src/PATCHES.md). No @chkp package is
# installed from the npm registry.
WORKDIR /opt/mcp-doc
RUN npm init -y >/dev/null 2>&1 && \
  npm install --omit=dev --no-audit --no-fund /opt/artifacts/*.tgz

# ============== FINAL STAGE ==============
FROM n8nio/n8n:2.41.5@sha256:6f532d3b819cfe66c6a13c5375228f312c53df1abaa11a377eb8c3d3f105c363

# The image would otherwise carry only the n8n base image's labels. CI passes REVISION (the commit).
ARG REVISION=unknown
LABEL org.opencontainers.image.title="cp-agentic-n8n" \
  org.opencontainers.image.description="Check Point AI agent lab: n8n 2.41.5 with the Check Point MCP servers" \
  org.opencontainers.image.source="https://github.com/alshawwaf/cp-agentic-mcp-playground" \
  org.opencontainers.image.revision="${REVISION}" \
  org.opencontainers.image.base.name="docker.io/n8nio/n8n:2.41.5"

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
  wrap cpinfo-analysis-mcp cpinfo-analysis-mcp; \
  wrap policy-insights-mcp policy-insights-mcp

# Fix ownership
RUN chown -R node:node /opt/mcp-doc /opt/artifacts

# Back to node user
USER node
