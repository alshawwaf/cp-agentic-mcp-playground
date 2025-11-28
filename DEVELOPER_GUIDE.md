# Developer Guide

This guide is for developers who want to extend, customize, or debug the Check Point Agentic MCP Playground.

## Development Setup

1.  **Clone the repository**:
    ```bash
    git clone https://github.com/alshawwaf/cp-agentic-mcp-playground.git
    cd cp-agentic-mcp-playground
    ```

2.  **Initialize Environment**:
    Run the setup script to generate your `.env` file. For development, you might want to use the default "demo" credentials for ease of use.
    ```bash
    ./setup.sh
    # Choose 'No' for random passwords to use defaults (admin/change_me)
    ```

3.  **Start the Stack**:
    ```bash
    docker compose --profile cpu up -d
    ```

## Architecture Overview

The stack is composed of several Docker services connected via the `demo` network:

-   **n8n**: The core orchestrator. It uses a custom image (`custom-mcp-n8n:custom`) that includes the Check Point MCP CLI tools.
-   **MCP Sidecars**: These are lightweight containers (using the same custom n8n image) that run the MCP servers in HTTP mode. They expose ports (e.g., 3000, 3001) to the internal network.
-   **Ollama**: Provides local LLM inference.
-   **PostgreSQL**: Database for n8n.

## Customizing the n8n Image

The `n8n` service and all MCP sidecars share the same base image defined in `docker/n8n/Dockerfile`.

### Adding New MCP Tools
To add a new MCP tool to the stack:

1.  **Edit `docker/n8n/Dockerfile`**:
    -   Add the package to the `npm install` command.
    -   Create a wrapper script in the `wrap()` section.

2.  **Rebuild the Image**:
    ```bash
    docker compose build n8n
    ```

3.  **Add Service to `docker-compose.yml`**:
    -   Define a new service (e.g., `my-new-mcp`).
    -   Use the `custom-mcp-n8n:custom` image.
    -   Set the entrypoint to your new wrapper script.
    -   Expose the necessary port.

## Debugging

### Viewing Logs
To see logs for a specific service (e.g., n8n):
```bash
docker compose logs -f n8n
```

To see logs for an MCP sidecar:
```bash
docker compose logs -f mcp-documentation
```

### Shell Access
To get a shell inside the n8n container:
```bash
docker compose exec n8n sh
```

### Testing MCP Connections
From inside the n8n container, you can verify connectivity to sidecars:
```bash
# Inside n8n container
curl http://mcp-documentation:3000/
```

##  Common Tasks

### Rebuild Everything
If you changed the Dockerfile or updated dependencies:
```bash
docker compose down
docker compose build n8n
docker compose --profile cpu up -d
```

### Reset Database
To wipe all data and start fresh (WARNING: Destructive):
```bash
docker compose down -v
docker compose --profile cpu up -d
```

## Contributing
1.  Fork the repo.
2.  Create a feature branch.
3.  Submit a Pull Request.
