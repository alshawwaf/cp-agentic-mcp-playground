# Application Control and URL Filtering

> Demo content for the Visible RAG lab — a short, paraphrased primer, NOT official Check Point documentation.

Application Control identifies thousands of applications and widgets regardless of port or
protocol, while URL Filtering categorizes web destinations so you can allow, block, or limit
them by category. Both draw on the continuously updated Check Point Application Database and
are enabled on an Access Control layer. Rules reference Application/Site objects and URL
categories in the Services & Applications column, and you can attach a UserCheck action to
warn or ask users before granting access. Because these blades inspect Layer 7, pairing them
with HTTPS Inspection is what lets them classify encrypted web traffic. Limit objects can cap
bandwidth for categories such as streaming media.
