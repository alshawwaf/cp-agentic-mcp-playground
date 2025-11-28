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
