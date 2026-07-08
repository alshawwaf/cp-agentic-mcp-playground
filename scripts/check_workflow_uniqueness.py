#!/usr/bin/env python3
"""Fleet-wide uniqueness audit for the n8n import set.

Copy-pasting a workflow (or a node) silently duplicates ids that MUST be
unique once everything is active:
  - workflow ids (import upserts by id — a duplicate overwrites another flow)
  - chat-trigger / webhook `webhookId`s and REST webhook paths (n8n refuses to
    activate the second workflow: "URL path already taken")
  - node ids / names within a single workflow

Run from the repo root (CI or pre-commit):  python3 scripts/check_workflow_uniqueness.py
Exits non-zero and prints every violation if any id is duplicated.
"""
import json
import glob
import os
import sys
from collections import defaultdict

WF_DIR = os.path.join(os.path.dirname(__file__), '..', 'n8n', 'backup', 'workflows')

wf_ids = defaultdict(list)
webhook_ids = defaultdict(list)
rest_paths = defaultdict(list)
problems = []

for path in sorted(glob.glob(os.path.join(WF_DIR, '*.json'))):
    fname = os.path.basename(path)
    d = json.load(open(path))
    wf_ids[d.get('id')].append(fname)

    node_ids = defaultdict(int)
    node_names = defaultdict(int)
    for n in d.get('nodes', []):
        node_ids[n.get('id')] += 1
        node_names[n.get('name')] += 1
        wid = n.get('webhookId')
        if wid:
            webhook_ids[wid].append(f"{fname}:{n['name']}")
        if n.get('type') == 'n8n-nodes-base.webhook':
            p = n.get('parameters', {}).get('path')
            if p:
                rest_paths[p].append(f"{fname}:{n['name']}")

    for nid, cnt in node_ids.items():
        if cnt > 1:
            problems.append(f"{fname}: node id {nid!r} appears {cnt}x")
    for name, cnt in node_names.items():
        if cnt > 1:
            problems.append(f"{fname}: node name {name!r} appears {cnt}x")

for wid, files in wf_ids.items():
    if len(files) > 1:
        problems.append(f"workflow id {wid!r} duplicated across: {files}")
for wid, sites in webhook_ids.items():
    if len(sites) > 1:
        problems.append(f"webhookId {wid!r} duplicated across: {sites}")
for p, sites in rest_paths.items():
    if len(sites) > 1:
        problems.append(f"REST webhook path {p!r} duplicated across: {sites}")

if problems:
    print("UNIQUENESS VIOLATIONS:")
    for p in problems:
        print("  -", p)
    sys.exit(1)
print(f"OK — {len(wf_ids)} workflows, {len(webhook_ids)} webhook ids, all unique.")
