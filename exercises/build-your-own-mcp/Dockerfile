# Zero-dependency MCP server image — pure Python stdlib, no pip install needed
# (so it builds anywhere, incl. behind an Artifactory-only package policy).
FROM python:3.11-slim
WORKDIR /app
# Ship the SOLUTION by default; point at scaffold/ while doing the exercise.
COPY solution/ips_cve_mcp.py /app/ips_cve_mcp.py
EXPOSE 3013
CMD ["python3", "ips_cve_mcp.py"]
