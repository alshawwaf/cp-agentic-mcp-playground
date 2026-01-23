FROM n8nio/n8n:latest

# ---------- Base tools ----------
USER root
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*

# ---------- Build all MCP servers from local source ----------
COPY mcp-src /opt/mcp-servers

RUN set -eux; \
  chown -R node:node /opt/mcp-servers; \
  cd /opt/mcp-servers; \
  # Install dependencies and build
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
  done; \
  chown -R node:node /opt/artifacts

# ---------- make target dir as root, then hand it to node ----------
RUN mkdir -p /opt/mcp-doc && chown -R node:node /opt/mcp-doc

# ---------- Install all MCP CLIs as node ----------
USER node
RUN set -eux; \
  cd /opt/mcp-doc; \
  npm init -y >/dev/null 2>&1; \
  # Install all packed tarballs
  npm install --omit=dev /opt/artifacts/*.tgz; \
  # quick sanity check
  /opt/mcp-doc/node_modules/.bin/documentation-mcp --help >/dev/null

# ---------- Create /usr/local/bin wrappers for ALL of them ----------
USER root
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

# ---------- back to node like the base image ----------
USER node
