# HTTPS Inspection MCP Agent Guide

This guide details the **HTTPS Inspection MCP Agent** workflow, designed to manage and query Check Point HTTPS Inspection rules using AI and the Model Context Protocol (MCP).

## Overview

The **HTTPS Inspection MCP Agent** enables users to interact with HTTPS Inspection settings using natural language. Users can query existing rules and potentially manage configurations through the AI interface.

## Prerequisites

*   **n8n URL**: `http://<host_ip>:5678`
*   **Credentials**:
    *   **Postgres**: For chat memory.
    *   **OpenAI / Azure OpenAI / Ollama**: For the LLM.
    *   **MCP Client**: HTTP connection to the HTTPS Inspection MCP server.

## Step-by-Step Guide

### 1. Open the Workflow

1.  Log in to n8n.
2.  Locate and click on the **https-inspection-mcp-agent** workflow.

### 2. Using the Chat Interface

1.  Click the **Test Workflow** button.
2.  Click the **Chat** button.
3.  Enter a query, for example:
    > "What are the HTTPS inspection rules I have in the 'Default Outbound Layer' and 'Standard' package?"

### 3. Review Results

The AI agent will retrieve the requested rule information via MCP and present it in the chat.

## Workflow Deep Dive

### 1. Input Trigger: Chat Interface
*   **Node Name**: `When chat message received`
*   **Purpose**: Captures the user's query about HTTPS inspection.

### 2. Data Processing
*   **Node Name**: `Edit Fields`
*   **Purpose**: Prepares the input data for the agent.

### 3. The Brain: AI Agent
*   **Node Name**: `CP-HTTPS-Inspection-MCP-AI Agent`
*   **Type**: `@n8n/n8n-nodes-langchain.agent`
*   **Purpose**: Understands the user's request and controls the MCP tools.
*   **Configuration**:
    *   **System Message**: Instructs the AI to list tools first, then execute the appropriate tool with correct parameters to answer the user's question.

### 4. Memory Management
*   **Node Name**: `Postgres Chat Memory`
*   **Purpose**: Stores conversation history.

### 5. The Tools: MCP Client
*   **Nodes**: `List-CP-HTTPS-Inspection-MCP-Tools` and `Execute-CP-HTTPS-Inspection-MCP-Tools`
*   **Purpose**: Interfaces with the HTTPS Inspection MCP server.
*   **Functionality**:
    *   **List**: Discovers available tools for HTTPS inspection management.
    *   **Execute**: Runs the selected tool (e.g., to show rules) based on the AI's decision.

## Best Practices

*   **Precise Names**: Use exact names for layers and packages to ensure the agent finds the correct rules.
*   **Verify**: Always verify the returned information against the management console if making critical decisions.
