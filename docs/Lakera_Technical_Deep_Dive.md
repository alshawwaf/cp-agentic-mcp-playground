# Lakera Playground: Technical Deep Dive

This document provides a comprehensive, node-by-node technical analysis of the **Lakera Playground** workflow. It is designed for users who want to understand the inner workings, configuration, and data flow of the system.

![Full Workflow Canvas](assets/lakera-guide/full_workflow_canvas.png)

## Workflow Overview

The workflow follows a linear path with two main branches based on security screening:
1.  **Safe Path**: Input -> Pre-LLM Check -> AI Agent -> Post-LLM Check -> Response.
2.  **Blocked Path**: Input -> Pre-LLM Check -> Block Logic -> Explanation -> Response.

---

## Node-by-Node Analysis

### 1. Message to Inspect (Chat Trigger)
**Type**: `@n8n/n8n-nodes-langchain.chatTrigger`

This node initiates the workflow when a user sends a message in the chat interface. It captures the user's input.

**Configuration:**
*   **Response Mode**: Set to `responseNodes` to allow the workflow to process the message before replying.

![Node View](assets/lakera-guide/nodes/node_1_collab.png)

---

### 2. Lakera Guard Pre-LLM (Security Scan)
**Type**: `n8n-nodes-base.httpRequest`

This is the first line of defense. It sends the raw user input to the Lakera Guard API to check for prompt injections, toxicity, and other threats *before* the LLM sees it.

**Configuration:**
*   **Method**: `POST`
*   **URL**: `https://api.lakera.ai/v2/guard`
*   **Body**: Sends the `chatInput` in the `messages` array.

![Node View](assets/lakera-guide/nodes/node_2_collab.png)

---

### 3. Input Screening Flag (Routing Logic)
**Type**: `n8n-nodes-base.if`

Acts as a gatekeeper. It checks the `flagged` status from the previous node to decide the path.

**Configuration:**
*   **Condition**: Checks if `flagged` is equal to `false`.

![Node View](assets/lakera-guide/nodes/node_3_collab.png)

---

### 4. Chat Assistant (AI Agent)
**Type**: `@n8n/n8n-nodes-langchain.agent`

The core intelligence of the workflow. If the input is safe, this node processes the request using Google Gemini.

**Configuration:**
*   **Model**: Connected to `Gemini 2.5 Flash Lite`.
*   **System Message**: Defines the persona ("Healthcare Assistant").

![Node View](assets/lakera-guide/nodes/node_4_collab.png)

---

### 5. Lakera Guard Post-LLM (Output Scan)
**Type**: `n8n-nodes-base.httpRequest`

Ensures the AI's response is safe. Even if the input was safe, the model might hallucinate or be tricked into generating harmful content. This node scans the *output*.

**Configuration:**
*   **Body**: Sends both the user input and the `assistant`'s response to Lakera.

![Node View](assets/lakera-guide/nodes/node_5_collab.png)

---

### 6. Output Screening Flag (Final Gate)
**Type**: `n8n-nodes-base.if`

Similar to the input flag, this checks if the *response* was flagged.

**Configuration:**
*   **Condition**: Checks if `flagged` is `false`.

![Node View](assets/lakera-guide/nodes/node_6_collab.png)

---

### 7. Respond to Chat (Safe Path)
**Type**: `@n8n/n8n-nodes-langchain.chat`

Delivers the final, safe response to the user.

**Configuration:**
*   **Message**: Mapped to the AI's `output`.

![Node View](assets/lakera-guide/nodes/node_7_collab.png)

---

### 8. Understanding The Threat (Blocked Path)
**Type**: `@n8n/n8n-nodes-langchain.chainLlm`

If the input was blocked, this node analyzes *why*. It uses a separate LLM call to interpret the Lakera `breakdown` and generate a user-friendly explanation.

**Configuration:**
*   **Prompt**: Instructions to explain the block based on the provided JSON breakdown.

![Node View](assets/lakera-guide/nodes/node_8_collab.png)

---

### 9. Explain Block (Blocked Response)
**Type**: `@n8n/n8n-nodes-langchain.chat`

Delivers the explanation message to the user instead of the original requested content.

**Configuration:**
*   **Message**: Mapped to the explanation text.

![Node View](assets/lakera-guide/nodes/node_9_collab.png)
