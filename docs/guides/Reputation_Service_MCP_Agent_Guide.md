# Reputation Service MCP Agent Guide

This guide details the **Reputation Service MCP Agent** workflow, designed to check the reputation of domains, URLs, IPs, and files using AI and the Model Context Protocol (MCP).

## Overview

The **Reputation Service MCP Agent** allows users to quickly verify if a specific indicator (like a domain name or file hash) is malicious by querying Check Point's reputation services via natural language.

## Prerequisites

*   **n8n URL**: `http://<host_ip>:5678`
*   **Credentials**:
    *   **Postgres**: For chat memory.
    *   **OpenAI / Azure OpenAI / Ollama**: For the LLM.
    *   **MCP Client**: HTTP connection to the Reputation Service MCP server.

## Step-by-Step Guide

### 1. Open the Workflow

1.  Log in to n8n.
2.  Locate and click on the **reputation-service-mcp-agent** workflow.

### 2. Using the Chat Interface

1.  Click the **Test Workflow** button.
2.  Click the **Chat** button.
3.  Enter a query, for example:
    > "Is the domain 'hotmil.com' malicious?"

### 3. Review Results

The AI agent will query the reputation service via MCP and report the classification (e.g., Malicious, Benign) and other relevant details.

## Workflow Deep Dive

### 1. Input Trigger: Chat Interface
*   **Node Name**: `When chat message received`
*   **Purpose**: Captures the user's reputation query.

### 2. The Brain: AI Agent
*   **Node Name**: `CP Reputation Service AI Agent`
*   **Type**: `@n8n/n8n-nodes-langchain.agent`
*   **Purpose**: Identifies the indicator in the user's message and calls the reputation tool.
*   **Configuration**:
    *   **System Message**: Instructs the AI to list tools and then execute the reputation check tool.

### 3. Memory Management
*   **Node Name**: `Postgres Chat Memory`
*   **Purpose**: Maintains conversation context.

### 4. The Tools: MCP Client
*   **Nodes**: `List-CP-MCP-Client` and `Execute-CP-MCP-Client`
*   **Purpose**: Interfaces with the Reputation Service MCP server.
*   **Functionality**:
    *   **List**: Shows available reputation tools.
    *   **Execute**: Runs the check for the specific indicator.

## Best Practices

*   **Clear Indicators**: Ensure the domain, IP, or hash is clearly visible in your message.
*   **Bulk Checks**: You can try asking about multiple indicators, though results depend on the specific tool's capabilities.
