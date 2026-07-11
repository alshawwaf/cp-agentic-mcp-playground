# Identity Awareness

> Demo content for the Visible RAG lab — a short, paraphrased primer, NOT official Check Point documentation.

Identity Awareness maps network traffic to actual user and machine identities so that
access rules can be written against people and groups instead of raw IP addresses.
It gathers identities through several acquisition sources — AD Query (agentless,
reading Active Directory security-event logs), Browser-Based Authentication (a captive
portal), Identity Collector, and Identity Agents. Once a user is identified, the
gateway builds an association between the IP address and the identity, which the Access
Control policy uses in the Source column via Access Roles. Access Roles bundle
networks, users, groups, and machines into a single reusable policy object.
