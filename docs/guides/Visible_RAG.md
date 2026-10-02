# Visible RAG: Answer from Documents and Cite Them

Retrieval-augmented generation (RAG) lets an agent answer from your documents instead of from memory. This lab makes every step visible: ingest, embed, retrieve, cite. You see the chunks the agent found, their scores, and the file each answer came from.

The **Documentation RAG Agent** ships in n8n, Flowise and Langflow. It searches a small corpus of eight Check Point concept notes in `integrations/rag-cp-docs/corpus/`. The notes are training content written for this lab, not official Check Point documentation.

## How RAG works, in one paragraph

A model knows only its training data. RAG adds a search step. Before the model answers, the agent turns the question into a vector (a list of numbers) with an embedding model, and asks a vector database for the stored chunks whose vectors are closest. The model then answers from those chunks and names their sources. Two rules keep it honest: the corpus and the question must use the same embedding model, and the agent must answer only from what the search returned.

## The pipeline

```
INGEST (rag-ingest, one-shot)                ASK (every chat turn)
corpus/*.md                                  your question
   |  read each note                            |
   v                                            v
Ollama nomic-embed-text -> 768-dim vector    Ollama nomic-embed-text -> 768-dim vector
   |                                            |
   v                                            v
Qdrant collection cp_docs                    Qdrant search: top 4, score at least 0.5
   (vector + text, source, title)               |
                                                v
                                             lab-chat answers from those snippets
                                             and cites each source file
```

Embeddings run on the local Ollama model `nomic-embed-text`, so retrieval needs no API key. Only the answer uses `lab-chat`.

## Prerequisites

- The Standard lab (RAG is part of it). `./scripts/doctor.sh --post-start` shows `ready` on the `RAG (Documentation RAG Agent)` line
- A model for `lab-chat`
- Optional: `QDRANT_API_KEY`. Blank means Qdrant runs without authentication on the internal lab network. Set it to turn authentication on; every lab client then sends it

## Step 1: Ingest the corpus

`rag-ingest` runs on every `docker compose up -d`. It embeds each note and writes it to the Qdrant collection `cp_docs`. To run it by hand:

```sh
docker compose up rag-ingest
```

**Expected result**

```
=== Visible RAG ingest ===
[ingest]  (1/8) access-layers.md
[embed]   -> 768-dim vector
...
[qdrant]  upserted 8 point(s) into 'cp_docs' ...
=== done — ask the RAG agent a question and watch it cite these sources. ===
```

When nothing changed since the last run, it prints `already matches the corpus` and exits without calling Ollama. To re-embed anyway:

```sh
docker compose run --rm -e FORCE=1 rag-ingest
```

The ingest is safe to repeat. It embeds every note before it writes anything. If Ollama fails half-way, the existing collection stays as it was.

## Step 2: Ask the agent

1. Open **Documentation RAG Agent** in n8n (**Open chat**), Flowise or Langflow (**Playground**)
2. Ask *How do I enable Identity Awareness?*
3. The answer ends with a **Sources:** line, for example `Sources: identity-awareness.md`

In n8n, open the execution to watch the retrieval. The `search_cp_docs` tool runs the sub-workflow **Documentation RAG Retriever**: **Embed query (Ollama)**, then **Search cp_docs (Qdrant)**, then **Format hits + sources**. The search node's output shows each snippet with its `score`.

More questions that map to the corpus:

| Ask | Cites |
|---|---|
| What is a Threat Prevention profile, and which one should I start with? | `threat-prevention-profiles.md` |
| How do I authenticate to the Gaia API? | `gaia-api.md` |
| What is the difference between Publish and Install Policy? | `smartconsole-basics.md`, `policy-installation.md` |
| Why pair HTTPS Inspection with Application Control? | `https-inspection.md`, `application-control-urlf.md` |

## Step 3: Prove it is grounded

Ask a question the corpus does not cover, for example *What is the capital of France?* No snippet passes the relevance threshold, so the agent says the indexed documentation does not cover it. It does not answer from memory.

Then edit a note in `integrations/rag-cp-docs/corpus/`, run `docker compose up rag-ingest`, and ask again. The answer follows your edit.

## The relevance threshold

A vector search always returns its nearest chunks, even for an unrelated question. So each retriever drops hits that score below **0.5** (cosine similarity, 0 to 1).

| Builder | Where the threshold lives |
|---|---|
| n8n | `score_threshold` in the body of **Search cp_docs (Qdrant)** in **Documentation RAG Retriever** |
| Flowise | **Minimum Similarity Score (%)** = 50 on **Similarity Score Threshold Retriever** (Max K 4) |
| Langflow | **Minimum Score** = 0.5 on **Check Point Docs Retriever** |

When nothing passes, the n8n retriever says so in its tool result and names the fix. The agents' prompts tell them to say the corpus does not cover the question.

### Calibrate it for your questions

0.5 is a permissive default. It has not been tuned for your corpus. Measure real scores with `--search`, which embeds one question and lists the top hits with `keep` or `drop`:

```sh
docker compose run --rm rag-ingest python3 ingest.py --search "How do I enable Identity Awareness?"
docker compose run --rm rag-ingest python3 ingest.py --search "What is the capital of France?"
```

**Expected result:** lines like `0.xxx  keep  Identity Awareness (identity-awareness.md)`, then `N of 4 hit(s) pass the threshold.`

Pick a value between the scores of the two kinds of question. To test a candidate value, add `-e RAG_MIN_SCORE=0.6` after `--rm`. `RAG_MIN_SCORE` affects only this probe: to change what the agents use, edit the threshold in each builder (table above). n8n does not read `RAG_MIN_SCORE` from `.env`. To change the repository copy of all three agents, edit `RAG_MIN_SCORE` in `scripts/flows/langflow_fix.py` and regenerate the flows.

## Files

| Piece | File |
|---|---|
| Ingester and `--search` probe | `integrations/rag-cp-docs/ingest.py` |
| Corpus (eight notes) | `integrations/rag-cp-docs/corpus/*.md` |
| n8n agent and retriever | `n8n/backup/workflows/rag-cp-docs-agent.json`, `rag-cp-docs-retriever.json` |
| Flowise and Langflow agents | `integrations/flowise/rag-cp-docs.flowdata.json`, `integrations/langflow/rag-cp-docs.flow.json` |
| Offline tests | `python3 integrations/rag-cp-docs/test_ingest.py` |

The acceptance check `RAG` (`tests/acceptance/run.sh --only RAG`) proves the collection, the retrieval and, with model calls on, that the agent cites a source.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Tool result: `The Qdrant collection 'cp_docs' does not exist yet` | The ingest has not run | `docker compose up rag-ingest` |
| Tool result: `Could not embed the query with Ollama` | Ollama is down or `nomic-embed-text` is missing | `docker compose up -d ollama-cpu ollama-pull-models-cpu`, then retry |
| Tool result: `Qdrant rejected the search (HTTP 401)` | `QDRANT_API_KEY` is set but a client has the old value | `docker compose up -d`, then `docker compose run --rm n8n-import` and `docker compose run --rm builders-import` |
| Every question says "not covered" | Threshold too high for your questions | Calibrate with `--search`, then lower the threshold in the builder |
| Unrelated snippets appear | Threshold too low | Raise it the same way |
| The agent answers without sources | The model skipped the tool | Ask again, or check the execution for a `search_cp_docs` call |
| Odd matches after a model change | Corpus and question used different embedding models | `docker compose run --rm -e FORCE=1 rag-ingest` |
