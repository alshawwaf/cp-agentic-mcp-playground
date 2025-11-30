# Documentation MCP Agent Guide

This guide details the **Documentation MCP Agent** workflow, designed to query Check Point documentation using AI and the Model Context Protocol (MCP).

## Overview

The **Documentation MCP Agent** allows users to ask natural language questions about Check Point products and receive answers sourced directly from official documentation via MCP tools.

## Prerequisites

*   **n8n URL**: `http://<host_ip>:5678`
*   **Credentials**:
    *   **Postgres**: For chat memory.
    *   **OpenAI / Azure OpenAI / Ollama**: For the LLM.
    *   **MCP Client**: HTTP connection to the Documentation MCP server.

## Step-by-Step Guide

### 1. Open the Workflow

1.  Log in to n8n.
2.  Locate and click on the **documentation-mcp-agent** workflow.

### 2. Using the Chat Interface

1.  Click the **Test Workflow** button.
2.  Click the **Chat** button.
3.  Enter a question, for example:
    > "What are the minimum requirements for R81.10?"

### 3. Review Results

The AI agent will query the documentation tools and provide a summarized answer based on the retrieved information.

## Workflow Deep Dive

### 1. Input Trigger: Chat Interface
*   **Node Name**: `When chat message received`
*   **Purpose**: Captures the user's question.

### 2. Data Processing
*   **Node Name**: `Edit Fields`
*   **Purpose**: Extracts and formats the `chatInput` and `sessionId` for the agent.

### 3. The Brain: AI Agent
*   **Node Name**: `CP documentation AI Agent`
*   **Type**: `@n8n/n8n-nodes-langchain.agent`
*   **Purpose**: Orchestrates the search for information.
*   **Configuration**:
    *   **System Message**: Guides the AI to use tools to find information and summarize the results.

### 4. Memory Management
*   **Node Name**: `Postgres Chat Memory`
*   **Purpose**: Maintains conversation context.

### 5. The Tools: MCP Client
*   **Nodes**: `List-CP-MCP-Tools` and `Execute-CP-MCP-Tools`
*   **Purpose**: Connects to the Documentation MCP server.
*   **Functionality**:
    *   **List**: Shows available documentation query tools.
    *   **Execute**: Performs the actual search or retrieval of documentation content.

## Best Practices

*   **Clear Questions**: Phrasing questions clearly helps the AI find the most relevant documentation.
*   **Version Specifics**: Mentioning specific versions (e.g., "R81.20") yields more accurate results.
