"""RAG relevance threshold, Langflow: the "Check Point Docs Retriever" component of the committed
integrations/langflow/rag-cp-docs.flow.json, run offline against a mock Ollama + Qdrant that implements
Qdrant's score_threshold semantics. Runs in the pinned Langflow image (it needs lfx), with no network.

    python langflow_retriever_test.py /repo/integrations/langflow/rag-cp-docs.flow.json
"""
import importlib.util
import json
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

FLOW = sys.argv[1] if len(sys.argv) > 1 else "/repo/integrations/langflow/rag-cp-docs.flow.json"
with open(FLOW, encoding="utf-8") as fh:
    flow = json.load(fh)
nodes = (flow.get("data") or flow)["nodes"]
codes = [n["data"]["node"]["template"]["code"]["value"] for n in nodes
         if "class CPDocsRetriever" in str(n.get("data", {}).get("node", {}).get("template", {}).get("code", {}).get("value", ""))]
if len(codes) != 1:
    print(f"FAIL expected exactly one CPDocsRetriever component in {FLOW}, found {len(codes)}")
    sys.exit(1)

REQS, MODE = [], {"m": "relevant"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        REQS.append((self.path, {k: v for k, v in body.items() if k != "vector"}))
        if self.path.startswith("/api/embeddings"):
            out = {"embedding": [0.1, 0.2]}
        else:
            scores = [0.81, 0.62, 0.44, 0.30] if MODE["m"] == "relevant" else [0.41, 0.33]
            hits = [{"id": i, "score": s, "payload": {"title": f"T{i}", "source": f"d{i}.md", "text": "x"}}
                    for i, s in enumerate(scores)]
            if isinstance(body.get("score_threshold"), (int, float)):
                hits = [h for h in hits if h["score"] >= body["score_threshold"]]
            out = {"result": hits[: body.get("limit", 10)]}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


srv = HTTPServer(("127.0.0.1", 0), Handler)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()

with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as fh:
    fh.write(codes[0])
    component_file = fh.name
spec = importlib.util.spec_from_file_location("cpdocs_component", component_file)
mod = importlib.util.module_from_spec(spec)
sys.modules["cpdocs_component"] = mod
spec.loader.exec_module(mod)
C = mod.CPDocsRetriever
C.OLLAMA_URL = f"http://127.0.0.1:{port}/api/embeddings"
C.QDRANT_URL = f"http://127.0.0.1:{port}/collections/cp_docs/points/search"


def run(mode, **attrs):
    MODE["m"] = mode
    REQS.clear()
    c = C()
    c.set_attributes({"query": "How do I enable Identity Awareness?", "qdrant_api_key": "", **attrs})
    return c.retrieve().text, list(REQS)


fails = 0
total = 0


def expect(name, cond, detail=""):
    global fails, total
    total += 1
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else f"  -> {detail}"))
    fails += 0 if cond else 1


text, reqs = run("relevant", min_score=0.5)
search = [b for p, b in reqs if "points/search" in p]
expect("search body carries score_threshold 0.5", search and search[0].get("score_threshold") == 0.5, reqs)
expect("relevant query: the 2 snippets above 0.5", text.startswith("Retrieved 2 snippet(s) scoring at least 0.5"), text[:120])
text, reqs = run("irrelevant", min_score=0.5)
expect("irrelevant query: the threshold message, no snippets",
       text.startswith("No snippet in the 'cp_docs' collection scored at least 0.5 (the relevance threshold)"), text[:160])
text, reqs = run("relevant", min_score=None)
expect("blank Minimum Score (Langflow coerces it to 0.0) turns the filter off",
       [b for p, b in reqs if "search" in p][0].get("score_threshold") == 0.0, reqs)
text, reqs = run("relevant", min_score=1.5)
expect("out-of-range Minimum Score rejected before any request",
       text.startswith("Minimum Score must be a number between 0 and 1") and not reqs, (text, reqs))
text, reqs = run("relevant", min_score=0.35)
expect("Minimum Score 0.35 is sent and lets 3 snippets through", text.startswith("Retrieved 3 snippet(s) scoring at least 0.35"), text[:120])
print(f"Langflow RAG threshold: {total - fails}/{total} passed")
sys.exit(1 if fails else 0)
