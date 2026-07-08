# Identity Provisioning Agent (SCIM)

The network-access lessons in this lab (Quantum Management, PolicyPilot) are about
*firewall* policy. This one covers the other half of Zero Trust — **identity**: an
agent that onboards a person into a Check Point **KhalIDP Identity Provider (SAML SSO + SCIM)**
from a plain-language request.

> *"Create an account for Jane Doe, jane.doe@contractor.example."* →
> the agent extracts the details and provisions a SCIM user in the IdP.

Workflow: `identity-provisioning-scim-agent` (auto-imported).
Pairs with the [SAML + SCIM IdP Simulator](https://github.com/alshawwaf/SAML_IDP_Simulator).

## How it works

```
chat → Identity Provisioning Agent ──(ai_tool)──▶ SCIM-Create-User (HTTP tool)
                    │                                   POST {IdP}/scim/v2/Users
                    └─ LLM extracts email + first/last name from the request
```

The agent has one tool — `SCIM-Create-User` — an HTTP-request tool that POSTs a
standard **SCIM 2.0** user. The LLM fills three placeholders (`email`,
`givenName`, `familyName`) from the request; the tool sends:

```json
{
  "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
  "userName": "jane.doe@contractor.example",
  "name": { "givenName": "Jane", "familyName": "Doe" },
  "displayName": "Jane Doe",
  "emails": [ { "value": "jane.doe@contractor.example", "primary": true } ],
  "active": true
}
```

## Setup (two edits)

1. **Point it at your IdP.** Open the `SCIM-Create-User` node and set the URL to
   your simulator, e.g. `https://<your-idp-domain>/scim/v2/Users` (use the
   internal service name if the IdP runs on the same Docker network).
2. **Set the token.** In KhalIDP, enable SCIM and copy the
   **inbound bearer token** (auto-generated on first boot — shown in the SCIM
   admin UI, or in `data/.scim-bootstrap-token`). Paste it into the n8n
   credential **CP SCIM IdP Token** as `Bearer <token>` (the credential ships
   with a `CHANGE_ME` placeholder).

## Run it

1. Open the workflow → **Test Workflow → Chat**.
2. *"Onboard Marco Rossi, marco.rossi@contractor.example."*
3. The agent calls `SCIM-Create-User`; confirm the new user appears in the
   simulator's admin UI → Users, and in its provisioning audit log.

Try the guardrails: ask it to *"add a user"* with no email — it should ask for
the email rather than inventing one (per the system prompt).

## Teaching extensions

- **Ticket-driven:** swap the chat trigger for a webhook and feed a ServiceNow /
  Jira payload → an end-to-end "ticket → identity" automation.
- **Groups:** add a second HTTP tool for `POST /scim/v2/Groups` and have the
  agent place the user in a department group.
- **Full Zero Trust story:** chain this with a PolicyPilot access request — create
  the identity here, then grant its group network access there.

> **Security notes** — this agent *provisions identities*, so treat it as a
> privileged entry point:
> - The workflow ships **inactive** (`active: false`); test it via the editor's
>   **Chat** button, which is behind your n8n login. **Do not activate its public
>   chat/webhook trigger without authentication** — an open endpoint here lets
>   anyone create users. Set the chat trigger's Authentication to Basic Auth (or
>   put n8n behind your reverse proxy's auth) before going live, and scope the
>   SCIM token to least privilege.
> - Keep the SCIM inbound token in the n8n **credential store**, never in the
>   workflow JSON or git.
> - The SCIM URL is **admin-set on the node**, not taken from chat input, so the
>   agent can't be steered to POST to an arbitrary host (no SSRF via the prompt).
