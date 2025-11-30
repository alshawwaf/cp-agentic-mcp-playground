# Quantum Gateway CLI MCP Agent Guide

This guide details the **Quantum Gateway CLI MCP Agent** workflow, designed to execute Gateway CLI commands on Check Point devices using AI and the Model Context Protocol (MCP).

## Overview

The **Quantum Gateway CLI MCP Agent** provides a conversational interface to the Check Point Gateway CLI (Clish/Expert mode commands, depending on implementation). It simplifies the execution of complex CLI commands.

## Prerequisites

*   **n8n URL**: `http://<host_ip>:5678`
*   **Credentials**:
    *   **Postgres**: For chat memory.
    *   **OpenAI / Azure OpenAI / Ollama**: For the LLM.
    *   **MCP Client**: HTTP connection to the Gateway CLI MCP server.

## Step-by-Step Guide

### 1. Open the Workflow

1.  Log in to n8n.
2.  Locate and click on the **quantum-gw-cli-mcp-agent** workflow.

### 2. Using the Chat Interface

1.  Click the **Test Workflow** button.
2.  Click the **Chat** button.
3.  Enter a CLI request, for example:
    > "Show all IP routes on target 'GW'."

### 3. Review Results

The AI agent will run the CLI command via MCP and return the output.

## Workflow Deep Dive

### 1. Input Trigger: Chat Interface
*   **Node Name**: `When chat message received`
*   **Purpose**: Captures the user's CLI command request.

### 2. The Brain: AI Agent
*   **Node Name**: `Quantum GW CLI AI Agent`
*   **Type**: `@n8n/n8n-nodes-langchain.agent`
*   **Purpose**: Determines the correct CLI tool and parameters to use.
*   **Configuration**:
    *   **System Message**: Instructs the AI to list tools and then execute the CLI command tool.

### 3. Memory Management
*   **Node Name**: `Postgres Chat Memory`
*   **Purpose**: Stores conversation history.

### 4. The Tools: MCP Client
*   **Nodes**: `List-CP-MCP-Tools-Client` and `Execute-CP-MCP-Tools-Client`
*   **Purpose**: Connects to the Gateway CLI MCP server.
*   **Functionality**:
    *   **List**: Retrieves available CLI execution tools.
    *   **Execute**: Runs the specified CLI command.

## Best Practices

*   **Target Specification**: Clearly state which gateway (target) the command should run on.
*   **Command Safety**: Exercise caution when running commands that modify system state or network traffic.
