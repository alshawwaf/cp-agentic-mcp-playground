#!/usr/bin/env python3
"""
Offline tests for ingest.py (Visible RAG). Standard library only; no Ollama,
no Qdrant, no network: both are mocked on 127.0.0.1 and ingest.py runs as a
subprocess exactly as the rag-ingest service runs it.

    python3 integrations/rag-cp-docs/test_ingest.py

Covers: first ingest, idempotent re-run (no embedding calls), in-place update
with stale-point removal, an Ollama failure half-way leaving the collection
untouched, QDRANT_API_KEY sent as the api-key header (and a clear hint when it
is wrong), the model pull fallback, and the --search relevance probe.
"""
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
INGEST = os.path.join(HERE, "ingest.py")
CORPUS = os.path.join(HERE, "corpus")
DIM = 16


def fake_vector(text):
    """Deterministic bag-of-words vector, so similar texts score higher."""
    vec = [0.0] * DIM
    for word in text.lower().split():
        h = hashlib.sha256(word.strip(".,?!:;()`*#>").encode()).digest()
        vec[h[0] % DIM] += 1.0
    return vec or [1.0] * DIM


def cosine(a, b):
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b)) / (na * nb) if na and nb else 0.0


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self):  # skip the reverse-DNS lookup HTTPServer does
        super(HTTPServer, self).server_bind()
        self.server_name, self.server_port = "127.0.0.1", self.server_address[1]


class Mocks:
    def __init__(self):
        self.embed_calls = 0
        self.fail_after = None      # Ollama answers 500 after this many embeddings
        self.model_missing = False  # first embedding answers 404 "model not found"
        self.pulls = 0
        self.api_key = ""           # Qdrant requires this api-key header when set
        self.keys_seen = set()
        self.collections = {}       # name -> {"size": n, "points": {id: (vector, payload)}}
        mocks = self

        class H(BaseHTTPRequestHandler):
            def _json(self, status, obj):
                body = json.dumps(obj).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(n) or b"{}") if n else {}

            def _route(self, method):
                path = urllib.parse.urlsplit(self.path).path
                body = self._body() if method in ("POST", "PUT") else {}
                if path == "/api/embeddings":
                    return self._ollama(body)
                if path == "/api/pull":
                    mocks.pulls += 1
                    mocks.model_missing = False
                    return self._json(200, {"status": "success"})
                mocks.keys_seen.add(self.headers.get("api-key", ""))
                if mocks.api_key and self.headers.get("api-key") != mocks.api_key:
                    return self._json(401, {"status": {"error": "Must provide an API key or an Authorization bearer token"}})
                parts = path.strip("/").split("/")
                if len(parts) < 2 or parts[0] != "collections":
                    return self._json(404, {"status": {"error": "not found"}})
                return self._qdrant(method, parts[1], "/".join(parts[2:]), body)

            def _ollama(self, body):
                if mocks.model_missing:
                    return self._json(404, {"error": "model \"nomic-embed-text\" not found, try pulling it first"})
                if mocks.fail_after is not None and mocks.embed_calls >= mocks.fail_after:
                    return self._json(500, {"error": "llama runner process has terminated"})
                mocks.embed_calls += 1
                self._json(200, {"embedding": fake_vector(body.get("prompt", ""))})

            def _qdrant(self, method, name, rest, body):
                col = mocks.collections.get(name)
                if rest == "" and method == "GET":
                    if col is None:
                        return self._json(404, {"status": {"error": "Not found: Collection"}})
                    return self._json(200, {"result": {"config": {"params": {
                        "vectors": {"size": col["size"], "distance": "Cosine"}}}}})
                if rest == "" and method == "PUT":
                    mocks.collections[name] = {"size": body["vectors"]["size"], "points": {}}
                    return self._json(200, {"result": True})
                if rest == "" and method == "DELETE":
                    mocks.collections.pop(name, None)
                    return self._json(200, {"result": True})
                if col is None:
                    return self._json(404, {"status": {"error": "Not found: Collection"}})
                pts = col["points"]
                if rest == "points/scroll":
                    keys = body.get("with_payload")
                    out = [{"id": pid, "payload": {k: v for k, v in pl.items() if not isinstance(keys, list) or k in keys}}
                           for pid, (_, pl) in sorted(pts.items(), key=lambda kv: str(kv[0]))]
                    return self._json(200, {"result": {"points": out, "next_page_offset": None}})
                if rest == "points" and method == "PUT":
                    for p in body["points"]:
                        assert len(p["vector"]) == col["size"]
                        pts[p["id"]] = (p["vector"], p["payload"])
                    return self._json(200, {"result": {"status": "completed"}})
                if rest == "points/delete":
                    for pid in body["points"]:
                        pts.pop(pid, None)
                    return self._json(200, {"result": {"status": "completed"}})
                if rest == "points/count":
                    return self._json(200, {"result": {"count": len(pts)}})
                if rest == "points/search":
                    scored = sorted(((cosine(body["vector"], v), pid, pl) for pid, (v, pl) in pts.items()), reverse=True)
                    hits = [{"id": pid, "score": round(s, 4), "payload": pl} for s, pid, pl in scored[:body.get("limit", 4)]]
                    return self._json(200, {"result": hits})
                return self._json(404, {"status": {"error": "unknown route"}})

            def do_GET(self):
                self._route("GET")

            def do_PUT(self):
                self._route("PUT")

            def do_POST(self):
                self._route("POST")

            def do_DELETE(self):
                self._route("DELETE")

            def log_message(self, *a):
                pass

        self.server = _Server(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


class IngestTests(unittest.TestCase):
    def setUp(self):
        self.m = Mocks()
        self.tmp = tempfile.mkdtemp(prefix="rag-test-")
        self.corpus = os.path.join(self.tmp, "corpus")
        shutil.copytree(CORPUS, self.corpus)

    def tearDown(self):
        self.m.server.shutdown()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_ingest(self, *args, **env):
        e = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONDONTWRITEBYTECODE": "1",
             "OLLAMA_URL": self.m.url, "QDRANT_URL": self.m.url, "CORPUS_DIR": self.corpus,
             "COLLECTION": "cp_docs"}
        e.update({k: str(v) for k, v in env.items()})
        p = subprocess.run([sys.executable, INGEST, *args], env=e, capture_output=True, text=True, timeout=60)
        return p.returncode, p.stdout + p.stderr

    def points(self):
        return self.m.collections["cp_docs"]["points"]

    def n_docs(self):
        return len([f for f in os.listdir(self.corpus) if f.endswith(".md")])

    def test_first_run_then_idempotent(self):
        rc, out = self.run_ingest()
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(self.points()), self.n_docs())
        self.assertEqual(self.m.embed_calls, self.n_docs())
        for _, payload in self.points().values():
            self.assertTrue({"text", "source", "title", "sha256"} <= set(payload))
        rc, out = self.run_ingest()
        self.assertEqual(rc, 0, out)
        self.assertIn("nothing to embed", out)
        self.assertEqual(self.m.embed_calls, self.n_docs(), "a no-change re-run must not call Ollama")

    def test_update_in_place_and_remove_stale(self):
        self.assertEqual(self.run_ingest()[0], 0)
        before = set(self.points())
        files = sorted(f for f in os.listdir(self.corpus) if f.endswith(".md"))
        os.remove(os.path.join(self.corpus, files[0]))
        with open(os.path.join(self.corpus, files[1]), "a", encoding="utf-8") as fh:
            fh.write("\nAn extra sentence about policy layers.\n")
        rc, out = self.run_ingest()
        self.assertEqual(rc, 0, out)
        self.assertIn("updating 'cp_docs' in place", out)
        self.assertIn("removed 1 stale point", out)
        self.assertEqual(len(self.points()), len(files) - 1)
        self.assertLess(set(self.points()), before, "ids must be stable per file")

    def test_ollama_failure_leaves_collection_untouched(self):
        self.assertEqual(self.run_ingest()[0], 0)
        snapshot = json.dumps(self.points(), sort_keys=True, default=str)
        with open(os.path.join(self.corpus, "new-snippet.md"), "w", encoding="utf-8") as fh:
            fh.write("# New snippet\n\nSample text.\n")
        self.m.fail_after = self.m.embed_calls + 2
        rc, out = self.run_ingest(FORCE=1)
        self.assertEqual(rc, 1)
        self.assertIn("was NOT changed", out)
        self.assertEqual(json.dumps(self.points(), sort_keys=True, default=str), snapshot)

    def test_qdrant_api_key(self):
        self.m.api_key = "test-qdrant-key"
        rc, out = self.run_ingest(QDRANT_API_KEY="test-qdrant-key")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.m.keys_seen, {"test-qdrant-key"})
        self.assertNotIn("test-qdrant-key", out, "the key must never be printed")
        rc, out = self.run_ingest(QDRANT_API_KEY="wrong-key")
        self.assertEqual(rc, 1)
        self.assertIn("rejected the API key", out)
        self.assertNotIn("wrong-key", out)

    def test_pulls_missing_model(self):
        self.m.model_missing = True
        rc, out = self.run_ingest()
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.m.pulls, 1)

    def test_search_probe(self):
        self.assertEqual(self.run_ingest()[0], 0)
        rc, out = self.run_ingest("--search", "How do I enable Identity Awareness?", RAG_MIN_SCORE="0.3")
        self.assertEqual(rc, 0, out)
        self.assertIn("RAG_MIN_SCORE = 0.30", out)
        self.assertIn("identity-awareness.md", out.splitlines()[3], "best hit should be listed first")
        rc, out = self.run_ingest("--search", "x", RAG_MIN_SCORE="1.5")
        self.assertEqual(rc, 1)
        self.assertIn("between 0 and 1", out)
        rc, out = self.run_ingest("--bogus")
        self.assertEqual(rc, 1)

    def test_vector_size_change_rebuilds(self):
        self.m.collections["cp_docs"] = {"size": DIM + 1, "points": {1: ([0.0] * (DIM + 1), {"text": "old"})}}
        rc, out = self.run_ingest()
        self.assertEqual(rc, 0, out)
        self.assertIn("rebuilding the collection", out)
        self.assertEqual(len(self.points()), self.n_docs())


if __name__ == "__main__":
    unittest.main(verbosity=2)
