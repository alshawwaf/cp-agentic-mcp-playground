# Management Logs MCP Agent Guide

This guide details the **Management Logs MCP Agent** workflow, designed to query and analyze Check Point logs using AI and the Model Context Protocol (MCP).

## Overview

The **Management Logs MCP Agent** enables users to search through Check Point logs using natural language queries. It supports both chat-based interaction and webhook triggers.

## Prerequisites

*   **n8n URL**: `http://<host_ip>:5678`
*   **Credentials**:
    *   **Postgres**: For chat memory.
    *   **OpenAI / Azure OpenAI / Ollama**: For the LLM.
    *   **MCP Client**: HTTP connection to the Management Logs MCP server.

## Step-by-Step Guide

### 1. Open the Workflow

1.  Log in to n8n.
2.  Locate and click on the **management-logs-mcp-webhook-OpenAI** workflow.

### 2. Using the Chat Interface

1.  Click the **Test Workflow** button.
2.  Click the **Chat** button.
3.  Enter a log query, for example:
    > "blade:Anti-Bot OR blade:Anti-Virus NOT action:Accept"
    > "Check if there are any threats blocked in the last 7 days."

### 3. Review Results

The AI agent will use the MCP tools to query the logs and return a summary of the findings.

## Workflow Deep Dive

### 1. Input Triggers
*   **Chat Trigger**: `When chat message received` - For interactive use.
*   **Webhook**: `Webhook` - Allows external systems to trigger log queries via HTTP POST.

### 2. The Brain: AI Agent
*   **Node Name**: `Check Point Logs AI Agent`
*   **Type**: `@n8n/n8n-nodes-langchain.agent`
*   **Purpose**: Interprets the user's log query and executes the appropriate MCP search tools.
*   **Configuration**:
    *   **System Message**: Instructs the AI to list tools, then execute the log search tool with the correct parameters.

### 3. Memory Management
*   **Node Name**: `Postgres Chat Memory`
*   **Purpose**: Stores conversation history.

### 4. The Tools: MCP Client
*   **Nodes**: `management-logs-MCP-tools-list` and `management-logs-MCP-tools-execute`
*   **Purpose**: Connects to the Management Logs MCP server.
*   **Functionality**:
    *   **List**: Retrieves available log query tools.
    *   **Execute**: Runs the log search command.

## Best Practices

*   **Filter Syntax**: Using Check Point log filter syntax (e.g., `blade:IPS`) helps the agent construct precise queries.
*   **Time Frames**: Specify time ranges (e.g., "last 24 hours") to narrow down results.
