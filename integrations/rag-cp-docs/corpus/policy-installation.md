# Policy Installation

> Demo content for the Visible RAG lab — a short, paraphrased primer, NOT official Check Point documentation.

Install Policy is the step that compiles the security rulebase on the Management Server and
distributes the enforced policy to the selected Security Gateways. Publishing saves your
edits as a revision, but only Install Policy makes them active on the gateways. Installation
runs as a verification-then-compile-then-transfer sequence; if verification finds an error the
install is aborted before anything reaches the gateway. Each policy package can contain Access
Control, Threat Prevention, and other policy types, and you choose which installation targets
receive it. If an install fails or misbehaves, you can revert by installing an earlier database
revision. Gateways keep the last good policy, so a failed push does not leave them unprotected.
