# Threat Prevention Agent Guide

This guide details the **Threat Prevention Agent** workflow, designed to autonomously manage and monitor threat prevention policies using Check Point's Management API.

![Full Workflow Canvas](../assets/threat-prevention/full_workflow_canvas.png)

## Overview

The **Threat Prevention Agent** is an intelligent automation workflow that interacts with the Check Point Management Server. It utilizes an LLM (Large Language Model) to interpret user requests and execute complex security operations, such as:

*   Retrieving threat prevention profiles.
*   Analyzing protection statuses.
*   Managing IPS protections.
*   Installing policies.

## Workflow Deep Dive

This section breaks down the workflow into its core components, explaining the purpose, configuration, and data flow of each node.

### 1. Input Trigger: Chat Interface

**Node Name**: `When chat message received`

![Chat Trigger Config](../assets/threat-prevention/nodes/chat_trigger_config.png)

*   **Type**: `@n8n/n8n-nodes-langchain.chatTrigger`
*   **Purpose**: Initiates the workflow when a user sends a message via the chat interface.
*   **Configuration**:
    *   **Mode**: `Webhook` (for external chat UIs) or `n8n Chat` (for testing).
    *   **Public**: Enabled to allow external access.

---

### 2. The Brain: AI Agent

**Node Name**: `Threat Prevention Agent`

![AI Agent Config](../assets/threat-prevention/nodes/ai_agent_config.png)

*   **Type**: `@n8n/n8n-nodes-langchain.agent`
*   **Purpose**: The central intelligence that parses user intent and orchestrates tool usage.
*   **Configuration**:
    *   **System Message**: "You are a Check Point security expert. Your goal is to assist users with Threat Prevention policy management..."
    *   **Tools**: Connected to `MCP Client`.
    *   **Memory**: Connected to `Window Buffer Memory`.
*   **Input**:
    ```json
    {
      "chatInput": "Show me all active IPS protections for Log4j"
    }
    ```
*   **Output**:
    ```json
    {
      "output": "I found 3 active protections related to Log4j..."
    }
    ```

---

### 3. The Tools: MCP Client

**Node Name**: `MCP Client`

![MCP Client Config](../assets/threat-prevention/nodes/mcp_client_config.png)

*   **Type**: `@n8n/n8n-nodes-langchain.mcpClientTool`
*   **Purpose**: Bridges the AI agent with the Check Point Management API via the Model Context Protocol.
*   **Configuration**:
    *   **Tool Name**: `threat-prevention-mcp` (or similar).
    *   **Connection**: HTTP connection to the MCP sidecar (e.g., `http://threat-prevention-mcp:3005`).
*   **Available Tools**:
    *   `show-protections`: Filters and lists IPS protections.
    *   `show-profiles`: Lists threat profiles.
    *   `install-policy`: Triggers policy installation on gateways.

---

### 4. Memory Management

**Node Name**: `Window Buffer Memory`

![Memory Config](../assets/threat-prevention/nodes/memory_config.png)

*   **Type**: `@n8n/n8n-nodes-langchain.memoryBufferWindow`
*   **Purpose**: Stores the conversation history to allow for context-aware follow-up questions.
*   **Configuration**:
    *   **Session ID**: Dynamic (from chat trigger).
    *   **Window Size**: `5` (remembers the last 5 interactions).

## How It Works

1.  **User Request**: You send a message like "Find all protections related to 'Log4j'".
2.  **Intent Analysis**: The **AI Agent** analyzes the request and determines it needs to use the `show-protections` tool with a filter for "Log4j".
3.  **Tool Execution**: The **MCP Client** executes the command against the Check Point Management Server via the MCP server.
4.  **Response Generation**: The raw data from the Management Server is returned to the AI Agent, which summarizes it into a human-readable response.
5.  **Reply**: The agent sends the formatted answer back to the chat.

## Best Practices

*   **Specific Requests**: The more specific your request, the better the agent can filter the data (e.g., instead of "show protections", try "show active protections with high severity").
*   **Verify Actions**: For critical actions like `install-policy`, always verify the changes in the Management Console or ask the agent to confirm the details before execution.
*   **Context Usage**: Leverage the memory by asking follow-up questions. If you just retrieved a list of profiles, you can say "export *that* list to JSON" without restating the whole query.
