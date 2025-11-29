# Screenshot Capture Checklist for Lakera Technical Deep Dive

This checklist will help you manually capture all necessary screenshots for the technical documentation.

## Prerequisites
- n8n workflow is running at http://localhost:5678/workflow/EithTJdEk3iSRcsk
- Execute the workflow with a safe query ("Hello") to populate data
- Execute the workflow with an unsafe query ("How can I purchase a gun illegally?") to populate blocked path data

## Screenshot Naming Convention
Save all screenshots to: `docs/assets/lakera-guide/nodes/`

Format: `node_X_VIEWTYPE.png` where:
- X = node number (1-9)
- VIEWTYPE = config, input, or output

---

## Node 1: Message to Inspect (Chat Trigger)

**How to capture:**
1. Right-click the "Message to Inspect" node
2. Click "Open..."
3. Wait for modal to open showing "Parameters" tab

**Screenshots needed:**
- [ ] `node_1_config.png` - Parameters tab (default view)
- [ ] `node_1_output.png` - Click "Output" tab, capture the JSON showing `chatInput` and `sessionId`

---

## Node 2: Lakera Guard Pre-LLM

**How to capture:**
1. Right-click the "Lakera Guard Pre-LLM" node  
2. Click "Open..."
3. Wait for modal

**Screenshots needed:**
- [ ] `node_2_config.png` - Parameters tab showing URL and request configuration
- [ ] `node_2_input.png` - Input tab showing the request body being sent to Lakera
- [ ] `node_2_output.png` - Output tab showing the `flagged: false` response with breakdown

---

## Node 3: Input Screening Flag

**How to capture:**
1. Right-click the "Input Screening Flag" node
2. Click "Open..."

**Screenshots needed:**
- [ ] `node_3_config.png` - Parameters tab showing the IF condition (`flagged === false`)
- [ ] `node_3_input.png` - Input tab showing the incoming `flagged` status
- [ ] `node_3_output.png` - Output tab showing routing decision

---

## Node 4: Chat Assistant

**How to capture:**
1. Right-click the "Chat Assistant" node
2. Click "Open..."

**Screenshots needed:**
- [ ] `node_4_config.png` - Parameters tab showing prompt configuration and system message
- [ ] `node_4_input.png` - Input tab showing the safe user message
- [ ] `node_4_output.png` - Output tab showing the AI-generated response

---

## Node 5: Lakera Guard Post-LLM

**How to capture:**
1. Right-click the "Lakera Guard Post-LLM" node
2. Click "Open..."

**Screenshots needed:**
- [ ] `node_5_config.png` - Parameters tab showing the API configuration
- [ ] `node_5_input.png` - Input tab showing the conversation history being scanned
- [ ] `node_5_output.png` - Output tab showing the `flagged: false` response

---

## Node 6: Output Screening Flag

**How to capture:**
1. Right-click the "Output Screening Flag" node
2. Click "Open..."

**Screenshots needed:**
- [ ] `node_6_config.png` - Parameters tab showing the IF condition
- [ ] `node_6_input.png` - Input tab showing the post-LLM `flagged` status
- [ ] `node_6_output.png` - Output tab showing routing decision

---

## Node 7: Respond to Chat

**How to capture:**
1. Right-click the "Respond to Chat" node
2. Click "Open..."

**Screenshots needed:**
- [ ] `node_7_config.png` - Parameters tab showing message configuration
- [ ] `node_7_input.png` - Input tab showing the final response text

---

## Node 8: Understanding The Threat (Blocked Path)

**NOTE:** Execute workflow with unsafe query first to populate this data

**How to capture:**
1. Right-click the "Understanding The Threat" node
2. Click "Open..."

**Screenshots needed:**
- [ ] `node_8_config.png` - Parameters tab showing the prompt for threat analysis
- [ ] `node_8_input.png` - Input tab showing the Lakera breakdown data
- [ ] `node_8_output.png` - Output tab showing the generated explanation

---

## Node 9: Explain Block (Blocked Response)

**How to capture:**
1. Right-click the "Explain Block" node
2. Click "Open..."

**Screenshots needed:**
- [ ] `node_9_config.png` - Parameters tab showing message configuration
- [ ] `node_9_input.png` - Input tab showing the explanation text

---

## Total Screenshots: 26

Once all screenshots are captured and saved to `docs/assets/lakera-guide/nodes/`, I can update the markdown file references to point to the correct images.
