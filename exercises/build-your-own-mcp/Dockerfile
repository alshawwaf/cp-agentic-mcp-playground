# Build Your Own MCP: zero-dependency server image (Python standard library only,
# no pip install, so it builds behind any package-mirror policy).
#
#   Solution (what CI publishes as ghcr.io/alshawwaf/cp-agentic-ips-cve-mcp):
#     docker build -t ips-cve-mcp:exercise exercises/build-your-own-mcp
#   Your scaffold, once the TODOs are done:
#     docker build --build-arg VARIANT=scaffold -t ips-cve-mcp:exercise exercises/build-your-own-mcp
#
# Runs as the compose service ips-cve-mcp (profile exercises): MCP_PORT and the
# IPS_* variables come from compose; this image binds 0.0.0.0 so the gateway can
# reach it on the lab network (run directly on a laptop, the script defaults to
# 127.0.0.1). python3 is on PATH for the compose healthcheck.

# Same pinned multi-arch (amd64 + arm64) base as the lab's other Python services.
FROM python:3.12.14-alpine@sha256:4c47124a8391cb7a9f571164147d154777cf012a4ece5f86097130d7a4478111

# solution (default) or scaffold
ARG VARIANT=solution

WORKDIR /app
# root-owned: readable by the runtime user, not writable by it
COPY ${VARIANT}/ips_cve_mcp.py /app/ips_cve_mcp.py

ENV MCP_HOST=0.0.0.0 \
    MCP_PORT=3013 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# nobody:nobody. The server needs no writable path and no capabilities.
USER 65534:65534
EXPOSE 3013

# CI passes REVISION (the commit).
ARG REVISION=unknown
LABEL org.opencontainers.image.title="Check Point IPS/CVE MCP server (Build Your Own MCP exercise)" \
      org.opencontainers.image.source="https://github.com/alshawwaf/cp-agentic-mcp-playground" \
      org.opencontainers.image.revision="${REVISION}"

CMD ["python3", "/app/ips_cve_mcp.py"]
