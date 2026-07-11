# Threat Prevention Profiles

> Demo content for the Visible RAG lab — a short, paraphrased primer, NOT official Check Point documentation.

A Threat Prevention profile is a reusable set of protections that you apply to traffic
through a Threat Prevention policy layer. One profile controls several blades at once:
IPS, Anti-Bot, Anti-Virus, Threat Emulation, and Threat Extraction. Each profile has an
activation mode — typically Prevent (block) or Detect (log only) — and a confidence and
performance-impact threshold that decides which protections are active. Check Point ships
predefined profiles such as Optimized, Basic, and Strict; Optimized is the recommended
starting point because it balances security with performance. You clone a predefined
profile to build a custom one rather than editing the defaults, then tune exceptions per
protection.
