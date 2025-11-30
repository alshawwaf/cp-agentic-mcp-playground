# Threat Emulation MCP Agent Guide

This guide details the **Threat Emulation MCP Agent** workflow, designed to analyze files and file hashes for malicious content using Check Point Threat Emulation and the Model Context Protocol (MCP).

## Overview

The **Threat Emulation MCP Agent** enables users to submit files or file hashes (SHA-256) for emulation and analysis. It helps in identifying zero-day threats and known malicious files.

## Prerequisites

*   **n8n URL**: `http://<host_ip>:5678`
*   **Credentials**:
    *   **Postgres**: For chat memory.
    *   **OpenAI / Azure OpenAI / Ollama**: For the LLM.
    *   **MCP Client**: HTTP connection to the Threat Emulation MCP server.

## Step-by-Step Guide

### 1. Open the Workflow

1.  Log in to n8n.
2.  Locate and click on the **threat-emulation-mcp-agent** workflow.

### 2. Using the Chat Interface

1.  Click the **Test Workflow** button.
2.  Click the **Chat** button.
3.  Enter a query, for example:
    > "Is this sha-256 hash malicious? 059840cd8398c039fec8cb538d76dada72c76f5392f64f7df962db3fdcb53514"
    > "Check if this file is malicious: /data/shared/npp.8.8.7.Installer.x64.exe"

### 3. Review Results

The AI agent will submit the hash or file path to the Threat Emulation service via MCP and return the analysis verdict.

## Workflow Deep Dive

### 1. Input Trigger: Chat Interface
*   **Node Name**: `When chat message received`
*   **Purpose**: Captures the user's request and supports file uploads (if configured).

### 2. The Brain: AI Agent
*   **Node Name**: `Threat Emulation AI Agent`
*   **Type**: `@n8n/n8n-nodes-langchain.agent`
*   **Purpose**: Parses the request to identify if it's a file path or a hash and calls the appropriate tool.
*   **Configuration**:
    *   **System Message**: Instructs the AI to list tools and then execute the emulation tool.

### 3. Memory Management
*   **Node Name**: `Postgres Chat Memory`
*   **Purpose**: Maintains conversation context.

### 4. The Tools: MCP Client
*   **Nodes**: `List-CP-MCP-Tools` and `CP-MCP-Client` (Execute)
*   **Purpose**: Interfaces with the Threat Emulation MCP server.
*   **Functionality**:
    *   **List**: Shows available emulation tools (e.g., query-hash, upload-file).
    *   **Execute**: Performs the emulation or query.

## Best Practices

*   **File Paths**: When checking files, ensure the path provided is accessible to the MCP server (e.g., in a shared volume).
*   **Hashes**: SHA-256 is the standard hash format for these queries.
