# Certificates for self-signed Check Point servers

The Check Point MCP servers always verify TLS certificates. If your Security Management Server or
Security Gateway uses a self-signed certificate, give the lab a copy of it here. `*.pem` files are
git-ignored.

Files in this folder are mounted read-only at `/certs` in the MCP server containers that use the shared
sidecar volumes (`x-mcp-sidecar` in `docker-compose.yml`): every Check Point MCP server except
`threat-emulation-mcp` and `cpinfo-analysis-mcp`. Those two set their own volumes (`./n8n/shared`) and
need no custom CA: Threat Emulation connects to the public Check Point cloud service, and CPInfo
Analysis reads local files only.

1. Save the server's certificate (replace `<host>`):

   ```bash
   openssl s_client -connect <host>:443 -servername <host> </dev/null | openssl x509 > certs/sms.pem
   ```

2. Confirm its SHA-256 fingerprint with the server's administrator:

   ```bash
   openssl x509 -in certs/sms.pem -noout -fingerprint -sha256
   ```

3. Set the path in `.env`: `MANAGEMENT_CA_CERT=/certs/sms.pem` (for a gateway's Gaia API:
   `GAIA_CA_CERT=/certs/gateway.pem`).
4. If you connect by IP address and the certificate names a host, also set
   `MANAGEMENT_TLS_SERVERNAME` (or `GAIA_TLS_SERVERNAME`) to that host name.
5. Recreate the servers: `docker compose up -d`.

The error `TLS certificate verification failed` means the file or the name does not match yet.
