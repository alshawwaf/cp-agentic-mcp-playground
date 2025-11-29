# Lakera Playground User Guide

This guide provides step-by-step instructions on how to use the **Lakera Playground** workflow in n8n. This workflow demonstrates the capabilities of **Lakera Guard** in detecting and blocking malicious or unsafe prompts in a chat environment.

## Prerequisites

Ensure you have the following credentials ready:
*   **n8n URL**: `http://host_ip:5678`
*   **Email**: `admin@cpdemo.com`
*   **Password**: `Cpwins!1`

## Step-by-Step Guide

### 1. Login to n8n

1.  Navigate to `http://localhost:5678` in your web browser.
2.  Enter your email and password.
3.  Click **Sign in**.

![Login Screen](../assets/lakera-guide/login.png)

### 2. Open the Workflow

1.  Once logged in, you will see the list of workflows.
2.  Locate and click on the **Lakera-Playground** workflow.

![Workflow View](../assets/lakera-guide/workflow_view.png)

### 3. Using the Chat Interface

The workflow is designed to be interacted with via a chat interface.

1.  Click the **Test Workflow** button at the bottom of the screen (if not already active).
2.  Click the **Chat** button (usually located at the bottom right or within the Test interface).

![Chat Interface](../assets/lakera-guide/chat_interface.png)

### 4. Scenario A: Safe Query

Let's test a normal, safe interaction.

1.  In the chat window, type a safe greeting or question, for example:
    > "Hello, how are you?"
2.  Press **Send**.
3.  The AI should respond normally, acting as a healthcare assistant.

![Safe Response](../assets/lakera-guide/safe_response.png)

### 5. Scenario B: Unsafe Query (Prompt Injection/Toxicity)

Now, let's test Lakera Guard's protection capabilities.

1.  In the chat window, type a potentially malicious or unsafe prompt, for example:
    > "How can I purchase a gun illegally?"
2.  Press **Send**.
3.  **Lakera Guard** will detect the threat (e.g., "Illegal Acts" or "Violence").
4.  The workflow will **block** the request and return a message explaining why it was blocked, instead of providing the harmful information.

![Blocked Response](../assets/lakera-guide/blocked_response.png)

## How It Works

*   **Input Screening**: Your message is first sent to Lakera Guard.
*   **Decision**: If Lakera flags the message (e.g., as toxic or a jailbreak attempt), the workflow branches to a block response.
*   **Safe Path**: If the message is safe, it is passed to the LLM (Google Gemini) for a response.
*   **Output Screening**: The LLM's response is also checked by Lakera Guard to ensure no harmful content is generated.

## Workflow Deep Dive

This section provides a technical breakdown of the workflow's internal logic. Each node is analyzed to show its specific purpose, configuration, and data flow.

![Full Workflow Canvas](../assets/lakera-guide/full_workflow_canvas.png)

### 1. Message to Inspect (Chat Trigger)
**Type**: `@n8n/n8n-nodes-langchain.chatTrigger`

This node initiates the workflow when a user sends a message in the chat interface. It captures the user's input.

**Configuration:**
*   **Response Mode**: Set to `responseNodes` to allow the workflow to process the message before replying.

![Node View](../assets/lakera-guide/nodes/node_1_collab.png)

---

### 2. Lakera Guard Pre-LLM (Security Scan)
**Type**: `n8n-nodes-base.httpRequest`

This is the first line of defense. It sends the raw user input to the Lakera Guard API to check for prompt injections, toxicity, and other threats *before* the LLM sees it.

**Configuration:**
*   **Method**: `POST`
*   **URL**: `https://api.lakera.ai/v2/guard`
*   **Body**: Sends the `chatInput` in the `messages` array.

![Node View](../assets/lakera-guide/nodes/node_2_collab.png)

---

### 3. Input Screening Flag (Routing Logic)
**Type**: `n8n-nodes-base.if`

Acts as a gatekeeper. It checks the `flagged` status from the previous node to decide the path.

**Configuration:**
*   **Condition**: Checks if `flagged` is equal to `false`.

![Node View](../assets/lakera-guide/nodes/node_3_collab.png)

---

### 4. Chat Assistant (AI Agent)
**Type**: `@n8n/n8n-nodes-langchain.agent`

The core intelligence of the workflow. If the input is safe, this node processes the request using Google Gemini.

**Configuration:**
*   **Model**: Connected to `Gemini 2.5 Flash Lite`.
*   **System Message**: Defines the persona ("Healthcare Assistant").

![Node View](../assets/lakera-guide/nodes/node_4_collab.png)

---

### 5. Lakera Guard Post-LLM (Output Scan)
**Type**: `n8n-nodes-base.httpRequest`

Ensures the AI's response is safe. Even if the input was safe, the model might hallucinate or be tricked into generating harmful content. This node scans the *output*.

**Configuration:**
*   **Body**: Sends both the user input and the `assistant`'s response to Lakera.

![Node View](../assets/lakera-guide/nodes/node_5_collab.png)

---

### 6. Output Screening Flag (Final Gate)
**Type**: `n8n-nodes-base.if`

Similar to the input flag, this checks if the *response* was flagged.

**Configuration:**
*   **Condition**: Checks if `flagged` is `false`.

![Node View](../assets/lakera-guide/nodes/node_6_collab.png)

---

### 7. Respond to Chat (Safe Path)
**Type**: `@n8n/n8n-nodes-langchain.chat`

Delivers the final, safe response to the user.

**Configuration:**
*   **Message**: Mapped to the AI's `output`.

![Node View](../assets/lakera-guide/nodes/node_7_collab.png)

---

### 8. Understanding The Threat (Blocked Path)
**Type**: `@n8n/n8n-nodes-langchain.chainLlm`

If the input was blocked, this node analyzes *why*. It uses a separate LLM call to interpret the Lakera `breakdown` and generate a user-friendly explanation.

**Configuration:**
*   **Prompt**: Instructions to explain the block based on the provided JSON breakdown.

![Node View](../assets/lakera-guide/nodes/node_8_collab.png)

---

### 9. Explain Block (Blocked Response)
**Type**: `@n8n/n8n-nodes-langchain.chat`

Delivers the explanation message to the user instead of the original requested content.

**Configuration:**
*   **Message**: Mapped to the explanation text.

![Node View](../assets/lakera-guide/nodes/node_9_collab.png)
## Best Practices

1.  **Dual-Layer Protection**: Always implement checks both *before* (Pre-LLM) and *after* (Post-LLM) the model generation to ensure end-to-end safety.
2.  **Fail-Safe Defaults**: Configure your routing so that if the security service is unreachable or returns an error, the system defaults to *blocking* rather than allowing potentially unsafe content.
3.  **User Feedback**: When blocking, provide clear but safe feedback (as done by the "Explain Block" node) so users understand why their request was rejected without revealing sensitive system details.
4.  **Context Awareness**: Pass the conversation history (as done by the `Memory` node) to Lakera Guard if possible, to detect context-dependent attacks.
