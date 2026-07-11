# Gaia API

> Demo content for the Visible RAG lab — a short, paraphrased primer, NOT official Check Point documentation.

The Gaia REST API lets you configure the Gaia operating system on Check Point Security
Gateways and Management Servers programmatically, mirroring what Gaia Clish commands and
the Gaia Portal expose. You authenticate by calling the login endpoint to obtain a
session identifier (sid), then send that sid in the X-chkp-sid header on subsequent calls.
Requests and responses are JSON over HTTPS. Typical operations include reading and setting
interfaces, static routes, DNS, NTP, hostname, and DHCP. The API is versioned, and you can
discover the running version with the show-api-versions call. It is the recommended way to
automate Gaia OS configuration in Infrastructure-as-Code pipelines instead of screen-scraping
Clish.
