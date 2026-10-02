#!/usr/bin/env python3
"""Visible RAG — one-shot ingester for the Check Point documentation sample corpus.

STDLIB ONLY (urllib) — no pip, so it runs anywhere in this lab. It walks
the bundled `corpus/*.md` snippets and:

    1. INGEST  — read every markdown snippet from disk
    2. EMBED   — POST each one to Ollama /api/embeddings (model: nomic-embed-text)
    3. UPSERT  — write vector + {text, source, title} payload into Qdrant

Safe to run on every `docker compose up`:

* NOTHING CHANGES UNTIL EVERY EMBEDDING SUCCEEDED. All snippets are embedded
  first, in memory. If Ollama fails half-way, the existing `cp_docs` collection
  is left exactly as it was (the RAG agents keep answering from it) and the job
  exits non-zero with the reason.
* NO EMPTY WINDOW. An existing collection is updated in place: points are
  upserted under stable ids (one per snippet file), then points for snippets you
  deleted are removed. The collection is only dropped and rebuilt when the
  vector size changes (you switched EMBED_MODEL).
* SKIPS WORK WHEN NOTHING CHANGED. Each point stores a hash of its text and the
  embedding model. If the collection already matches the corpus, the job prints
  "already up to date" and exits 0 without calling Ollama. Set FORCE=1 to
  re-embed anyway.

Payload keys `text`, `source` and `title` are what the n8n retriever, the
Flowise Qdrant node (contentPayloadKey=text) and the Langflow retriever read —
keep them if you edit this script.

Endpoints (override with env vars):
    OLLAMA_URL      default http://ollama-cpu:11434
    QDRANT_URL      default http://qdrant:6333
    EMBED_MODEL     default nomic-embed-text
    COLLECTION      default cp_docs
    CORPUS_DIR      default ./corpus next to this script
    QDRANT_API_KEY  optional; sent as the `api-key` header if set
    FORCE           1 = re-embed and rewrite every point even if nothing changed
    RAG_MIN_SCORE   minimum cosine score a hit needs to count as relevant
                    (default 0.5; used by --search, see "Relevance threshold")

Run inside the lab network (the `rag-ingest` compose service does this):
    docker compose up rag-ingest

Relevance threshold. A vector search always returns its `limit` nearest
snippets, even for a question the corpus does not cover. The retrievers should
therefore drop hits below a minimum score (Qdrant `score_threshold`). With
nomic-embed-text and cosine distance, 0.5 is a deliberately permissive default:
it removes clearly unrelated snippets without hiding real matches. Calibrate it
for your corpus with --search, which embeds one question, lists the top hits
with their scores and marks the ones that pass RAG_MIN_SCORE:
    docker compose run --rm rag-ingest python3 ingest.py --search "How do I enable Identity Awareness?"
    docker compose run --rm rag-ingest python3 ingest.py --search "What is the capital of France?"
Use a value between the scores of the two kinds of question, and set the same
value as score_threshold in every retriever.

Nothing here is Check Point-confidential: the corpus is clearly marked sample
text, not official documentation.
Exit code: 0 on success (or nothing to do), 1 on any failure.
"""
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://ollama-cpu:11434").rstrip("/")
QDRANT_URL = os.environ.get("QDRANT_URL", "http://qdrant:6333").rstrip("/")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "nomic-embed-text").strip()
COLLECTION = os.environ.get("COLLECTION", "cp_docs").strip()
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY", "").strip()
FORCE = os.environ.get("FORCE", "").strip().lower() in ("1", "true", "yes")
DEFAULT_MIN_SCORE = 0.5
CORPUS_DIR = os.environ.get("CORPUS_DIR", "").strip() or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "corpus")

# Stable point ids: uuid5 of the snippet's file name, so the same file always
# maps to the same point (re-runs overwrite instead of duplicating).
ID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "cp-agentic-mcp-playground/rag-cp-docs")
EMBED_RETRIES = 3
# nomic-embed-text reads about 2k tokens per input in Ollama's default context;
# longer snippets are silently truncated, so warn the author.
LONG_SNIPPET_CHARS = 6000


def log(msg):
    print(msg, flush=True)


def fatal(msg):
    print("FATAL: " + msg, file=sys.stderr, flush=True)
    sys.exit(1)


def _request(method, url, payload=None, headers=None, timeout=120):
    """Minimal JSON HTTP helper.

    Returns (status, parsed_body_or_text). status is 0 when the server could not
    be reached at all (DNS, connection refused, timeout) and the body is then the
    reason string, so callers can print a useful hint instead of a traceback.
    """
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
    except (urllib.error.URLError, OSError) as e:
        return 0, str(getattr(e, "reason", e))
    try:
        return status, json.loads(body) if body else None
    except ValueError:
        return status, body


def _short(body, limit=300):
    text = body if isinstance(body, str) else json.dumps(body)
    return text if len(text) <= limit else text[:limit] + "..."


# ───────────────────────────── corpus ─────────────────────────────

def load_corpus():
    if not os.path.isdir(CORPUS_DIR):
        fatal("corpus directory not found: %s" % CORPUS_DIR)
    files = sorted(f for f in os.listdir(CORPUS_DIR) if f.endswith(".md"))
    if not files:
        fatal("no .md snippets in %s" % CORPUS_DIR)
    docs = []
    for fname in files:
        with open(os.path.join(CORPUS_DIR, fname), "r", encoding="utf-8") as fh:
            text = fh.read().strip()
        if not text:
            log("[ingest]  skipping empty file %s" % fname)
            continue
        # title = first markdown heading if present, else the filename stem
        title = fname[:-3]
        for line in text.splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                break
        if len(text) > LONG_SNIPPET_CHARS:
            log("[ingest]  WARNING: %s is %d characters; the embedding model only "
                "reads about the first %d. Split it into smaller snippets for "
                "better retrieval." % (fname, len(text), LONG_SNIPPET_CHARS))
        digest = hashlib.sha256(
            (EMBED_MODEL + "\n" + text).encode("utf-8")).hexdigest()
        docs.append({
            "id": str(uuid.uuid5(ID_NAMESPACE, fname)),
            "source": fname,
            "title": title,
            "text": text,
            "sha256": digest,
        })
    if not docs:
        fatal("every .md snippet in %s is empty" % CORPUS_DIR)
    return docs


# ───────────────────────────── Ollama ─────────────────────────────

def pull_model(model):
    """Ask Ollama to pull an embedding model that isn't present yet."""
    log("[embed]   model %r not found — pulling it (first run can take a few "
        "minutes)..." % model)
    status, body = _request(
        "POST", OLLAMA_URL + "/api/pull",
        payload={"model": model, "name": model, "stream": False}, timeout=1800,
    )
    if status != 200:
        fatal("could not pull embedding model %r (HTTP %s): %s\n"
              "       Pull it by hand, then re-run the ingest:\n"
              "         docker compose exec ollama-cpu ollama pull %s\n"
              "         docker compose up rag-ingest"
              % (model, status, _short(body), model))
    log("[embed]   pull complete.")


def embed(text):
    """Return the embedding vector for `text`.

    Retries transient failures (Ollama busy, restarting, loading the model) and
    pulls the model once if Ollama reports it missing.
    """
    pulled = False
    attempt = 0
    while True:
        attempt += 1
        status, body = _request(
            "POST", OLLAMA_URL + "/api/embeddings",
            payload={"model": EMBED_MODEL, "prompt": text}, timeout=300,
        )
        if status == 200 and isinstance(body, dict):
            vec = body.get("embedding")
            # tolerate the newer /api/embed shape if a proxy rewrites the call
            if not vec and isinstance(body.get("embeddings"), list) and body["embeddings"]:
                vec = body["embeddings"][0]
            if vec:
                return vec
        text_body = _short(body)
        if not pulled and (status == 404 or "not found" in text_body.lower()):
            pull_model(EMBED_MODEL)
            pulled = True
            continue
        if attempt < EMBED_RETRIES and (status == 0 or status >= 500 or status == 200):
            wait = 3 * attempt
            log("[embed]   Ollama call failed (%s); retrying in %ds..."
                % ("unreachable: " + text_body if status == 0 else "HTTP %s" % status, wait))
            time.sleep(wait)
            continue
        where = "unreachable (%s)" % text_body if status == 0 else "HTTP %s: %s" % (status, text_body)
        fatal("embedding call failed — %s\n"
              "       Is Ollama running at %s with %r pulled?\n"
              "       The existing %r collection was NOT changed."
              % (where, OLLAMA_URL, EMBED_MODEL, COLLECTION))


# ───────────────────────────── Qdrant ─────────────────────────────

def _qdrant(method, path, payload=None, timeout=120):
    headers = {"api-key": QDRANT_API_KEY} if QDRANT_API_KEY else None
    return _request(method, QDRANT_URL + path, payload=payload,
                    headers=headers, timeout=timeout)


def _qdrant_fail(what, status, body):
    hint = ""
    if status == 0:
        hint = "\n       Is Qdrant running and reachable at %s?" % QDRANT_URL
    elif status in (401, 403):
        hint = ("\n       Qdrant rejected the API key. QDRANT_API_KEY must match the "
                "key the qdrant service was started with (or both must be empty).")
    where = "unreachable: %s" % _short(body) if status == 0 else "HTTP %s: %s" % (status, _short(body))
    fatal("%s (%s)%s" % (what, where, hint))


def collection_info():
    """Return (vector_size, distance) of the collection, or None if it is missing."""
    status, body = _qdrant("GET", "/collections/%s" % COLLECTION)
    if status == 404:
        return None
    if status != 200 or not isinstance(body, dict):
        _qdrant_fail("could not read Qdrant collection %r" % COLLECTION, status, body)
    vectors = (((body.get("result") or {}).get("config") or {}).get("params") or {}).get("vectors") or {}
    if "size" not in vectors:
        # named-vector layout: this script did not create it, so rebuild it.
        return (None, None)
    return (vectors.get("size"), vectors.get("distance"))


def existing_points():
    """Return {str(point_id): (raw_id, sha256_or_None)} for every point.

    raw_id keeps Qdrant's own type (int or uuid string) so stale points written
    by older versions of this script (integer ids) can still be deleted.
    """
    points = {}
    offset = None
    while True:
        payload = {"limit": 256, "with_payload": ["sha256"], "with_vector": False}
        if offset is not None:
            payload["offset"] = offset
        status, body = _qdrant("POST", "/collections/%s/points/scroll" % COLLECTION, payload)
        if status != 200 or not isinstance(body, dict):
            _qdrant_fail("could not list points in %r" % COLLECTION, status, body)
        result = body.get("result") or {}
        for p in result.get("points") or []:
            points[str(p.get("id"))] = (p.get("id"), (p.get("payload") or {}).get("sha256"))
        offset = result.get("next_page_offset")
        if offset is None:
            return points


def create_collection(vector_size):
    status, body = _qdrant(
        "PUT", "/collections/%s" % COLLECTION,
        {"vectors": {"size": vector_size, "distance": "Cosine"}},
    )
    if status not in (200, 201):
        _qdrant_fail("could not create Qdrant collection %r" % COLLECTION, status, body)
    log("[qdrant]  collection %r created (size=%d, distance=Cosine)."
        % (COLLECTION, vector_size))


def delete_collection():
    status, body = _qdrant("DELETE", "/collections/%s" % COLLECTION)
    if status not in (200, 404):
        _qdrant_fail("could not delete Qdrant collection %r" % COLLECTION, status, body)


def upsert(points):
    status, body = _qdrant(
        "PUT", "/collections/%s/points?wait=true" % COLLECTION, {"points": points},
        timeout=300,
    )
    if status not in (200, 201):
        _qdrant_fail("Qdrant upsert failed", status, body)


def delete_points(ids):
    status, body = _qdrant(
        "POST", "/collections/%s/points/delete?wait=true" % COLLECTION, {"points": ids},
    )
    if status not in (200, 201):
        _qdrant_fail("could not remove stale points from %r" % COLLECTION, status, body)


def count_points():
    status, body = _qdrant(
        "POST", "/collections/%s/points/count" % COLLECTION, {"exact": True})
    if status != 200 or not isinstance(body, dict):
        _qdrant_fail("could not count points in %r" % COLLECTION, status, body)
    return (body.get("result") or {}).get("count")


def min_score():
    raw = os.environ.get("RAG_MIN_SCORE", "").strip()
    if not raw:
        return DEFAULT_MIN_SCORE
    try:
        value = float(raw)
    except ValueError:
        fatal("RAG_MIN_SCORE=%r is not a number (use a value such as 0.5)" % raw)
    if not 0.0 <= value <= 1.0:
        fatal("RAG_MIN_SCORE must be between 0 and 1 (got %s)" % raw)
    return value


def search(question, limit=4):
    """Embed one question and print the top hits with their scores (no writes)."""
    threshold = min_score()
    if collection_info() is None:
        fatal("collection %r does not exist yet; run the ingest first (docker compose up rag-ingest)"
              % COLLECTION)
    vec = embed(question)
    status, body = _qdrant("POST", "/collections/%s/points/search" % COLLECTION,
                           {"vector": vec, "limit": limit, "with_payload": ["title", "source"]})
    if status != 200 or not isinstance(body, dict):
        _qdrant_fail("Qdrant search failed", status, body)
    hits = body.get("result") or []
    log("Top %d of %r for: %s" % (limit, COLLECTION, question))
    log("RAG_MIN_SCORE = %.2f (hits below it would be dropped by a retriever with that score_threshold)\n"
        % threshold)
    for h in hits:
        p = h.get("payload") or {}
        score = h.get("score") or 0.0
        log("  %.3f  %-4s  %s (%s)" % (score, "keep" if score >= threshold else "drop",
                                      p.get("title") or "?", p.get("source") or "?"))
    kept = sum(1 for h in hits if (h.get("score") or 0.0) >= threshold)
    log("\n%d of %d hit(s) pass the threshold." % (kept, len(hits)))


# ───────────────────────────── main ─────────────────────────────

def main():
    log("=== Visible RAG ingest ===")
    log("    ollama     : %s (model %s)" % (OLLAMA_URL, EMBED_MODEL))
    log("    qdrant     : %s (collection %s)" % (QDRANT_URL, COLLECTION))

    docs = load_corpus()
    log("    corpus     : %d snippet(s) from %s\n" % (len(docs), CORPUS_DIR))

    info = collection_info()
    current = existing_points() if info is not None else {}
    wanted = {d["id"]: d["sha256"] for d in docs}
    if info is not None and not FORCE and {k: v[1] for k, v in current.items()} == wanted:
        log("[qdrant]  %r already matches the corpus (%d point(s), model %s) — "
            "nothing to embed." % (COLLECTION, len(docs), EMBED_MODEL))
        log("          Edit a file in corpus/ (or set FORCE=1) and re-run to re-embed.")
        return

    # 1) EMBED everything first. Nothing in Qdrant is touched until this succeeds.
    points = []
    vector_size = None
    for i, doc in enumerate(docs, start=1):
        log("[ingest]  (%d/%d) %s" % (i, len(docs), doc["source"]))
        vec = embed(doc["text"])
        if vector_size is None:
            vector_size = len(vec)
        elif len(vec) != vector_size:
            fatal("inconsistent embedding size (%d vs %d) — mixed models? "
                  "The existing %r collection was NOT changed."
                  % (len(vec), vector_size, COLLECTION))
        log("[embed]   -> %d-dim vector" % len(vec))
        points.append({
            "id": doc["id"],
            "vector": vec,
            "payload": {
                "text": doc["text"],
                "source": doc["source"],
                "title": doc["title"],
                "sha256": doc["sha256"],
                "embed_model": EMBED_MODEL,
            },
        })

    # 2) WRITE: create, update in place, or rebuild only if the vector size changed.
    if info is None:
        create_collection(vector_size)
    elif info != (vector_size, "Cosine"):
        log("[qdrant]  %r has vector size/distance %s but %s produces %d-dim vectors "
            "— rebuilding the collection." % (COLLECTION, info, EMBED_MODEL, vector_size))
        delete_collection()
        create_collection(vector_size)
        current = {}
    else:
        log("[qdrant]  updating %r in place (size=%d, distance=Cosine)."
            % (COLLECTION, vector_size))

    upsert(points)
    stale = [current[pid][0] for pid in sorted(current) if pid not in wanted]
    if stale:
        delete_points(stale)
        log("[qdrant]  removed %d stale point(s) (deleted snippets, or ids from an older ingest)."
            % len(stale))

    total = count_points()
    log("\n[qdrant]  upserted %d point(s) into %r (collection now holds %s)."
        % (len(points), COLLECTION, total))
    if total != len(points):
        fatal("expected %d point(s) in %r after the ingest, found %s."
              % (len(points), COLLECTION, total))
    log("=== done — ask the RAG agent a question and watch it cite these sources. ===")


if __name__ == "__main__":
    t0 = time.time()
    try:
        if len(sys.argv) >= 2 and sys.argv[1] == "--search":
            if len(sys.argv) != 3 or not sys.argv[2].strip():
                fatal('usage: python3 ingest.py --search "your question"')
            search(sys.argv[2].strip())
            sys.exit(0)
        if len(sys.argv) > 1:
            fatal('unknown arguments %s (usage: python3 ingest.py [--search "question"])' % sys.argv[1:])
        main()
    except SystemExit:
        raise
    except Exception as exc:  # pragma: no cover - defensive top-level guard
        print("FATAL: unexpected error: %r" % exc, file=sys.stderr)
        sys.exit(1)
    print("(%.1fs)" % (time.time() - t0), flush=True)
