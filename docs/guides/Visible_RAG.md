# Visible RAG — Chat with Check Point Docs (see every step)

A tiny, **inspectable** Retrieval-Augmented Generation demo. You watch the whole
pipeline — ingest → embed → retrieve → cite — instead of it hiding inside a black-box
"knowledge base." It reuses the stack's existing **Ollama** (embeddings) and
**Qdrant** (vector store); the corpus is a handful of short, clearly-marked *demo*
snippets about Check Point concepts.

> The corpus in `integrations/rag-cp-docs/corpus/*.md` is paraphrased teaching text,
> **not** official Check Point documentation. It exists to make the mechanics visible.

---

## 1. What RAG is (in one paragraph)

A language model only knows what was in its training data. **Retrieval-Augmented
Generation** bolts on a search step: before answering, you *retrieve* the most
relevant chunks from your own documents and hand them to the model as context, so it
answers from *your* facts and can **cite** them. The magic that makes "relevant" work
without keyword matching is the **embedding** — a model turns each chunk (and later,
each question) into a vector of numbers, and chunks whose vectors are *close* to the
question's vector are the ones you retrieve. A **vector database** (Qdrant) stores the
vectors and does the nearest-neighbour search fast.

---

## 2. The pipeline, in words

```
INGEST (one-shot, offline)                     ASK (per chat message)
──────────────────────────                     ─────────────────────────────
corpus/*.md                                     your question
   │  read each snippet                            │  "How do I enable Identity Awareness?"
   ▼                                                ▼
Ollama  /api/embeddings                         Ollama  /api/embeddings
   │  nomic-embed-text → 768-dim vector             │  same model → 768-dim vector
   ▼                                                ▼
Qdrant  PUT cp_docs points                      Qdrant  POST cp_docs/points/search
   │  {vector, payload:{text, source}}              │  top-k nearest vectors
   ▼                                                ▼
collection "cp_docs" now holds the corpus       top snippets (text + source)
                                                    │
                                                    ▼
                                                LLM (Azure OpenAI default)
                                                    │  answer using ONLY those snippets
                                                    ▼
                                                answer + "Sources: identity-awareness.md"
```

Two rules make the retrieval honest: **the same embedding model** must be used for the
corpus and the question (vectors from different models aren't comparable), and the
agent is instructed to answer **only** from what came back and to **cite the `source`**
of every snippet it used.

---

## 3. Run the ingest

The ingester is STDLIB-only Python (no pip). It reads `corpus/*.md`, embeds each via
Ollama, (re)creates the `cp_docs` Qdrant collection sized to the model, and upserts one
point per snippet. It is idempotent — re-run it any time you edit the corpus.

**In the stack** (the wiring is in `integrations/rag-cp-docs/INTEGRATION.md`):

```sh
docker compose up -d qdrant ollama-cpu
docker exec -it ollama-cpu ollama pull nomic-embed-text   # skip if in OLLAMA_MODELS
docker compose up rag-ingest                              # runs once, prints each step, exits
```

Expected output (abridged):

```
=== Visible RAG ingest ===
[ingest]  (1/8) access-layers.md
[qdrant]  collection 'cp_docs' ready (size=768, distance=Cosine).
[embed]   -> 768-dim vector
...
[qdrant]  upserted 8 point(s) into 'cp_docs'.
```

**Locally** (services port-forwarded), same script:

```sh
OLLAMA_URL=http://localhost:11434 QDRANT_URL=http://localhost:6333 \
  python3 integrations/rag-cp-docs/ingest.py
```

Sanity-check the collection directly (from inside the demo network):

```sh
curl -s http://qdrant:6333/collections/cp_docs | python3 -m json.tool
```

---

## 4. Demo script (ingest → embed → retrieve → cite)

1. **Open the chat.** In n8n, open **CP Docs — Visible RAG Agent** and click *Chat*
   (or open its public chat URL). The welcome message lists sample questions.
2. **Ask:** *"How do I enable Identity Awareness?"*
3. **Watch the retrieval.** In the execution view you can literally see the tool run:
   the `search_cp_docs` step calls the **RAG · CP Docs Retriever** sub-workflow →
   *Embed query (Ollama)* → *Search cp_docs (Qdrant)* → *Format hits + sources*. Open
   the Qdrant search node's output to see the top snippets and their `score`.
4. **Read the answer.** The model responds from the retrieved snippet(s) and ends with
   a **Sources:** line, e.g. `Sources: identity-awareness.md`.
5. **Prove it's grounded.** Ask something the corpus does NOT cover (e.g. *"What's the
   default admin password for a Quantum Spark appliance?"*). The agent should say the
   corpus doesn't contain the answer instead of guessing — that's RAG doing its job.
6. **Show the loop.** Edit a snippet in `corpus/`, re-run `rag-ingest`, ask again —
   the answer changes to match your edit. That's the whole point: the model's knowledge
   is now *your* documents.

Good follow-up questions that map cleanly to the corpus:

| Ask | Cites |
|-----|-------|
| What is a Threat Prevention profile and which one should I start with? | `threat-prevention-profiles.md` |
| How do I authenticate to the Gaia API? | `gaia-api.md` |
| What's the difference between Publish and Install Policy? | `smartconsole-basics.md`, `policy-installation.md` |
| Why pair HTTPS Inspection with Application Control? | `https-inspection.md`, `application-control-urlf.md` |

---

## 5. How it's built (files)

| Piece | File |
|-------|------|
| Ingester (embed → upsert) | `integrations/rag-cp-docs/ingest.py` |
| Demo corpus (8 snippets) | `integrations/rag-cp-docs/corpus/*.md` |
| Chat agent + `search_cp_docs` tool | `n8n/backup/workflows/rag-cp-docs-agent.json` |
| Retriever sub-workflow (the tool) | `n8n/backup/workflows/rag-cp-docs-retriever.json` |
| Compose / env wiring | `integrations/rag-cp-docs/INTEGRATION.md` |

The agent defaults to **Azure OpenAI**; Ollama, Gemini, OpenAI and Claude model nodes
are on the canvas one drag away (the embedding model stays `nomic-embed-text`
regardless of which chat model answers — RAG governs the *context*, not the *brain*).

---

## 6. Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| Ingest: `model 'nomic-embed-text' not found` | The script auto-pulls it once; if that fails, `docker exec -it ollama-cpu ollama pull nomic-embed-text`. |
| Ingest: `could not create Qdrant collection` | Qdrant isn't running/reachable — `docker compose up -d qdrant`, confirm `http://qdrant:6333`. |
| Agent answers with no citations / from general knowledge | The tool returned nothing — did `rag-ingest` run? Check `curl http://qdrant:6333/collections/cp_docs`. |
| Tool node shows no workflow | Re-select **RAG · CP Docs Retriever** in the `search_cp_docs` node (see INTEGRATION.md §4). |
| Retrieval returns odd matches | Corpus and query must use the **same** embedding model; re-run `rag-ingest` after any model change. |
