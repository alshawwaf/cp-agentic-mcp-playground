# Visible RAG: integration notes

Visible RAG embeds a small, bundled corpus into Qdrant, and the **Documentation RAG Agent** in n8n,
Flowise and Langflow answers from it and cites its sources. The walkthrough for trainees is
[docs/guides/Visible_RAG.md](../../docs/guides/Visible_RAG.md).

| File | What it is |
|------|------------|
| `ingest.py` | The one-shot ingester: embed every snippet with Ollama, write it to Qdrant. Standard library only. |
| `corpus/*.md` | 8 short snippets on Check Point concepts. They are training content, paraphrased, not official Check Point documentation. |
| `test_ingest.py` | Offline tests with a mock Ollama and a mock Qdrant. |

## How it is wired

Everything is in the Standard lab. No setting is required.

| Service | Role |
|---------|------|
| `qdrant` | Vector database, Qdrant 1.19.1, data in the `qdrant_data` volume. Lab network only, no published port, telemetry off. |
| `ollama-cpu` | Embeddings with `nomic-embed-text` (768 dimensions). `ollama-pull-models-cpu` pulls it on every start. |
| `rag-ingest` | One-shot: runs `ingest.py` and exits. The corpus folder is mounted read-only. |

`ingest.py` writes the collection `cp_docs`, one point per snippet with the payload keys `text`,
`source` and `title`. It is safe on every start:

- Nothing changes until every embedding succeeded. If Ollama fails half-way, the existing
  collection stays as it was and the job exits 1.
- An existing collection is updated in place, and points of deleted snippets are removed. The
  collection is rebuilt only when the vector size changes (another `EMBED_MODEL`).
- When the collection already matches the corpus, it prints "already up to date" and exits 0
  without calling Ollama. `FORCE=1` re-embeds anyway.

## The agents and the relevance threshold

A vector search always returns its nearest snippets, even for a question the corpus does not cover.
Every retriever therefore ignores snippets that score below 0.5 (cosine score, 0 to 1).

| Builder | Retrieval path | Threshold |
|---------|----------------|-----------|
| n8n | Documentation RAG Agent, tool `search_cp_docs`, calls the sub-workflow **Documentation RAG Retriever**: Embed query (Ollama), Search cp_docs (Qdrant), Format hits + sources | Qdrant `score_threshold` 0.5 in the search request |
| Flowise | Qdrant, Similarity Score Threshold Retriever (50 %, up to 4 hits), Retriever Tool, Tool Agent | 50 % in the retriever |
| Langflow | Check Point Docs Retriever component | Field **Minimum Score** 0.5 |

The value comes from `RAG_MIN_SCORE` in `scripts/flows/langflow_fix.py`, which all three generators
use. The builders cannot read `.env` here (n8n blocks `$env` in nodes), so the threshold is part of
each flow. To change it, edit `RAG_MIN_SCORE` in `langflow_fix.py`, run the `apply` command of the
three generators, then re-seed (see [docs/development/DEVELOPER_GUIDE.md](../../docs/development/DEVELOPER_GUIDE.md)).
`RAG_MIN_SCORE` in `.env` sets only the threshold the `--search` probe reports against.

When no snippet passes, the n8n retriever tells the agent that the corpus does not cover the question
and that it must say so instead of answering from memory. Clearing **Minimum Score** in Langflow
turns the filter off.

## Calibrate the threshold

The probe embeds one question and lists the top hits with their scores, marking the ones that pass:

```sh
docker compose run --rm rag-ingest python3 ingest.py --search "How do I enable Identity Awareness?"
docker compose run --rm rag-ingest python3 ingest.py --search "What is the capital of France?"
```

Pick a value between the scores of the covered and the unrelated question.

## Optional: Qdrant authentication

With `QDRANT_API_KEY` blank, Qdrant has no authentication on the lab network. Set a key in `.env` to
turn authentication on. Qdrant then requires it, and every lab client sends it: `rag-ingest`, the n8n
credential **Lab Qdrant**, the Flowise credential **Lab Qdrant** and the Langflow variable
`QDRANT_API_KEY`. Apply it:

```sh
docker compose up -d
docker compose run --rm n8n-import
docker compose run --rm builders-import
```

## Re-ingest after a corpus change

```sh
docker compose run --rm rag-ingest
docker compose run --rm -e FORCE=1 rag-ingest     # re-embed every snippet
```

## Verify

```sh
tests/acceptance/run.sh --only RAG
```

Expected: `cp_docs` holds 8 points with 768 dimensions (`nomic-embed-text`), the retriever returns
hits with sources and, when model calls are on, the RAG agent cites a source. The end-to-end runs of the lab
passed this check with the local Ollama model and with Azure OpenAI. `./scripts/doctor.sh --post-start`
also reports the number of points.

## Offline tests

```sh
.github/scripts/py-isolated.sh -- python3 integrations/rag-cp-docs/test_ingest.py
tests/rag-threshold/run.sh
```

`test_ingest.py` covers the first ingest, the idempotent re-run, the in-place update, an Ollama
failure half-way, the Qdrant key, the model pull fallback and the `--search` probe.
`tests/rag-threshold/run.sh` runs the committed n8n and Langflow retrievers against mocks and checks
the 0.5 threshold. `scripts/flows/flowise_fix.py check` checks the Flowise retriever.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| The agent says the collection is missing | `docker compose run --rm rag-ingest`, then `docker compose logs rag-ingest` |
| `rag-ingest` cannot reach Ollama, or the model is missing | `docker compose up -d ollama-pull-models-cpu`, then run `rag-ingest` again |
| Qdrant rejects the key | `QDRANT_API_KEY` differs between Qdrant and the clients. Run the three commands under "Optional: Qdrant authentication" |
| The agent finds nothing for a covered question | Calibrate with `--search` and lower the threshold |
| Answers cite snippets for unrelated questions | Raise the threshold |

Labs that ran an older Qdrant kept their data in a volume named `<project>_qdrant_storage`. Qdrant
1.19 cannot open it, so the lab uses `qdrant_data` and `rag-ingest` refills it. The old volume is no
longer used.
