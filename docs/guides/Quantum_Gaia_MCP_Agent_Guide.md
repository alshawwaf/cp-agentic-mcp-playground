# Quantum Gaia MCP Agent Guide

This guide details the **Quantum Gaia MCP Agent** workflow, designed to manage and query Check Point Gaia OS settings using AI and the Model Context Protocol (MCP).

## Overview

The **Quantum Gaia MCP Agent** allows users to interact with the Gaia operating system of Check Point devices using natural language. Users can retrieve interface information, routes, and other OS-level details.

## Prerequisites

*   **n8n URL**: `http://<host_ip>:5678`
*   **Credentials**:
    *   **Postgres**: For chat memory.
    *   **OpenAI / Azure OpenAI / Ollama**: For the LLM.
    *   **MCP Client**: HTTP connection to the Gaia MCP server.

## Step-by-Step Guide

### 1. Open the Workflow

1.  Log in to n8n.
2.  Locate and click on the **quantum-gaia-mcp-agent** workflow.

### 2. Using the Chat Interface

1.  Click the **Test Workflow** button.
2.  Click the **Chat** button.
3.  Enter a command, for example:
    > "Show me all the interfaces on 10.1.1.111"

### 3. Review Results

The AI agent will execute the necessary Gaia commands via MCP and display the output.

## Workflow Deep Dive

### 1. Input Trigger: Chat Interface
*   **Node Name**: `When chat message received`
*   **Purpose**: Captures the user's Gaia-related request.

### 2. The Brain: AI Agent
*   **Node Name**: `CP Gaia AI Agent`
*   **Type**: `@n8n/n8n-nodes-langchain.agent`
*   **Purpose**: Translates natural language into Gaia MCP tool calls.
*   **Configuration**:
    *   **System Message**: Guides the AI to list tools and then execute the appropriate Gaia command.

### 3. Memory Management
*   **Node Name**: `Postgres Chat Memory`
*   **Purpose**: Maintains conversation context.

### 4. The Tools: MCP Client
*   **Nodes**: `List CP MCP Client` and `Execute CP MCP Client`
*   **Purpose**: Interfaces with the Gaia MCP server.
*   **Functionality**:
    *   **List**: Shows available Gaia management tools.
    *   **Execute**: Runs the selected tool (e.g., show interfaces, show routes).

## Best Practices

*   **Target Device**: If managing multiple devices, specify the target IP or name in your request.
*   **Read-Only vs. Write**: Be aware of whether your request is just for information (show) or configuration (set/add), and ensure the MCP server permissions align with your intent.
