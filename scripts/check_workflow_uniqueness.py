#!/usr/bin/env python3
"""Uniqueness and reference audit for the n8n import set (n8n/backup).

Copying a workflow, a node or a credential file silently duplicates ids that must be unique once everything
is imported and published:
  - workflow ids (import upserts by id, so a duplicate overwrites another workflow) and workflow names
    (guides, evals and the naming pass find workflows by name)
  - chat-trigger and webhook `webhookId`s and REST webhook paths (n8n refuses to publish the second workflow:
    "URL path already taken")
  - node ids and node names inside one workflow
  - credential ids (import upserts credentials by id)
It also checks that every credential a node references exists in credentials_public with the same name, so a
removed or renamed credential cannot leave a workflow pointing at nothing.

Run from anywhere (CI or pre-commit):  python3 scripts/check_workflow_uniqueness.py
Exits 1 and prints every violation if any check fails. The full wiring checks live in scripts/flows/n8n_fix.py check.
"""
import glob
import json
import os
import sys
from collections import defaultdict

BACKUP = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "n8n", "backup"))
WF_DIR = os.path.join(BACKUP, "workflows")
CRED_DIR = os.path.join(BACKUP, "credentials_public")


def load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def main() -> int:
    problems = []
    creds = {}
    cred_ids = defaultdict(list)
    for path in sorted(glob.glob(os.path.join(CRED_DIR, "*.json"))):
        fname = os.path.basename(path)
        try:
            c = load(path)
        except ValueError as exc:
            problems.append(f"credentials_public/{fname}: not valid JSON ({exc})")
            continue
        if not c.get("id"):
            problems.append(f"credentials_public/{fname}: no id")
            continue
        cred_ids[c["id"]].append(fname)
        creds[c["id"]] = c

    wf_ids = defaultdict(list)
    wf_names = defaultdict(list)
    webhook_ids = defaultdict(list)
    rest_paths = defaultdict(list)
    workflows = sorted(glob.glob(os.path.join(WF_DIR, "*.json")))
    for path in workflows:
        fname = os.path.basename(path)
        try:
            d = load(path)
        except ValueError as exc:
            problems.append(f"{fname}: not valid JSON ({exc})")
            continue
        if not isinstance(d.get("id"), str) or not d["id"]:
            problems.append(f"{fname}: workflow has no id (import would create a new workflow on every deploy)")
        wf_ids[d.get("id")].append(fname)
        wf_names[d.get("name")].append(fname)

        node_ids = defaultdict(int)
        node_names = defaultdict(int)
        for n in d.get("nodes", []):
            node_ids[n.get("id")] += 1
            node_names[n.get("name")] += 1
            wid = n.get("webhookId")
            if wid:
                webhook_ids[wid].append(f"{fname}:{n['name']}")
            if n.get("type") == "n8n-nodes-base.webhook":
                p = n.get("parameters", {}).get("path")
                if p:
                    rest_paths[p].append(f"{fname}:{n['name']}")
            for ctype, ref in (n.get("credentials") or {}).items():
                cid = (ref or {}).get("id")
                if cid not in creds:
                    problems.append(f"{fname}: node {n['name']!r} uses credential {ref} that has no file in "
                                    "credentials_public")
                elif creds[cid].get("name") != ref.get("name"):
                    problems.append(f"{fname}: node {n['name']!r} names credential {cid} {ref.get('name')!r}, the "
                                    f"file says {creds[cid].get('name')!r}")

        for nid, cnt in node_ids.items():
            if cnt > 1:
                problems.append(f"{fname}: node id {nid!r} appears {cnt}x")
        for name, cnt in node_names.items():
            if cnt > 1:
                problems.append(f"{fname}: node name {name!r} appears {cnt}x")

    for wid, files in wf_ids.items():
        if len(files) > 1:
            problems.append(f"workflow id {wid!r} duplicated across: {files}")
    for name, files in wf_names.items():
        if len(files) > 1:
            problems.append(f"workflow name {name!r} duplicated across: {files}")
    for wid, sites in webhook_ids.items():
        if len(sites) > 1:
            problems.append(f"webhookId {wid!r} duplicated across: {sites}")
    for p, sites in rest_paths.items():
        if len(sites) > 1:
            problems.append(f"REST webhook path {p!r} duplicated across: {sites}")
    for cid, files in cred_ids.items():
        if len(files) > 1:
            problems.append(f"credential id {cid!r} duplicated across: {files}")

    if problems:
        print("UNIQUENESS OR REFERENCE VIOLATIONS:")
        for p in problems:
            print("  -", p)
        return 1
    print(f"OK: {len(workflows)} workflows, {len(webhook_ids)} webhook ids, {len(creds)} credentials; ids unique and "
          "every credential reference resolves.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
