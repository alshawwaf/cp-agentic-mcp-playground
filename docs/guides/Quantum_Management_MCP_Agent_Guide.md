# Quantum Management MCP Agent Guide

This guide details the **Quantum Management MCP Agent** workflow, designed to interact with the Check Point Management API using AI and the Model Context Protocol (MCP).

## Overview

The **Quantum Management MCP Agent** serves as a general-purpose assistant for Check Point Management API operations. It can answer questions about policy, objects, and rules by querying the management server.

## Prerequisites

*   **n8n URL**: `http://<host_ip>:5678`
*   **Credentials**:
    *   **Postgres**: For chat memory.
    *   **OpenAI / Azure OpenAI / Ollama**: For the LLM.
    *   **MCP Client**: HTTP connection to the Management MCP server.

## Step-by-Step Guide

### 1. Open the Workflow

1.  Log in to n8n.
2.  Locate and click on the **quantum-management-mcp** workflow.

### 2. Using the Chat Interface

1.  Click the **Test Workflow** button.
2.  Click the **Chat** button.
3.  Enter a management query, for example:
    > "Is there a rule in the DNS_Layer to allow traffic to 8.8.8.8?"

### 3. Review Results

The AI agent will query the management server via MCP and provide the answer.

## Workflow Deep Dive

### 1. Input Trigger: Chat Interface
*   **Node Name**: `When chat message received`
*   **Purpose**: Captures the user's management API query.

### 2. The Brain: AI Agent
*   **Node Name**: `Quantum Management AI Agent`
*   **Type**: `@n8n/n8n-nodes-langchain.agent`
*   **Purpose**: Orchestrates the API calls to answer the user's question.
*   **Configuration**:
    *   **System Message**: Guides the AI to list tools and then execute the appropriate management API tool.

### 3. Memory Management
*   **Node Name**: `Postgres Chat Memory`
*   **Purpose**: Maintains conversation context.

### 4. The Tools: MCP Client
*   **Nodes**: `List-CP-MCP-Tools` and `CP-MCP-Client` (Execute)
*   **Purpose**: Interfaces with the Check Point Management API via MCP.
*   **Functionality**:
    *   **List**: Shows available API tools (e.g., show-host, show-access-rule).
    *   **Execute**: Runs the selected API command.

## Best Practices

*   **Object Names**: Using exact object names (hosts, networks, layers) improves accuracy.
*   **Complex Queries**: You can ask complex questions like "Find all hosts with IP 1.2.3.4" or "Show me the policy for the Finance layer."
