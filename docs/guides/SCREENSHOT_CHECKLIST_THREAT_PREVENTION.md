# Screenshot Capture Checklist: Threat Prevention Guide

Please capture the following screenshots from your n8n workflow and save them to `docs/assets/threat-prevention/nodes/`.

## 1. AI Agent Node
- **Config**: Open the "Threat Prevention Agent" node. Capture the "Parameters" tab showing the System Message and Model connection.
  - Save as: `ai_agent_config.png`
- **Input**: (Optional) Capture the input JSON (chat message).
  - Save as: `ai_agent_input.png`
- **Output**: (Optional) Capture the output JSON (final response).
  - Save as: `ai_agent_output.png`

## 2. MCP Client Node
- **Config**: Open the "MCP Client" node. Capture the configuration showing the "Tool Name" or "Connected Tool".
  - Save as: `mcp_client_config.png`
- **Output**: Capture the output JSON showing the tool execution result (e.g., list of protections).
  - Save as: `mcp_client_output.png`

## 3. Memory Node
- **Config**: Open the "Window Buffer Memory" node. Capture the configuration showing the "Session ID" or "Window Size".
  - Save as: `memory_config.png`

## 4. Chat Trigger Node
- **Config**: Open the "When chat message received" node. Capture the configuration.
  - Save as: `chat_trigger_config.png`

## 5. Full Workflow
- **Canvas**: Capture the entire workflow canvas.
  - Save as: `full_workflow_canvas.png` (overwrite existing if needed)
