# Access Control Layers

> Demo content for the Visible RAG lab — a short, paraphrased primer, NOT official Check Point documentation.

An Access Control Policy is built from ordered layers, and each layer holds a rulebase that
is evaluated top-to-bottom until a rule matches. Ordered layers run in sequence: a packet
must be accepted by every layer to pass. A rule can also delegate to an Inline Layer (a
sub-rulebase) so that matching traffic is further evaluated by nested rules, which keeps large
policies modular. Layers can be shared across multiple policy packages for reuse. Each layer
can enable specific blades — for example a layer that turns on Applications & URL Filtering or
Content Awareness. The implicit cleanup rule at the bottom drops whatever no explicit rule
allowed, so ordering and the last-match-wins behavior matter.
