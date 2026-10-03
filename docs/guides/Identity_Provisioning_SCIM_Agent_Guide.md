# Identity Provisioning Agent (SCIM)

Most of the lab's agents work on network policy. This one works on identity, the other half of Zero Trust. The **Identity Provisioning Agent (SCIM)** creates and looks up users in an identity provider (IdP) over SCIM 2.0, from a plain-language request:

> *Create a user for Jane Doe, jane.doe@example.com*

The agent ships in n8n, Flowise and Langflow. It pairs with the [SAML and SCIM IdP Simulator](https://github.com/alshawwaf/SAML_IDP_Simulator), or any IdP that accepts SCIM 2.0 users with a Bearer token.

## What it can and cannot do

| It can | It cannot |
|---|---|
| Create a user from an email address, a first name and a last name | Assign groups or roles |
| List users, or search them with a SCIM filter such as `userName eq "jane.doe@example.com"` | Set start dates |
| Check whether a user exists before it creates one | Change or delete users |

When you ask for something it cannot do, the agent says so and suggests doing it in the IdP.

## How it works

```
chat -> Identity Provisioning Agent --> SCIM_Create_User  POST https://idp.<DOMAIN>/scim/v2/Users
                     |              --> SCIM_List_Users   GET  https://idp.<DOMAIN>/scim/v2/Users
                     +-- lab-chat fills the email, first name and last name from your request
```

`SCIM_Create_User` sends a standard SCIM 2.0 user:

```json
{
  "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
  "userName": "jane.doe@example.com",
  "name": { "givenName": "Jane", "familyName": "Doe" },
  "displayName": "Jane Doe",
  "emails": [ { "value": "jane.doe@example.com", "primary": true } ],
  "active": true
}
```

The agent uses the email exactly as you typed it. If the email, first name or last name is missing, it asks for it first. After creating the user, it confirms the `userName` and the HTTP status the IdP returned.

## Prerequisites

- The lab is running and `./scripts/doctor.sh --post-start` ends with `Result: no blockers`
- A model for `lab-chat`
- `DOMAIN`: the domain of the lab host. The agent calls `https://idp.<DOMAIN>/scim/v2/Users`. If `DOMAIN` is blank, the importers use `N8N_HOST` without its `n8n.` prefix
- `IDP_SCIM_TOKEN`: the SCIM Bearer token your IdP accepts. Give the IdP the same token
- An IdP that answers at `https://idp.<DOMAIN>/scim/v2/Users`

## Step 1: Set the token and the domain

Run `./setup.sh` (step 4 asks for the SCIM bearer token), or set `IDP_SCIM_TOKEN` and `DOMAIN` in `.env`. With `./setup.sh --1password`, `.env` holds an `op://` reference instead of the token.

Then sync the builders. `.env` is the source of truth:

```sh
docker compose run --rm n8n-import
docker compose run --rm builders-import
```

With 1Password, start each command with `op run --env-file=.env --`.

**Expected result**

- `./scripts/doctor.sh --post-start` shows `ready` on the `Identity Provisioning (SCIM)` line
- `n8n-import` publishes the n8n agent. Until both settings are set, it imports the agent but does not publish it, and its log says `not published: Identity Provisioning Agent (SCIM) (needs ...)`
- The token is in the n8n credential "SCIM IdP Token", the Flowise variable `IDP_SCIM_TOKEN` and the Langflow global variable `IDP_SCIM_TOKEN`

Change the token in `.env`, never in the builder UI: the importers overwrite it from `.env` on every run.

### No lab-host domain?

Edit the SCIM Users URL of the agent to your IdP's endpoint:

| Builder | Where |
|---|---|
| n8n | The URL of both tools, **SCIM_Create_User** and **SCIM_List_Users**. Duplicate the workflow first, so `n8n-import` never refreshes your copy |
| Flowise | The URL of **Requests Post** (`SCIM_Create_User`) and **Requests Get** (`SCIM_List_Users`) |
| Langflow | **SCIM Users URL** on the **SCIM Provisioning Tools** component |

## Step 2: Create a user

1. Open **Identity Provisioning Agent (SCIM)** in n8n (**Open chat**), Flowise, or Langflow (**Playground**)
2. Ask *Is there already a user with the email jane.doe@example.com?* The agent calls `SCIM_List_Users` with a filter
3. Ask *Create a user for Jane Doe, jane.doe@example.com*. The agent checks, then calls `SCIM_Create_User`
4. Confirm that the user appears in your IdP's admin UI

**Expected result:** the agent confirms the new `userName` and the HTTP status (`201` from an IdP that follows SCIM 2.0).

Try the guardrails:

- *Add a user* with no email: the agent asks for the email instead of inventing one
- *Add Jane to the contractors group*: the agent says it cannot assign groups

## Security notes

This agent creates identities, so treat it as a privileged entry point.

- The SCIM URL is set on the tool, not taken from the chat. A prompt cannot redirect the agent to another host
- The n8n chat page and webhook require the lab admin sign-in (HTTP Basic). Flowise and Langflow require their own sign-in
- Give the IdP a token that can create and read users only
- Keep the token in `.env` or 1Password. The repository files hold only placeholders

The lab's acceptance tests check that the agent is seeded and that n8n publishes it only when its prerequisites are set. They do not call an IdP.

## Ideas to extend it

- **Ticket-driven onboarding.** Duplicate the n8n workflow and replace the chat trigger with a webhook that receives a ticket
- **Zero Trust onboarding.** Create the identity here, then request network access with PolicyPilot: see the [Capstone](Capstone_Zero_Trust_Onboarding.md)

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| The agent is missing from the published n8n agents | `DOMAIN` or `IDP_SCIM_TOKEN` is not set | Step 1 |
| The tool error mentions `idp.{{DOMAIN}}` or a host that does not resolve | `DOMAIN` is blank and `N8N_HOST` is not `n8n.<domain>` | Set `DOMAIN`, or edit the URL (see "No lab-host domain?") |
| HTTP 401 or 403 from the IdP | The IdP does not accept the token | Set the same token in the IdP and in `IDP_SCIM_TOKEN`, then run both importers |
| HTTP 409 | The user already exists | Ask the agent to look the user up instead |
| A Langflow flow fails to build | The `IDP_SCIM_TOKEN` global variable is missing | `docker compose run --rm builders-import` |
