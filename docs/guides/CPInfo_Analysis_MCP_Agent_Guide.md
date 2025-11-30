# CPInfo Analysis MCP Agent Guide

This guide details the **CPInfo Analysis MCP Agent** workflow, designed to analyze Check Point CPInfo files using AI and the Model Context Protocol (MCP).

## Overview

The **CPInfo Analysis MCP Agent** is an intelligent automation workflow that interacts with Check Point CPInfo files. It utilizes an LLM (Large Language Model) to interpret user requests and execute analysis tools.

## Prerequisites

*   **n8n URL**: `http://<host_ip>:5678`
*   **Credentials**:
    *   **Postgres**: For chat memory.
    *   **OpenAI / Azure OpenAI / Ollama**: For the LLM.
    *   **MCP Client**: HTTP connection to the CPInfo Analysis MCP server.

## Step-by-Step Guide

### 1. Open the Workflow

1.  Log in to n8n.
2.  Locate and click on the **cp-cpinfo-analysis-mcp-agent** workflow.

### 2. Using the Chat Interface

1.  Click the **Test Workflow** button.
2.  Click the **Chat** button.
3.  Enter a request, for example:
    > "Analyze the file SMS_3_11_2025_11_17.info under /data/cpinfo/"

### 3. Review Results

The AI agent will use the MCP tools to analyze the specified file and return a summary of the findings.

## Workflow Deep Dive

### 1. Input Trigger: Chat Interface
*   **Node Name**: `When chat message received`
*   **Purpose**: Initiates the workflow when a user sends a message.

### 2. The Brain: AI Agent
*   **Node Name**: `CP CPinfo Analysis AI Agent`
*   **Type**: `@n8n/n8n-nodes-langchain.agent`
*   **Purpose**: The central intelligence that parses user intent and orchestrates tool usage.
*   **Configuration**:
    *   **System Message**: Instructs the AI to start by calling tools, show available tools, and then use the best tool to analyze the results.

### 3. Memory Management
*   **Node Name**: `Postgres Chat Memory`
*   **Purpose**: Stores conversation history for context-aware interactions.

### 4. The Tools: MCP Client
*   **Nodes**: `List-CP-MCP-Tools` and `Execute-CP-MCP-Tools`
*   **Purpose**: Bridges the AI agent with the Check Point CPInfo Analysis tools via MCP.
*   **Functionality**:
    *   **List**: Retrieves available analysis tools.
    *   **Execute**: Runs the selected tool with parameters derived by the AI.

## Best Practices

*   **Specific Filenames**: Provide exact filenames and paths for accurate analysis.
*   **Context**: The agent has memory, so you can ask follow-up questions about the analysis results.
