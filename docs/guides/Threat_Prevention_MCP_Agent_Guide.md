# Threat Prevention MCP Agent Guide

This guide details the **Threat Prevention MCP Agent** workflow, designed to manage and query Check Point Threat Prevention policies using AI and the Model Context Protocol (MCP).

## Overview

The **Threat Prevention MCP Agent** allows users to interact with their Threat Prevention settings. Users can ask about active protections, performance impacts, and policy configurations.

## Prerequisites

*   **n8n URL**: `http://<host_ip>:5678`
*   **Credentials**:
    *   **Postgres**: For chat memory.
    *   **OpenAI / Azure OpenAI / Ollama**: For the LLM.
    *   **MCP Client**: HTTP connection to the Threat Prevention MCP server.

## Step-by-Step Guide

### 1. Open the Workflow

1.  Log in to n8n.
2.  Locate and click on the **threat-prevention-mcp-agent** workflow.

### 2. Using the Chat Interface

1.  Click the **Test Workflow** button.
2.  Click the **Chat** button.
3.  Enter a query, for example:
    > "Do I have any protections with high performance impact enabled?"

### 3. Review Results

The AI agent will query the Threat Prevention policy via MCP and summarize the findings.

## Workflow Deep Dive

### 1. Input Trigger: Chat Interface
*   **Node Name**: `When chat message received`
*   **Purpose**: Captures the user's policy query.

### 2. The Brain: AI Agent
*   **Node Name**: `CP Threat Prevention AI Agent`
*   **Type**: `@n8n/n8n-nodes-langchain.agent`
*   **Purpose**: Understands the user's intent and queries the policy.
*   **Configuration**:
    *   **System Message**: Instructs the AI to list tools and then execute the threat prevention tool.

### 3. Memory Management
*   **Node Name**: `Postgres Chat Memory`
*   **Purpose**: Maintains conversation context.

### 4. The Tools: MCP Client
*   **Nodes**: `List-CP-MCP-Tools-Client` and `Execute-CP-MCP-Tools-Client`
*   **Purpose**: Interfaces with the Threat Prevention MCP server.
*   **Functionality**:
    *   **List**: Shows available tools (e.g., show-protections, show-profiles).
    *   **Execute**: Runs the selected command.

## Best Practices

*   **Impact Analysis**: Use this agent to proactively check for protections that might affect gateway performance.
*   **Profile Management**: You can query specific profiles to understand their security posture.
