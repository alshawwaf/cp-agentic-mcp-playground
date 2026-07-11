# HTTPS Inspection

> Demo content for the Visible RAG lab — a short, paraphrased primer, NOT official Check Point documentation.

HTTPS Inspection lets a Security Gateway decrypt, inspect, and re-encrypt TLS traffic so
that blades like Application Control, IPS, Anti-Virus, and Threat Emulation can see inside
otherwise-opaque HTTPS sessions. The gateway acts as a man-in-the-middle: for outbound
inspection it presents a certificate signed by an internal CA that clients must trust, so
you deploy that CA certificate to endpoints first. Policy is expressed in the HTTPS
Inspection rulebase, where rules decide which traffic to Inspect or Bypass — commonly
bypassing categories such as banking and healthcare for privacy and compliance. Enabling it
has a performance cost, so scope it to the traffic that needs inspection.
