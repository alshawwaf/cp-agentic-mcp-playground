# Screenshots for the Threat Prevention Guides (Author Note)

This is a note for guide authors, not a trainee guide.

The [Threat Prevention agents guide](Threat_Prevention_MCP_Agent_Guide.md) and the
[Threat Prevention deep dive](CheckPoint_Threat_Prevention_Guide.md) describe every step in text and
use no screenshots. The repository holds no Threat Prevention screenshots; older ones showed retired node
names and an earlier workflow layout and were removed.

If you add screenshots later:

- **Capture the current agents:** the n8n workflows **Threat Prevention Agent (Direct)** and **Threat
  Prevention Agent (MCP Gateway)**, with their current node names: `When chat message received`,
  `Normalize input`, `Threat Prevention Agent`, `OpenAI Chat Model`, `Conversation Memory`,
  `Threat Prevention MCP` (or `MCP Gateway`), and `Friendly error`
- **Prefer node settings panels** (for example the MCP Client Tool's endpoint and **Tools to Include**)
  over the full canvas. They stay readable and change less between n8n releases
- **Never show secrets:** no tokens, API keys, passwords, or `.env` content. The credentials
  **MCP Gateway Bearer**, **Lab Model (LiteLLM)**, and **Lab Agents Chat** hold secrets; show only their
  names
- **Use lab data only.** No customer hosts, policies, logs, or IP addresses
- **Store and reference them together.** Save the files under `docs/assets/threat-prevention/` and
  reference each one from the guide in the same change. Flag an image that no guide uses for removal
- **Describe the step in text as well,** so the guide still works when an image is out of date
