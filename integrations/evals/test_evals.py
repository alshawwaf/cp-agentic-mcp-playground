#!/usr/bin/env python3
"""
Offline tests for the evals harness. Standard library only; n8n is mocked on
127.0.0.1 and run_evals.py runs as a subprocess, as the evals-run service does.

    python3 integrations/evals/test_evals.py

Covers: HTTP Basic sign-in on every chat call, a unique sessionId per case and
per run, answer-quality FAIL vs wiring/setup ERROR classification (401, 404,
500, timeout, the workflows' error-branch replies, Lakera not configured), no
secret in the console output or the reports, and plain-http refusal for remote
hosts. When n8n/backup/workflows is present (repo checkout, CI), it also checks
that every case's webhookId and agent name match a committed chat trigger.
"""
import base64
import glob
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
RUNNER = os.path.join(HERE, "run_evals.py")
CASES = os.path.join(HERE, "evals_cases.json")
WORKFLOWS = os.path.join(os.path.dirname(os.path.dirname(HERE)), "n8n", "backup", "workflows")
EMAIL, PASSWORD = "admin@lab.local", "Test-Password-For-Evals-1"

with open(CASES, encoding="utf-8") as fh:
    CASE_LIST = json.load(fh)["cases"]
BY_NAME = {c["name"]: c for c in CASE_LIST}

# What the mock n8n answers per case: (status, body or callable, delay seconds)
SCRIPT = {
    "rag_identity_awareness_cited": (200, {"output": "Enable it on the gateway object. (source: identity-awareness.md)"}, 0),
    "reputation_8888_clean": (200, {"output": "I could not complete this request. This agent only reads, so nothing "
                                              "was changed.\n\nReason: fetch failed\n\nThe lab model endpoint ..."}, 0),
    "management_access_layers": (200, {"output": "There are two layers: Application and Data."}, 0),
    "threat_prevention_profiles_optimized": (500, {"message": "Error in workflow"}, 0),
    "documentation_identity_awareness": (404, {"code": 404, "message": "The requested webhook is not registered."}, 0),
    "documentation_identity_awareness_gateway": (200, {"output": "slow"}, 2.5),
    "devhub_apps_count": (200, {"output": "There are 4 apps: Portal, Wiki, CI, Chat."}, 0),
    "policypilot_network_layer_summary": (200, {"output": "The Network layer has 12 rules."}, 0),
    "guarded_chat_injection_blocked": (200, {"output": "Lakera Guard is not configured, so this agent did not run. "
                                                       "Set LAKERA_API_KEY in .env, then run `docker compose run --rm n8n-import`."}, 0),
    "guarded_chat_safe_passes": (200, {"output": "A Threat Prevention profile defines which protections run."}, 0),
}
EXPECTED = {
    "rag_identity_awareness_cited": ("pass", ""),
    "reputation_8888_clean": ("wiring", "agent-error"),
    "management_access_layers": ("quality", "answer"),
    "threat_prevention_profiles_optimized": ("wiring", "workflow-error"),
    "documentation_identity_awareness": ("wiring", "not-published"),
    "documentation_identity_awareness_gateway": ("wiring", "timeout"),
    "devhub_apps_count": ("pass", ""),
    "policypilot_network_layer_summary": ("pass", ""),
    "guarded_chat_injection_blocked": ("wiring", "not-configured"),
    "guarded_chat_safe_passes": ("pass", ""),
}


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self):  # skip the reverse-DNS lookup HTTPServer does
        super(HTTPServer, self).server_bind()
        self.server_name, self.server_port = "127.0.0.1", self.server_address[1]


class MockN8n:
    def __init__(self):
        self.calls = []  # (webhookId, sessionId, authorization header)
        by_id = {}
        for c in CASE_LIST:
            by_id.setdefault(c["webhookId"], []).append(c)
        mock = self
        good = "Basic " + base64.b64encode(f"{EMAIL}:{PASSWORD}".encode()).decode()

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                parts = self.path.strip("/").split("/")
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                auth = self.headers.get("Authorization", "")
                mock.calls.append((parts[1] if len(parts) > 1 else "", body.get("sessionId"), auth))
                if auth != good:
                    return self._send(401, {"message": "Authorization is required!"}, 0)
                cases = by_id.get(parts[1] if len(parts) == 3 else "")
                if not cases:
                    return self._send(404, {"message": "not registered"}, 0)
                case = next((c for c in cases if c["prompt"] == body.get("chatInput")), cases[0])
                status, payload, delay = SCRIPT[case["name"]]
                self._send(status, payload, delay)

            def _send(self, status, payload, delay):
                if delay:
                    time.sleep(delay)
                data = json.dumps(payload).encode()
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *a):
                pass

        self.server = _Server(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


class Harness(unittest.TestCase):
    def setUp(self):
        self.n8n = MockN8n()
        self.out = tempfile.mkdtemp(prefix="evals-test-")

    def tearDown(self):
        self.n8n.server.shutdown()

    def run_harness(self, **env):
        e = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONDONTWRITEBYTECODE": "1",
             "BASE_URL": self.n8n.url, "OUT_DIR": self.out, "TIMEOUT": "1",
             "N8N_ADMIN_EMAIL": EMAIL, "N8N_ADMIN_PASSWORD": PASSWORD}
        e.update(env)
        e = {k: v for k, v in e.items() if v is not None}
        p = subprocess.run([sys.executable, RUNNER], env=e, capture_output=True, text=True, timeout=120)
        return p.returncode, p.stdout + p.stderr

    def report(self):
        with open(os.path.join(self.out, "evals_report.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def test_classification_auth_and_sessions(self):
        rc, out = self.run_harness()
        self.assertEqual(rc, 1, out)
        rep = self.report()
        got = {r["name"]: (r["outcome"], r["cause"]) for r in rep["results"]}
        self.assertEqual(got, EXPECTED)
        self.assertEqual((rep["passed"], rep["quality_failed"], rep["wiring_failed"]), (4, 1, 5))
        self.assertIn("answer-quality failures (FAIL):  1", out)
        self.assertIn("wiring/setup errors     (ERROR): 5", out)
        # every wiring reason is recognisable by the acceptance suite's EVALS check
        for r in rep["results"]:
            if r["outcome"] == "wiring":
                self.assertRegex(r["reasons"][0], r"HTTP \d{3}|connection failed|unexpected:|timed out")
        # Basic sign-in on every call, unique session per case
        self.assertTrue(all(a.startswith("Basic ") for _, _, a in self.n8n.calls))
        sessions = [s for _, s, _ in self.n8n.calls]
        self.assertEqual(len(sessions), len(CASE_LIST))
        self.assertEqual(len(set(sessions)), len(sessions), "sessionId must be unique per case")
        guarded = [r["sessionId"] for r in rep["results"] if r["webhookId"] == BY_NAME["guarded_chat_safe_passes"]["webhookId"]]
        self.assertEqual(len(set(guarded)), 2, "the two guarded cases must not share chat memory")
        # the password never reaches the console or the reports
        with open(os.path.join(self.out, "evals_report.md"), encoding="utf-8") as fh:
            md = fh.read()
        js = json.dumps(rep)
        token = base64.b64encode(f"{EMAIL}:{PASSWORD}".encode()).decode()
        for text in (out, md, js):
            self.assertNotIn(PASSWORD, text)
            self.assertNotIn(token, text)
        self.assertIn("Wiring/setup errors (ERROR):** 5", md)
        # a second run never reuses a session id
        first = set(sessions)
        self.run_harness(ONLY="rag_identity")
        self.assertNotIn(self.n8n.calls[-1][1], first)

    def test_all_pass_exit_zero(self):
        rc, out = self.run_harness(ONLY="rag_identity,devhub,policypilot,guarded_chat_safe")
        self.assertEqual(rc, 0, out)
        self.assertIn("SCORE: 4/4 passed", out)

    def test_missing_or_wrong_sign_in_is_wiring(self):
        rc, out = self.run_harness(N8N_ADMIN_PASSWORD=None, ONLY="rag_identity")
        self.assertEqual(rc, 1)
        self.assertIn("WARNING: N8N_ADMIN_EMAIL / N8N_ADMIN_PASSWORD are not set", out)
        self.assertEqual(self.report()["results"][0]["cause"], "auth")
        rc, out = self.run_harness(N8N_ADMIN_PASSWORD="wrong-password-xyz", ONLY="rag_identity")
        self.assertEqual(self.report()["results"][0]["cause"], "auth")
        self.assertIn("n8n refused the lab admin sign-in", out)
        self.assertNotIn("wrong-password-xyz", out)

    def test_plain_http_to_remote_host_refused(self):
        rc, out = self.run_harness(BASE_URL="http://n8n.lab.example.com")
        self.assertEqual(rc, 2)
        self.assertIn("clear text", out)
        self.assertEqual(self.n8n.calls, [], "nothing may be sent")
        for ok in ("http://n8n:5678", "http://localhost:5678", "https://n8n.lab.example.com"):
            import importlib.util
            spec = importlib.util.spec_from_file_location("run_evals_under_test", RUNNER)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            self.assertIsNone(mod.check_base_url(ok), ok)


class CasesMatchWorkflows(unittest.TestCase):
    def setUp(self):
        if not os.path.isdir(WORKFLOWS):
            self.skipTest("n8n/backup/workflows not present (run from a repo checkout)")
        self.triggers = {}
        for path in glob.glob(os.path.join(WORKFLOWS, "*.json")):
            with open(path, encoding="utf-8") as fh:
                wf = json.load(fh)
            for node in wf.get("nodes", []):
                if node.get("type", "").endswith("chatTrigger") and node.get("webhookId"):
                    self.triggers[node["webhookId"]] = wf.get("name")

    def test_every_case_targets_a_committed_chat_trigger(self):
        for c in CASE_LIST:
            self.assertIn(c["webhookId"], self.triggers, f"{c['name']}: webhookId not in any workflow")
            self.assertEqual(c.get("agent"), self.triggers[c["webhookId"]], f"{c['name']}: agent name drifted")

    def test_case_names_unique(self):
        names = [c["name"] for c in CASE_LIST]
        self.assertEqual(len(names), len(set(names)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
