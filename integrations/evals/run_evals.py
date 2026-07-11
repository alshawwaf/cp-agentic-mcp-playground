#!/usr/bin/env python3
"""
Evals harness — "how do you know your agent is good?"
=====================================================

Runs a fixed, versioned set of eval cases against the deployed n8n agents in
this playground by POSTing one real chat turn to each agent's chat webhook and
scoring the answer against expected substrings. It prints a pass/fail table and
writes a scorecard to evals_report.md + evals_report.json.

Why this exists
---------------
A demo that "works when I try it" is not an agent you can trust. Evals turn that
gut feel into a number you can watch over time: change a model, a prompt, a tool,
or the gateway, re-run the harness, and see immediately whether quality moved.
Writing the eval FIRST — before you polish the agent — is the professional habit.
This is the batch, assertion-style sibling of the shipped
'CP Agents — Nightly Self-QA' n8n workflow (see docs/guides/Evals_Harness.md).

Design
------
* Standard library ONLY (urllib) — no pip, runs anywhere, including a compose
  one-shot on the demo network.
* Cases live in evals_cases.json (edit cases without touching this code).
* Talks to the same webhook the n8n chat trigger exposes:
      POST {BASE_URL}/webhook/<webhookId>/chat
      body: {"action":"sendMessage","chatInput":"<prompt>","sessionId":"<id>"}
  Reachable two ways (set BASE_URL):
      inside the demo network : http://n8n:5678         (default)
      from a laptop           : https://n8n.<your-domain>

Env (all optional)
------------------
  BASE_URL     agent host root, no trailing /webhook. Default http://n8n:5678
  CASES_FILE   path to the cases JSON.   Default ./evals_cases.json (next to this file)
  OUT_DIR      where to write reports.    Default current working directory
  SESSION_ID   chat session id sent with every turn. Default evals-harness
  TIMEOUT      per-request seconds.       Default 90
  ONLY         comma-separated case names to run (substring match); runs all if unset

Exit code: 0 if every case passed, 1 if any failed or errored (CI-friendly).
"""
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))

BASE_URL = os.getenv("BASE_URL", "http://n8n:5678").rstrip("/")
CASES_FILE = os.getenv("CASES_FILE", os.path.join(HERE, "evals_cases.json"))
OUT_DIR = os.getenv("OUT_DIR", os.getcwd())
SESSION_ID = os.getenv("SESSION_ID", "evals-harness")
TIMEOUT = int(os.getenv("TIMEOUT", "90"))
ONLY = [s.strip() for s in os.getenv("ONLY", "").split(",") if s.strip()]


def load_cases(path):
    with open(path, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    cases = doc.get("cases", doc) if isinstance(doc, dict) else doc
    if not isinstance(cases, list):
        raise ValueError("cases JSON must contain a 'cases' list")
    return cases


def extract_answer(raw):
    """Pull the agent's text answer out of whatever the webhook returned.

    n8n's chat trigger (responseMode 'lastNode') returns JSON like
    {"output": "..."}; be liberal — accept a few shapes and SSE frames so a
    tweak to the response node doesn't silently zero out every score.
    """
    text = raw.strip()
    # Server-Sent-Events framing: keep the last data: line's payload.
    if "data:" in text and "event:" in text:
        datas = [ln[5:].strip() for ln in text.splitlines() if ln.startswith("data:")]
        if datas:
            text = datas[-1]
    try:
        obj = json.loads(text)
    except (ValueError, TypeError):
        return raw  # not JSON — grade the raw body as-is
    if isinstance(obj, list):
        obj = obj[0] if obj else {}
    if isinstance(obj, dict):
        for key in ("output", "text", "response", "message", "answer", "data"):
            val = obj.get(key)
            if isinstance(val, str) and val.strip():
                return val
        return json.dumps(obj)
    return str(obj)


def call_agent(case):
    """Return (answer_text, error_string_or_None)."""
    url = "{}/webhook/{}/chat".format(BASE_URL, case["webhookId"])
    payload = json.dumps({
        "action": "sendMessage",
        "chatInput": case["prompt"],
        "sessionId": SESSION_ID,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json, text/event-stream")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read().decode("utf-8", "replace")
        return extract_answer(body), None
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        hint = " (404 — is the workflow ACTIVE and is the webhookId right?)" if e.code == 404 else ""
        return "", "HTTP {}{}: {}".format(e.code, hint, detail)
    except urllib.error.URLError as e:
        return "", "connection failed: {} (is BASE_URL={} reachable?)".format(e.reason, BASE_URL)
    except Exception as e:  # noqa: BLE001 — never let one case crash the run
        return "", "unexpected: {}".format(e)


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
    cases = load_cases(CASES_FILE)
    if ONLY:
        cases = [c for c in cases if any(o.lower() in c["name"].lower() for o in ONLY)]
    if not cases:
        print("No cases to run (check CASES_FILE / ONLY).")
        return 1

    print("Evals harness — {} case(s) against {}".format(len(cases), BASE_URL))
    print("=" * 72)
    results = []
    for case in cases:
        started = time.time()
        answer, err = call_agent(case)
        elapsed = time.time() - started
        if err:
            passed, reasons = False, [err]
        else:
            passed, reasons = score(case, answer)
        sample = re.sub(r"\s+", " ", (answer or err or "")).strip()[:160]
        results.append({
            "name": case["name"],
            "webhookId": case["webhookId"],
            "prompt": case["prompt"],
            "expect": case.get("expect", []),
            "expect_any": case.get("expect_any", []),
            "must_not": case.get("must_not", []),
            "passed": passed,
            "reasons": reasons,
            "seconds": round(elapsed, 1),
            "sample": sample,
        })
        mark = "PASS" if passed else "FAIL"
        print("[{}] {:<38} {:>5.1f}s  {}".format(mark, case["name"], elapsed, "" if passed else "| " + "; ".join(reasons)))

    passed_n = sum(1 for r in results if r["passed"])
    total = len(results)
    print("=" * 72)
    print("SCORE: {}/{} passed".format(passed_n, total))

    scorecard = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_url": BASE_URL,
        "cases_file": os.path.abspath(CASES_FILE),
        "total": total,
        "passed": passed_n,
        "failed": total - passed_n,
        "results": results,
    }
    write_reports(scorecard)
    return 0 if passed_n == total else 1


def write_reports(sc):
    os.makedirs(OUT_DIR, exist_ok=True)
    json_path = os.path.join(OUT_DIR, "evals_report.json")
    md_path = os.path.join(OUT_DIR, "evals_report.md")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(sc, fh, indent=2)

    pct = (100.0 * sc["passed"] / sc["total"]) if sc["total"] else 0.0
    lines = [
        "# Agent Evals Scorecard",
        "",
        "- **Run:** {}".format(sc["generated_at"]),
        "- **Target:** `{}`".format(sc["base_url"]),
        "- **Result:** **{}/{} passed** ({:.0f}%)".format(sc["passed"], sc["total"], pct),
        "",
        "| Result | Case | Time | Detail |",
        "|--------|------|------|--------|",
    ]
    for r in sc["results"]:
        mark = "PASS" if r["passed"] else "FAIL"
        detail = r["sample"] if r["passed"] else "; ".join(r["reasons"])
        detail = detail.replace("|", "\\|")
        lines.append("| {} | `{}` | {}s | {} |".format(mark, r["name"], r["seconds"], detail))
    lines += [
        "",
        "## Case detail",
        "",
    ]
    for r in sc["results"]:
        mark = "PASS" if r["passed"] else "FAIL"
        lines.append("### {} — {}".format(mark, r["name"]))
        lines.append("")
        lines.append("- **prompt:** {}".format(r["prompt"]))
        if r["expect"]:
            lines.append("- **expect (all):** {}".format(", ".join("`%s`" % s for s in r["expect"])))
        if r["expect_any"]:
            lines.append("- **expect_any (one of):** {}".format(", ".join("`%s`" % s for s in r["expect_any"])))
        if r["must_not"]:
            lines.append("- **must_not:** {}".format(", ".join("`%s`" % s for s in r["must_not"])))
        if not r["passed"]:
            lines.append("- **why it failed:** {}".format("; ".join(r["reasons"])))
        lines.append("- **answer sample:** {}".format(r["sample"] or "(empty)"))
        lines.append("")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    print("Wrote {}".format(md_path))
    print("Wrote {}".format(json_path))


if __name__ == "__main__":
    try:
        sys.exit(run())
    except FileNotFoundError as e:
        print("ERROR: {}".format(e), file=sys.stderr)
        print("Set CASES_FILE to your cases JSON (default: evals_cases.json next to this script).", file=sys.stderr)
        sys.exit(2)
