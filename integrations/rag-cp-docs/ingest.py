#!/usr/bin/env python3
"""Visible RAG — one-shot ingester for the Check Point docs demo corpus.

STDLIB ONLY (urllib) — no pip, so it runs anywhere in this playground without an
Artifactory round-trip. It walks the bundled `corpus/*.md` snippets and, for each:

    1. INGEST  — read the markdown snippet from disk
    2. EMBED   — POST it to Ollama  /api/embeddings  (model: nomic-embed-text)
    3. UPSERT  — write the vector + {text, source, title} payload into Qdrant

Before upserting it (re)creates the Qdrant collection with the exact vector size
reported by the embedding model, so the script is self-tuning and idempotent:
re-running it rebuilds `cp_docs` from scratch every time (stable integer point
ids), leaving you with exactly one point per snippet.

Endpoints (override with env vars):
    OLLAMA_URL   default http://ollama-cpu:11434
    QDRANT_URL   default http://qdrant:6333
    EMBED_MODEL  default nomic-embed-text
    COLLECTION   default cp_docs
    QDRANT_API_KEY  optional; sent as the `api-key` header if set

Run inside the demo network (see INTEGRATION.md for the `rag-ingest` compose
service), or locally against port-forwarded services:
    OLLAMA_URL=http://localhost:11434 QDRANT_URL=http://localhost:6333 \
        python3 ingest.py

Nothing here is Check Point-confidential: the corpus is clearly-marked demo text.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://ollama-cpu:11434").rstrip("/")
QDRANT_URL = os.environ.get("QDRANT_URL", "http://qdrant:6333").rstrip("/")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "nomic-embed-text")
COLLECTION = os.environ.get("COLLECTION", "cp_docs")
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY", "").strip()

CORPUS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "corpus")


def _request(method, url, payload=None, headers=None, timeout=120):
    """Minimal JSON HTTP helper. Returns (status_code, parsed_or_text)."""
    data = None
    hdrs = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
            status = resp.getcode()
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        status = e.code
    try:
        return status, json.loads(body) if body else None
    except json.JSONDecodeError:
        return status, body


def _qdrant_headers():
    return {"api-key": QDRANT_API_KEY} if QDRANT_API_KEY else None


def pull_model(model):
    """Ask Ollama to pull an embedding model that isn't present yet."""
    print("[embed]   model %r not found — pulling it (first run can take a "
          "few minutes)..." % model, flush=True)
    status, body = _request(
        "POST", OLLAMA_URL + "/api/pull",
        payload={"name": model, "stream": False}, timeout=1800,
    )
    if status != 200:
        raise SystemExit(
            "FATAL: could not pull embedding model %r (HTTP %s): %s\n"
            "        Pull it manually, then re-run:\n"
            "          docker exec -it ollama-cpu ollama pull %s"
            % (model, status, body, model)
        )
    print("[embed]   pull complete.", flush=True)


def embed(text, _allow_pull=True):
    """Return the embedding vector for `text` from Ollama, pulling the model
    once if the server reports it is missing."""
    status, body = _request(
        "POST", OLLAMA_URL + "/api/embeddings",
        payload={"model": EMBED_MODEL, "prompt": text}, timeout=300,
    )
    if status == 200 and isinstance(body, dict):
        vec = body.get("embedding")
        # tolerate the newer /api/embed shape if a proxy rewrites the call
        if vec is None and isinstance(body.get("embeddings"), list) and body["embeddings"]:
            vec = body["embeddings"][0]
        if vec:
            return vec
    # model-not-found → pull once and retry
    text_body = json.dumps(body) if not isinstance(body, str) else body
    if _allow_pull and ("not found" in text_body.lower() or status == 404):
        pull_model(EMBED_MODEL)
        return embed(text, _allow_pull=False)
    raise SystemExit(
        "FATAL: embedding call failed (HTTP %s): %s\n"
        "        Is Ollama reachable at %s and is %r pulled?"
        % (status, body, OLLAMA_URL, EMBED_MODEL)
    )


def recreate_collection(vector_size):
    """DELETE + PUT the collection so the run is idempotent and self-tuning."""
    _request("DELETE", "%s/collections/%s" % (QDRANT_URL, COLLECTION),
             headers=_qdrant_headers())  # ignore 404 on first run
    status, body = _request(
        "PUT", "%s/collections/%s" % (QDRANT_URL, COLLECTION),
        payload={"vectors": {"size": vector_size, "distance": "Cosine"}},
        headers=_qdrant_headers(),
    )
    if status not in (200, 201):
        raise SystemExit(
            "FATAL: could not create Qdrant collection %r (HTTP %s): %s\n"
            "        Is Qdrant reachable at %s?"
            % (COLLECTION, status, body, QDRANT_URL)
        )
    print("[qdrant]  collection %r ready (size=%d, distance=Cosine)."
          % (COLLECTION, vector_size), flush=True)


def upsert(points):
    status, body = _request(
        "PUT", "%s/collections/%s/points?wait=true" % (QDRANT_URL, COLLECTION),
        payload={"points": points}, headers=_qdrant_headers(),
    )
    if status not in (200, 201):
        raise SystemExit(
            "FATAL: Qdrant upsert failed (HTTP %s): %s" % (status, body)
        )


def load_corpus():
    if not os.path.isdir(CORPUS_DIR):
        raise SystemExit("FATAL: corpus directory not found: %s" % CORPUS_DIR)
    files = sorted(f for f in os.listdir(CORPUS_DIR) if f.endswith(".md"))
    if not files:
        raise SystemExit("FATAL: no .md snippets in %s" % CORPUS_DIR)
    docs = []
    for fname in files:
        with open(os.path.join(CORPUS_DIR, fname), "r", encoding="utf-8") as fh:
            text = fh.read().strip()
        # title = first markdown heading if present, else the filename stem
        title = fname[:-3]
        for line in text.splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                break
        docs.append({"source": fname, "title": title, "text": text})
    return docs


def main():
    print("=== Visible RAG ingest ===", flush=True)
    print("    ollama     : %s (model %s)" % (OLLAMA_URL, EMBED_MODEL), flush=True)
    print("    qdrant     : %s (collection %s)" % (QDRANT_URL, COLLECTION), flush=True)

    docs = load_corpus()
    print("    corpus     : %d snippet(s) from %s\n" % (len(docs), CORPUS_DIR), flush=True)

    points = []
    vector_size = None
    for i, doc in enumerate(docs, start=1):
        print("[ingest]  (%d/%d) %s" % (i, len(docs), doc["source"]), flush=True)
        vec = embed(doc["text"])
        if vector_size is None:
            vector_size = len(vec)
            recreate_collection(vector_size)
        elif len(vec) != vector_size:
            raise SystemExit(
                "FATAL: inconsistent embedding size (%d vs %d) — mixed models?"
                % (len(vec), vector_size)
            )
        points.append({
            "id": i,
            "vector": vec,
            "payload": {
                "text": doc["text"],
                "source": doc["source"],
                "title": doc["title"],
            },
        })
        print("[embed]   -> %d-dim vector" % len(vec), flush=True)

    upsert(points)
    print("\n[qdrant]  upserted %d point(s) into %r." % (len(points), COLLECTION), flush=True)
    print("=== done — ask the RAG agent a question and watch it cite these "
          "sources. ===", flush=True)


if __name__ == "__main__":
    t0 = time.time()
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # pragma: no cover - defensive top-level guard
        print("FATAL: unexpected error: %r" % exc, file=sys.stderr)
        sys.exit(1)
    print("(%.1fs)" % (time.time() - t0), flush=True)
