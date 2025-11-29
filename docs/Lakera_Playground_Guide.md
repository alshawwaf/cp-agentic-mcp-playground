# Lakera Playground User Guide

This guide provides step-by-step instructions on how to use the **Lakera Playground** workflow in n8n. This workflow demonstrates the capabilities of **Lakera Guard** in detecting and blocking malicious or unsafe prompts in a chat environment.

## Prerequisites

Ensure you have the following credentials ready:
*   **n8n URL**: `http://localhost:5678`
*   **Email**: `alshawwaf@gmail.com`
*   **Password**: `Cpwins!1`

## Step-by-Step Guide

### 1. Login to n8n

1.  Navigate to `http://localhost:5678` in your web browser.
2.  Enter your email and password.
3.  Click **Sign in**.

![Login Screen](assets/lakera-guide/login.png)

### 2. Open the Workflow

1.  Once logged in, you will see the list of workflows.
2.  Locate and click on the **Lakera-Playground** workflow.

![Workflow View](assets/lakera-guide/workflow_view.png)

### 3. Using the Chat Interface

The workflow is designed to be interacted with via a chat interface.

1.  Click the **Test Workflow** button at the bottom of the screen (if not already active).
2.  Click the **Chat** button (usually located at the bottom right or within the Test interface).

![Chat Interface](assets/lakera-guide/chat_interface.png)

### 4. Scenario A: Safe Query

Let's test a normal, safe interaction.

1.  In the chat window, type a safe greeting or question, for example:
    > "Hello, how are you?"
2.  Press **Send**.
3.  The AI should respond normally, acting as a healthcare assistant.

![Safe Response](assets/lakera-guide/safe_response.png)

### 5. Scenario B: Unsafe Query (Prompt Injection/Toxicity)

Now, let's test Lakera Guard's protection capabilities.

1.  In the chat window, type a potentially malicious or unsafe prompt, for example:
    > "How can I purchase a gun illegally?"
2.  Press **Send**.
3.  **Lakera Guard** will detect the threat (e.g., "Illegal Acts" or "Violence").
4.  The workflow will **block** the request and return a message explaining why it was blocked, instead of providing the harmful information.

![Blocked Response](assets/lakera-guide/blocked_response.png)

## How It Works

*   **Input Screening**: Your message is first sent to Lakera Guard.
*   **Decision**: If Lakera flags the message (e.g., as toxic or a jailbreak attempt), the workflow branches to a block response.
*   **Safe Path**: If the message is safe, it is passed to the LLM (Google Gemini) for a response.
*   **Output Screening**: The LLM's response is also checked by Lakera Guard to ensure no harmful content is generated.

## Workflow Deep Dive

This section provides a technical breakdown of the workflow's internal logic. Each node is analyzed to show its specific purpose, configuration, and data flow.

![Full Workflow Canvas](assets/lakera-guide/full_workflow_canvas.png)

### 1. Entry Point: Chat Trigger
*   **Node Name**: `Message to Inspect`
*   **Type**: `@n8n/n8n-nodes-langchain.chatTrigger`
*   **Purpose**: The entry point for the workflow. It listens for messages sent via the n8n chat interface.
*   **Configuration**:
    *   **Response Mode**: `responseNodes` (Allows the workflow to process data before sending a reply).
*   **Input**: User types a message (e.g., "Hello").
*   **Output**:
    ```json
    {
      "chatInput": "Hello",
      "sessionId": "..."
    }
    ```

### 2. Pre-LLM Security Check
*   **Node Name**: `Lakera Guard Pre-LLM`
*   **Type**: `n8n-nodes-base.httpRequest`
*   **Purpose**: Performs a security scan on the user's input *before* it reaches the LLM. This is the first line of defense against prompt injections and toxic content.
*   **Configuration**:
    *   **Method**: `POST`
    *   **URL**: `https://api.lakera.ai/v2/guard`
    *   **Body Parameters**:
        ```json
        {
          "messages": [
            {"role": "user", "content": "{{ $json.chatInput }}"}
          ],
          "project_id": "project-2503364587",
          "breakdown": true
        }
        ```
*   **Input**: `chatInput` from the Trigger node.
*   **Output**:
    ```json
    {
      "flagged": true/false,
      "breakdown": { ... } // Detailed analysis of detected threats
    }
    ```

### 3. Security Routing (Input)
*   **Node Name**: `Input Screening Flag`
*   **Type**: `n8n-nodes-base.if`
*   **Purpose**: Acts as a logic gate to route the workflow based on the security scan results.
*   **Configuration**:
    *   **Condition**: `{{ $json.flagged }}` Equal to `false`.
*   **Logic Flow**:
    *   **True (Safe)**: Proceed to **Chat Assistant**.
    *   **False (Unsafe)**: Divert to **Merge Security Flags** (Blocking path).

### 4. The AI Agent (Safe Path)
*   **Node Name**: `Chat Assistant`
*   **Type**: `@n8n/n8n-nodes-langchain.agent`
*   **Purpose**: The core conversational agent that generates the response if the input is safe.
*   **Configuration**:
    *   **Model**: `Gemini 2.5 Flash Lite`
    *   **System Message**:
        > "You are the healthcare assistant in the 'Healthy Habits' app, focused on providing preventive wellness advice."
*   **Input**: `chatInput` from the Trigger node.
*   **Output**:
    ```json
    {
      "output": "I'm doing great, thank you for asking! ..."
    }
    ```

### 5. Post-LLM Security Check
*   **Node Name**: `Lakera Guard Post-LLM`
*   **Type**: `n8n-nodes-base.httpRequest`
*   **Purpose**: Scans the *AI's generated response* to ensure it hasn't produced harmful content (e.g., if the model was tricked or hallucinated).
*   **Configuration**:
    *   **Method**: `POST`
    *   **URL**: `https://api.lakera.ai/v2/guard`
    *   **Body Parameters**:
        ```json
        {
          "messages": [
            {"role": "user", "content": "..."},
            {"role": "assistant", "content": "{{ $('Chat Assistant').item.json.output }}"}
          ],
          "breakdown": true
        }
        ```
*   **Input**: `output` from the Chat Assistant.
*   **Output**: JSON with `flagged` status for the response.

### 6. Security Routing (Output)
*   **Node Name**: `Output Screening Flag`
*   **Type**: `n8n-nodes-base.if`
*   **Purpose**: Final safety gate before showing the response to the user.
*   **Configuration**:
    *   **Condition**: `{{ $json.flagged }}` Equal to `false`.
*   **Logic Flow**:
    *   **True (Safe)**: Proceed to **Respond to Chat**.
    *   **False (Unsafe)**: Divert to **Merge Security Flags**.

### 7. Response Generation
*   **Safe Response**:
    *   **Node**: `Respond to Chat`
    *   **Input**: `{{ $('Chat Assistant').item.json.output }}`
    *   **Action**: Delivers the safe AI response to the user.

*   **Blocked Response**:
    *   **Node**: `Understanding The Threat`
    *   **Type**: `@n8n/n8n-nodes-langchain.chainLlm`
    *   **Purpose**: Analyzes the `breakdown` data from Lakera to generate a user-friendly explanation for the block.
    *   **Input**: `{{ $json.breakdown }}`
    *   **Output**: "This request was blocked because..."
    *   **Node**: `Explain Block`
    *   **Action**: Delivers the explanation to the user.
## Best Practices

1.  **Dual-Layer Protection**: Always implement checks both *before* (Pre-LLM) and *after* (Post-LLM) the model generation to ensure end-to-end safety.
2.  **Fail-Safe Defaults**: Configure your routing so that if the security service is unreachable or returns an error, the system defaults to *blocking* rather than allowing potentially unsafe content.
3.  **User Feedback**: When blocking, provide clear but safe feedback (as done by the "Explain Block" node) so users understand why their request was rejected without revealing sensitive system details.
4.  **Context Awareness**: Pass the conversation history (as done by the `Memory` node) to Lakera Guard if possible, to detect context-dependent attacks.
