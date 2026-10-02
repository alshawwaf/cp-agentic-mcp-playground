#!/usr/bin/env python3
"""
Evals harness — "how do you know your agent is good?"
=====================================================

Runs a fixed, versioned set of eval cases against the deployed n8n agents in
this lab by POSTing one real chat turn to each agent's chat webhook and scoring
the answer against expected substrings. It prints a table and writes a
scorecard to evals_report.md + evals_report.json.

Why this exists
---------------
An agent that "works when I try it" is not an agent you can trust. Evals turn
that gut feel into a number you can watch over time: change a model, a prompt,
a tool, or the gateway, re-run the harness, and see whether quality moved.
Writing the eval FIRST, before you polish the agent, is the professional habit.
This is the batch, assertion-style sibling of the n8n workflow
'Nightly Agent Self-Check' (see docs/guides/Evals_Harness.md).

Two kinds of failure, reported separately
-----------------------------------------
* FAIL  (answer quality) — the agent answered, but not as the case expects.
        Look at the model, the system prompt or the tools.
* ERROR (wiring / setup) — the agent could not answer at all: sign-in refused,
        workflow not published, a service or key missing, the workflow's error
        branch replied. Fix the deployment first; these say nothing about
        answer quality.

Design
------
* Standard library ONLY (urllib): no pip, runs anywhere, including the
  `evals-run` compose one-shot (profile evals) on the lab network.
* Cases live in evals_cases.json (edit cases without touching this code).
* Talks to the webhook the n8n chat trigger exposes:
      POST {BASE_URL}/webhook/<webhookId>/chat
      body: {"action":"sendMessage","chatInput":"<prompt>","sessionId":"<id>"}
  The chat endpoints require the lab admin sign-in (HTTP Basic, the same
  N8N_ADMIN_EMAIL / N8N_ADMIN_PASSWORD you use for the n8n editor).
* Every case gets its own sessionId, unique per run, so the agents' chat
  memory never carries one case (or an earlier run) into another.

Env
---
  N8N_ADMIN_EMAIL     lab admin e-mail     (required: the chat webhooks return 401 without it)
  N8N_ADMIN_PASSWORD  lab admin password   (required; never printed)
  BASE_URL     n8n root, no trailing /webhook. Default http://n8n:5678 (inside the lab
               network). For a remote lab use https://n8n.<your-domain>: plain http is
               refused for anything but a lab service name or localhost, because the
               password would cross the network in clear text.
  CASES_FILE   path to the cases JSON.   Default ./evals_cases.json (next to this file)
  OUT_DIR      where to write reports.    Default current working directory
  SESSION_ID   prefix for the per-case session ids. Default evals
  TIMEOUT      per-request seconds.       Default 90
  ONLY         comma-separated case names to run (substring match); runs all if unset

Exit code: 0 if every case passed, 1 if any case failed or errored (CI-friendly),
2 on a usage error (missing cases file, unsafe BASE_URL).
"""
import base64
import ipaddress
import json
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))

BASE_URL = os.getenv("BASE_URL", "http://n8n:5678").strip().rstrip("/")
CASES_FILE = os.getenv("CASES_FILE", os.path.join(HERE, "evals_cases.json"))
OUT_DIR = os.getenv("OUT_DIR", os.getcwd())
SESSION_PREFIX = re.sub(r"[^A-Za-z0-9_.-]", "-", os.getenv("SESSION_ID", "").strip() or "evals")
ONLY = [s.strip() for s in os.getenv("ONLY", "").split(",") if s.strip()]
ADMIN_EMAIL = os.getenv("N8N_ADMIN_EMAIL", "").strip()
ADMIN_PASSWORD = os.getenv("N8N_ADMIN_PASSWORD", "")


def _int_env(name, default):
    try:
        return max(1, int(os.getenv(name, "") or default))
    except ValueError:
        return default


TIMEOUT = _int_env("TIMEOUT", 90)

# Replies the lab workflows send from their error branches (HTTP 200, but the
# agent never answered). Matched case-insensitively against the start of the answer.
ERROR_REPLIES = [
    (re.compile(r"^\s*I could not complete this request"), "agent-error",
     "the workflow's error branch answered (model endpoint, tool server or credential); "
     "open the execution in n8n > Executions"),
    (re.compile(r"^\s*\W*\s*I hit an error", re.I), "agent-error",
     "the workflow's error branch answered; open the execution in n8n > Executions"),
    (re.compile(r"Lakera Guard is not configured", re.I), "not-configured",
     "set LAKERA_API_KEY in .env, then run docker compose run --rm n8n-import"),
    (re.compile(r"Lakera Guard input screening did not complete", re.I), "guard-unavailable",
     "Lakera Guard did not answer; check LAKERA_API_KEY and outbound access to api.lakera.ai"),
    (re.compile(r"^\s*\{?\s*\"?message\"?\s*:\s*\"?Error in workflow", re.I), "workflow-error",
     "the workflow failed; open the execution in n8n > Executions"),
]


def redact(text):
    """Remove the admin password (and its Basic token) from any text we print or store."""
    text = str(text)
    if ADMIN_PASSWORD:
        text = text.replace(ADMIN_PASSWORD, "[redacted]")
        token = _basic_token()
        if token:
            text = text.replace(token, "[redacted]")
    return text


def _basic_token():
    if not (ADMIN_EMAIL and ADMIN_PASSWORD):
        return ""
    return base64.b64encode("{}:{}".format(ADMIN_EMAIL, ADMIN_PASSWORD).encode("utf-8")).decode("ascii")


def check_base_url(url):
    """Return an error string if credentials must not be sent to this URL."""
    u = urllib.parse.urlsplit(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        return "BASE_URL must be an http(s) URL, got {!r}".format(url)
    if u.scheme == "https":
        return None
    host = u.hostname
    if host == "localhost" or "." not in host:  # a lab service name such as n8n
        return None
    try:
        if ipaddress.ip_address(host).is_loopback:
            return None
    except ValueError:
        pass
    return ("BASE_URL {} is plain http to a remote host; the lab admin password would cross the "
            "network in clear text. Use https://n8n.<your-domain>, or run the harness inside the lab "
            "network (docker compose --profile evals run --rm evals-run).".format(url))


def load_cases(path):
    with open(path, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    cases = doc.get("cases", doc) if isinstance(doc, dict) else doc
    if not isinstance(cases, list):
        raise ValueError("cases JSON must contain a 'cases' list")
    names = [c.get("name") for c in cases]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise ValueError("duplicate case names: {}".format(", ".join(dupes)))
    for c in cases:
        if not c.get("name") or not c.get("webhookId") or not c.get("prompt"):
            raise ValueError("every case needs name, webhookId and prompt: {!r}".format(c.get("name")))
    return cases


def extract_answer(raw):
    """Pull the agent's text answer out of whatever the webhook returned.

    n8n's chat trigger (responseMode 'lastNode') returns JSON like
    {"output": "..."}; accept a few shapes and SSE frames so a tweak to the
    response node doesn't silently zero out every score.
    """
    text = raw.strip()
    if "data:" in text and "event:" in text:  # Server-Sent Events: keep the last data payload
        datas = [ln[5:].strip() for ln in text.splitlines() if ln.startswith("data:")]
        if datas:
            text = datas[-1]
    try:
        obj = json.loads(text)
    except (ValueError, TypeError):
        return raw  # not JSON: grade the raw body as-is
    if isinstance(obj, list):
        obj = obj[0] if obj else {}
    if isinstance(obj, dict):
        for key in ("output", "text", "response", "message", "answer", "data"):
            val = obj.get(key)
            if isinstance(val, str) and val.strip():
                return val
        return json.dumps(obj)
    return str(obj)


def call_agent(case, session_id):
    """Return (answer_text, wiring_error_or_None). A wiring error is (cause, reason)."""
    url = "{}/webhook/{}/chat".format(BASE_URL, urllib.parse.quote(case["webhookId"], safe="-"))
    payload = json.dumps({
        "action": "sendMessage",
        "chatInput": case["prompt"],
        "sessionId": session_id,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json, text/event-stream")
    token = _basic_token()
    if token:
        req.add_header("Authorization", "Basic " + token)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            detail = re.sub(r"\s+", " ", e.read().decode("utf-8", "replace"))[:200]
        except Exception:  # noqa: BLE001
            detail = ""
        if e.code in (401, 403):
            cause, hint = "auth", ("the chat endpoints need the lab admin sign-in: set N8N_ADMIN_EMAIL and "
                                   "N8N_ADMIN_PASSWORD (as in .env)" if not token else
                                   "n8n refused the lab admin sign-in: check N8N_ADMIN_EMAIL / N8N_ADMIN_PASSWORD")
        elif e.code == 404:
            cause, hint = "not-published", ("the workflow is not published or the webhookId changed; agents whose "
                                            "prerequisites are missing are imported unpublished (see 'requires')")
        elif e.code >= 500:
            cause, hint = "workflow-error", "the workflow failed; open the execution in n8n > Executions"
        else:
            cause, hint = "http", ""
        reason = "HTTP {} ({}){}{}".format(e.code, cause, ": " + detail if detail else "",
                                           " -> " + hint if hint else "")
        return "", (cause, redact(reason))
    except urllib.error.URLError as e:
        if isinstance(e.reason, TimeoutError):
            return "", ("timeout", "timed out after {}s connecting to {}".format(TIMEOUT, BASE_URL))
        return "", ("unreachable", redact("connection failed: {} (is BASE_URL={} reachable?)".format(e.reason, BASE_URL)))
    except TimeoutError:
        return "", ("timeout", "timed out after {}s waiting for the answer (raise TIMEOUT for slow local "
                               "models)".format(TIMEOUT))
    except Exception as e:  # noqa: BLE001 — never let one case crash the run
        return "", ("unexpected", redact("unexpected: {}".format(e)))
    answer = extract_answer(body)
    if not answer.strip():
        return "", ("empty", "HTTP 200 with an empty answer")
    for pattern, cause, hint in ERROR_REPLIES:
        if pattern.search(answer):
            first = re.sub(r"\s+", " ", answer).strip()[:160]
            return answer, (cause, redact("HTTP 200 from the agent's error branch ({}): {} -> {}".format(
                cause, first, hint)))
    return answer, None


def score(case, answer):
    """Return (passed_bool, list_of_reasons)."""
    hay = answer.lower()
    reasons = []
    for sub in case.get("expect", []):
        if sub.lower() not in hay:
            reasons.append("missing expected: {!r}".format(sub))
    any_group = case.get("expect_any", [])
    if any_group and not any(sub.lower() in hay for sub in any_group):
        reasons.append("none of expect_any present: {}".format(any_group))
    for sub in case.get("must_not", []):
        if sub.lower() in hay:
            reasons.append("forbidden substring present: {!r}".format(sub))
    return (len(reasons) == 0), reasons


def run():
    problem = check_base_url(BASE_URL)
    if problem:
        print("ERROR: " + problem, file=sys.stderr)
        return 2
    cases = load_cases(CASES_FILE)
    if ONLY:
        cases = [c for c in cases if any(o.lower() in c["name"].lower() for o in ONLY)]
    if not cases:
        print("No cases to run (check CASES_FILE / ONLY).")
        return 1

    run_id = "{}-{}".format(datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"), secrets.token_hex(3))
    print("Evals harness: {} case(s) against {} (run {})".format(len(cases), BASE_URL, run_id))
    if not _basic_token():
        print("WARNING: N8N_ADMIN_EMAIL / N8N_ADMIN_PASSWORD are not set; the chat endpoints will answer 401.")
    print("=" * 78)
    results = []
    for case in cases:
        session_id = "{}-{}-{}".format(SESSION_PREFIX, run_id, case["name"])[:120]
        started = time.time()
        answer, wiring = call_agent(case, session_id)
        elapsed = time.time() - started
        if wiring:
            outcome, cause, passed, reasons = "wiring", wiring[0], False, [wiring[1]]
        else:
            passed, reasons = score(case, answer)
            outcome, cause = ("pass", "") if passed else ("quality", "answer")
        sample = redact(re.sub(r"\s+", " ", answer or "").strip()[:160])
        results.append({
            "name": case["name"],
            "agent": case.get("agent", ""),
            "webhookId": case["webhookId"],
            "sessionId": session_id,
            "prompt": case["prompt"],
            "expect": case.get("expect", []),
            "expect_any": case.get("expect_any", []),
            "must_not": case.get("must_not", []),
            "requires": case.get("requires", []),
            "passed": passed,
            "outcome": outcome,
            "cause": cause,
            "reasons": reasons,
            "seconds": round(elapsed, 1),
            "sample": sample,
        })
        mark = {"pass": "PASS", "quality": "FAIL", "wiring": "ERROR"}[outcome]
        print("[{:<5}] {:<40} {:>5.1f}s  {}".format(mark, case["name"], elapsed,
                                                   "" if passed else "| " + "; ".join(reasons)))

    passed_n = sum(1 for r in results if r["passed"])
    quality_n = sum(1 for r in results if r["outcome"] == "quality")
    wiring_n = sum(1 for r in results if r["outcome"] == "wiring")
    total = len(results)
    print("=" * 78)
    print("SCORE: {}/{} passed".format(passed_n, total))
    print("  answer-quality failures (FAIL):  {}  the agent answered, but not as the case expects".format(quality_n))
    print("  wiring/setup errors     (ERROR): {}  the agent could not answer; fix the deployment first".format(wiring_n))
    for r in results:
        if r["outcome"] == "wiring" and r["requires"]:
            print("    {} needs: {}".format(r["name"], "; ".join(r["requires"])))

    scorecard = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "base_url": BASE_URL,
        "cases_file": os.path.abspath(CASES_FILE),
        "total": total,
        "passed": passed_n,
        "failed": total - passed_n,
        "quality_failed": quality_n,
        "wiring_failed": wiring_n,
        "results": results,
    }
    write_reports(scorecard)
    return 0 if passed_n == total else 1


def _md(text):
    return str(text).replace("|", "\\|")


def write_reports(sc):
    os.makedirs(OUT_DIR, exist_ok=True)
    json_path = os.path.join(OUT_DIR, "evals_report.json")
    md_path = os.path.join(OUT_DIR, "evals_report.md")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(sc, fh, indent=2, ensure_ascii=False)

    pct = (100.0 * sc["passed"] / sc["total"]) if sc["total"] else 0.0
    mark = {"pass": "PASS", "quality": "FAIL", "wiring": "ERROR"}
    lines = [
        "# Agent Evals Scorecard",
        "",
        "- **Run:** {} (`{}`)".format(sc["generated_at"], sc["run_id"]),
        "- **Target:** `{}`".format(sc["base_url"]),
        "- **Result:** **{}/{} passed** ({:.0f}%)".format(sc["passed"], sc["total"], pct),
        "- **Answer-quality failures (FAIL):** {} — the agent answered, but not as expected".format(sc["quality_failed"]),
        "- **Wiring/setup errors (ERROR):** {} — the agent could not answer; these say nothing about "
        "answer quality".format(sc["wiring_failed"]),
        "",
        "| Result | Case | Agent | Time | Detail |",
        "|--------|------|-------|------|--------|",
    ]
    for r in sc["results"]:
        detail = r["sample"] if r["passed"] else "; ".join(r["reasons"])
        lines.append("| {} | `{}` | {} | {}s | {} |".format(mark[r["outcome"]], r["name"], _md(r["agent"]),
                                                          r["seconds"], _md(detail)))
    lines += ["", "## Case detail", ""]
    for r in sc["results"]:
        lines.append("### {} — {}".format(mark[r["outcome"]], r["name"]))
        lines.append("")
        if r["agent"]:
            lines.append("- **agent:** {}".format(r["agent"]))
        lines.append("- **prompt:** {}".format(r["prompt"]))
        if r["expect"]:
            lines.append("- **expect (all):** {}".format(", ".join("`%s`" % s for s in r["expect"])))
        if r["expect_any"]:
            lines.append("- **expect_any (one of):** {}".format(", ".join("`%s`" % s for s in r["expect_any"])))
        if r["must_not"]:
            lines.append("- **must_not:** {}".format(", ".join("`%s`" % s for s in r["must_not"])))
        if r["requires"]:
            lines.append("- **requires:** {}".format("; ".join(r["requires"])))
        if not r["passed"]:
            kind = "wiring/setup" if r["outcome"] == "wiring" else "answer quality"
            lines.append("- **why it failed ({}):** {}".format(kind, "; ".join(r["reasons"])))
        lines.append("- **answer sample:** {}".format(r["sample"] or "(empty)"))
        lines.append("")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    print("Wrote {}".format(md_path))
    print("Wrote {}".format(json_path))


if __name__ == "__main__":
    try:
        sys.exit(run())
    except (FileNotFoundError, ValueError) as e:
        print("ERROR: {}".format(redact(e)), file=sys.stderr)
        print("Set CASES_FILE to a valid cases JSON (default: evals_cases.json next to this script).",
              file=sys.stderr)
        sys.exit(2)
