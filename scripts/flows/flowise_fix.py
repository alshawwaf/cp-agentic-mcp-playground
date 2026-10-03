#!/usr/bin/env python3
"""flowise_fix.py: re-runnable transformer and checker for integrations/flowise/*.flowdata.json.

What it enforces (lab design contract, section 2 "Builder wiring"):
  * Model: every agent uses Flowise's ChatOpenAI node (chatOpenAI, the installed version) with Base Path
    http://litellm:4000/v1 and model lab-chat. The repo files carry no credential id; builders-import
    (integrations/seed_builders.py) attaches the "Lab Model (LiteLLM)" credential it creates from
    LITELLM_MASTER_KEY.
  * MCP: gateway agents send "Authorization: Bearer {{$vars.MCP_GATEWAY_TOKEN}}". The token lives in the
    Flowise variable MCP_GATEWAY_TOKEN, which builders-import keeps in sync with .env, so no token is
    written into a flow. Each gateway twin selects exactly its own server's tools (the SERVER_TOOLS lists in
    scripts/flows/langflow_fix.py), each direct twin selects every tool of its sidecar, the umbrella, Fleet
    Commander, Guarded and SOC agents use the same curated cores as Langflow, and no agent selects more
    than 128 tools.
  * Lakera Guard: the Guarded agent and the Lakera Guard screening agent are Agentflow V2 flows:
    Start -> Lakera Guard (input) -> Allowed? -> Agent -> Lakera Guard (output), with a Direct Reply for
    blocked prompts. Input screening blocks the prompt when Lakera Guard flags it or when screening cannot
    complete (no key, unreachable, rejected key). Output screening withholds a flagged answer and, when
    screening cannot complete, delivers the answer with a note. The key is the Flowise variable
    LAKERA_API_KEY. Without a key nothing is sent to Lakera.
  * Prompts are the Langflow prompts of the same agents (same tool scopes), with braces turned into
    parentheses so Flowise's prompt template does not read them as variables. The Documentation RAG and
    identity provisioning agents keep their Flowise prompts (their tools differ from Langflow's).
    Sticky notes describe the real wiring.
  * Documentation RAG: Qdrant (Vector Store output) -> Similarity Score Threshold Retriever -> Retriever
    Tool, so snippets scoring below RAG_MIN_SCORE (langflow_fix.py, 0.5 = 50 %) never reach the agent. The
    Flowise Qdrant node itself has no score threshold.
  * Node templates match the installed Flowise (no "outdated node" banner): "apply --snapshot" rebuilds
    every node from the live node definitions and regenerates the edge handles.

Commands (run from the repo root; stdlib only):
  snapshot --out DIR      Inside the Docker network (for example python:3.12-alpine on the lab network).
                          Saves the installed node definitions this script uses and compares every
                          sidecar's live tool list with SERVER_TOOLS. Env: FLOWISE_URL (default
                          http://flowise:3020), FLOWISE_API_KEY or ADMIN_EMAIL + ADMIN_PASSWORD,
                          MCP_GATEWAY_TOKEN (optional, to compare the gateway too). Writes no secrets.
  apply [--snapshot DIR]  Rewrite the flow files in place. Without --snapshot only value-level fixes run and
                          node templates are kept from the files. Running it twice gives the same files.
  check                   Validate every flow file. Exit 1 on any problem (suitable for CI).
"""
from __future__ import annotations

import argparse
import copy
import glob
import gzip
import json
import os
import re
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import langflow_fix as lf  # noqa: E402  (tool scopes, sidecar URLs and prompts are shared with Langflow)

REPO = lf.REPO
FLOW_DIR = os.path.join(REPO, "integrations", "flowise")
LANGFLOW_DIR = lf.FLOW_DIR

MODEL_BASE_URL = lf.MODEL_BASE_URL
MODEL_NAME = lf.MODEL_NAME
MODEL_CREDENTIAL = "Lab Model (LiteLLM)"       # created by integrations/seed_builders.py
MODEL_TEMPERATURE = 0.2
GATEWAY_URL = lf.GATEWAY_URL
MAX_TOOLS = lf.MAX_TOOLS
FLOWISE_VERSION = "3.1.2"

# Installed node versions (Flowise 3.1.2 lab image). "apply --snapshot" warns when the live versions differ.
NODE_VERSIONS = {
    "chatOpenAI": 8.3, "customMCP": 1.1, "toolAgent": 2, "bufferMemory": 2, "stickyNote": 2,
    "ollamaEmbedding": 2, "qdrant": 5, "retrieverTool": 3, "requestsGet": 2, "requestsPost": 2,
    "startAgentflow": 1.4, "customFunctionAgentflow": 1.1, "conditionAgentflow": 1,
    "agentAgentflow": 3.2, "directReplyAgentflow": 1, "stickyNoteAgentflow": 1,
    "similarityThresholdRetriever": 2,
}

# Documentation RAG relevance threshold (shared with n8n and Langflow). Flowise takes it in percent.
RAG_MIN_SCORE = lf.RAG_MIN_SCORE
RAG_MIN_PERCENT = round(RAG_MIN_SCORE * 100)
RAG_TOP_K = 4
RAG_THRESHOLD_ID = "similarityThresholdRetriever_0"
# Definition of Flowise's Similarity Score Threshold Retriever (packages/components/nodes/retrievers/
# SimilarityThresholdRetriever, version 2, unchanged in 3.1.2 and 3.1.4), used when apply runs without
# --snapshot. With --snapshot the live definition is used instead.
THRESHOLD_RETRIEVER_DEF = {
    "label": "Similarity Score Threshold Retriever", "name": "similarityThresholdRetriever", "version": 2,
    "type": "SimilarityThresholdRetriever", "category": "Retrievers",
    "description": "Return results based on the minimum similarity percentage",
    "baseClasses": ["SimilarityThresholdRetriever", "BaseRetriever"],
    "inputs": [
        {"label": "Vector Store", "name": "vectorStore", "type": "VectorStore"},
        {"label": "Query", "name": "query", "type": "string",
         "description": "Query to retrieve documents from retriever. If not specified, user question will be used",
         "optional": True, "acceptVariable": True},
        {"label": "Minimum Similarity Score (%)", "name": "minSimilarityScore",
         "description": "Finds results with at least this similarity score", "type": "number", "default": 80,
         "step": 1},
        {"label": "Max K", "name": "maxK", "description": "The maximum number of results to fetch",
         "type": "number", "default": 20, "step": 1, "additionalParams": True},
        {"label": "K Increment", "name": "kIncrement",
         "description": ("How much to increase K by each time. It'll fetch N results, then N + kIncrement, "
                         "then N + kIncrement * 2, etc."),
         "type": "number", "default": 2, "step": 1, "additionalParams": True},
    ],
    "outputs": [
        {"label": "Similarity Threshold Retriever", "name": "retriever",
         "baseClasses": ["SimilarityThresholdRetriever", "BaseRetriever"]},
        {"label": "Document", "name": "document",
         "description": "Array of document objects containing metadata and pageContent",
         "baseClasses": ["Document", "json"]},
        {"label": "Text", "name": "text", "description": "Concatenated string from pageContent of documents",
         "baseClasses": ["string", "json"]},
    ],
}
# Left-to-right retrieval pipeline without overlapping nodes (positions only; sizes come from the canvas).
RAG_LAYOUT = {
    "ollamaEmbedding_0": (-1140, 470), "qdrant_0": (-780, 470), RAG_THRESHOLD_ID: (-420, 470),
    "retrieverTool_0": (-60, 470), "chatOpenAI_0": (-780, -260), "bufferMemory_0": (-420, 120),
    "toolAgent_0": (320, 180),
}
RAG_LINKS = [
    ("qdrant_0", RAG_THRESHOLD_ID, "vectorStore"),
    (RAG_THRESHOLD_ID, "retrieverTool_0", "retriever"),
]

# Flowise variables the flows reference as {{$vars.NAME}}; builders-import creates them from .env.
VARIABLES = ("MCP_GATEWAY_TOKEN", "LAKERA_API_KEY", "IDP_SCIM_TOKEN", "DEVHUB_MCP_TOKEN", "PILOT_MCP_TOKEN")
EXTERNAL_TOKEN = {
    "devhub": "DEVHUB_MCP_TOKEN",
    "policypilot-management": "PILOT_MCP_TOKEN",
    "policypilot-dynamic-layer": "PILOT_MCP_TOKEN",
}
EXTERNAL_URL = {
    "devhub": "https://hub.{{DOMAIN}}/api/mcp",
    "policypilot-management": "https://policypilot.{{DOMAIN}}/mcp/",
    "policypilot-dynamic-layer": "https://policypilot.{{DOMAIN}}/mcp/",
    "security-lab": "http://vuln-mcp:3099",
}
SCIM_URL = "https://idp.{{DOMAIN}}/scim/v2/Users"

GATEWAY_TWINS = lf.GATEWAY_TWINS
DIRECT_TWINS = lf.DIRECT_TWINS
CURATED = lf.CURATED
TOOL_OWNER = lf.TOOL_OWNER
SERVER_TOOLS = lf.SERVER_TOOLS
SIDECAR_URL = lf.SIDECAR_URL
GUARDED = {"guarded-chat", "lakera-guard-screening"}       # Agentflow V2 with Lakera Guard screening
GUARDED_TOOLS = {"guarded-chat": lf.FLEET_CORE, "lakera-guard-screening": None}
NO_MCP = {"rag-cp-docs", "scim-provisioning", "lakera-guard-screening"}
OWN_PROMPT = {"rag-cp-docs", "scim-provisioning"}          # tools differ from the Langflow twin
SECRET_PLACEHOLDER = re.compile(r"__[A-Z][A-Z0-9_]*__")
CREDENTIAL_ID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")

WHITELIST_PARAM_TYPES = {
    "asyncOptions", "asyncMultiOptions", "options", "multiOptions", "array", "datagrid", "string", "number",
    "boolean", "password", "json", "code", "date", "file", "folder", "tabs", "conditionFunction",
    "timePicker", "weekDaysPicker", "monthDaysPicker", "datePicker",
}
NODE_DATA_KEYS = ("label", "version", "name", "type", "color", "hideInput", "hideOutput", "baseClasses",
                  "category", "description")


def var_ref(name: str) -> str:
    return "{{$vars.%s}}" % name


def scope_for(slug: str) -> list | None:
    """Tool names a flow selects, or None when the list is kept from the file (external endpoints)."""
    if slug in GATEWAY_TWINS:
        return list(SERVER_TOOLS[GATEWAY_TWINS[slug]])
    if slug in DIRECT_TWINS:
        return list(SERVER_TOOLS[DIRECT_TWINS[slug]])
    if slug in CURATED:
        return list(CURATED[slug])
    if slug in GUARDED:
        return list(GUARDED_TOOLS[slug]) if GUARDED_TOOLS[slug] else []
    return None


def server_config(slug: str) -> dict | None:
    if slug in GATEWAY_TWINS or slug in CURATED or slug == "guarded-chat":
        return {"url": GATEWAY_URL, "headers": {"Authorization": "Bearer " + var_ref("MCP_GATEWAY_TOKEN")}}
    if slug in DIRECT_TWINS:
        return {"url": SIDECAR_URL[DIRECT_TWINS[slug]]}
    if slug in EXTERNAL_TOKEN:
        return {"url": EXTERNAL_URL[slug], "headers": {"Authorization": "Bearer " + var_ref(EXTERNAL_TOKEN[slug])}}
    if slug == "security-lab":
        return {"url": EXTERNAL_URL[slug]}
    return None


# ─────────────────────────── Lakera Guard screening (Agentflow V2 custom functions) ───────────────────────────
# Flowise runs these in its NodeVM sandbox: $input is the user's message, $vars the Flowise variables,
# $answer (output stage) the agent's answer; require('node-fetch') is Flowise's guarded fetch.
# The code must not contain double curly braces (Flowise variable syntax).

LAKERA_COMMON = r"""
const fetch = require('node-fetch');
const vars = (typeof $vars === 'object' && $vars) ? $vars : {};
const key = String(vars.LAKERA_API_KEY || '').trim();
const url = String(vars.LAKERA_GUARD_URL || 'https://api.lakera.ai/v2/guard').trim();

async function screen(messages) {
  if (!key) {
    return { verdict: 'error', detail: 'Lakera Guard is not configured. Set LAKERA_API_KEY in .env, then run ' +
      '"docker compose run --rm builders-import" to create the LAKERA_API_KEY variable in Flowise.' };
  }
  let res;
  try {
    res = await fetch(url, {
      method: 'POST',
      headers: { 'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json' },
      body: JSON.stringify({ messages: messages, breakdown: true }),
      timeout: 20000
    });
  } catch (e) {
    return { verdict: 'error', detail: 'Lakera Guard could not be reached (' + ((e && e.name) || 'error') + ').' };
  }
  if (res.status === 401 || res.status === 403) {
    return { verdict: 'error', detail: 'Lakera Guard rejected the API key (HTTP ' + res.status + '). Check LAKERA_API_KEY.' };
  }
  if (res.status !== 200) {
    return { verdict: 'error', detail: 'Lakera Guard returned HTTP ' + res.status + '.' };
  }
  let data;
  try {
    data = await res.json();
  } catch (e) {
    return { verdict: 'error', detail: 'Lakera Guard returned a response that is not JSON.' };
  }
  if (data && data.flagged) {
    const found = [];
    for (const d of (data.breakdown || [])) {
      if (d && d.detected && d.detector_type && found.indexOf(String(d.detector_type)) < 0) {
        found.push(String(d.detector_type));
      }
    }
    found.sort();
    return { verdict: 'flagged', detail: found.join(', ') };
  }
  return { verdict: 'allowed', detail: '' };
}
"""

LAKERA_INPUT_CODE = ("""// Lakera Guard input screening. Returns ALLOWED, or the reply to show instead of running the agent.
// No key: the prompt is blocked and nothing is sent to Lakera.""" + LAKERA_COMMON + r"""
const prompt = String($input || '');
const result = await screen([{ role: 'user', content: prompt }]);
if (result.verdict === 'allowed') {
  return 'ALLOWED';
}
if (result.verdict === 'flagged') {
  const why = result.detail ? ' (' + result.detail + ')' : '';
  return 'Blocked by Lakera Guard (input). Your message tripped a security policy' + why +
    ', so it was not sent to the agent. Rephrase it and try again.';
}
return 'Lakera Guard input screening did not complete, so the agent did not run. ' + result.detail;
""").lstrip()

LAKERA_OUTPUT_CODE = ("""// Lakera Guard output screening. Returns the answer, the answer with a note when screening could not
// complete, or a notice that a flagged answer was withheld.""" + LAKERA_COMMON + r"""
const prompt = String($input || '');
const answer = String((typeof $answer === 'undefined' || $answer === null) ? '' : $answer);
const result = await screen([{ role: 'user', content: prompt }, { role: 'assistant', content: answer }]);
if (result.verdict === 'allowed') {
  return answer;
}
if (result.verdict === 'flagged') {
  const why = result.detail ? ' (' + result.detail + ')' : '';
  return 'Blocked by Lakera Guard (output). The agent\'s answer tripped a security policy' + why +
    ' and was withheld.';
}
return answer + '\n\n---\nLakera Guard output screening was skipped: ' + result.detail;
""").lstrip()

ALLOWED_TOKEN = "ALLOWED"


# ─────────────────────────── prompts, descriptions, sticky notes ───────────────────────────

def langflow_prompt(slug: str) -> str:
    path = os.path.join(LANGFLOW_DIR, f"{slug}.flow.json")
    with open(path, encoding="utf-8") as fh:
        flow = json.load(fh)
    for n in flow["data"]["nodes"]:
        if n["data"].get("type") in ("Agent", "ToolCallingAgent"):
            t = n["data"]["node"]["template"]
            return (t.get("system_prompt") or t.get("system_message") or {}).get("value") or ""
    raise ValueError(f"{path}: no agent node")


def flowise_safe(text: str) -> str:
    """Flowise's ChatPromptTemplate reads {x} as a variable and Flowise reads {{x}} as a node reference."""
    text = re.sub(r"\{\{\s*([^{}]*?)\s*\}\}", r"(\1)", text)
    return text.replace("{", "(").replace("}", ")")


FLOWISE_PROMPT_FIXES = [
    ("- The corpus is clearly-marked DEMO content, not official documentation — if asked, say so.",
     "- The corpus is a small set of sample snippets written for this lab, not official Check Point "
     "documentation; if asked, say so."),
]


def expected_prompt(slug: str, current: str) -> str:
    if slug in OWN_PROMPT:
        text = current.lstrip("=")
        for old, new in FLOWISE_PROMPT_FIXES:
            text = text.replace(old, new)
        return flowise_safe(text)
    return flowise_safe(langflow_prompt(slug))


def description(slug: str, count: int | None) -> str:
    text = lf.description_for(slug, count) or ""
    return (text.replace("the LAKERA_API_KEY global variable", "the Flowise variable LAKERA_API_KEY")
                .replace("the IDP_SCIM_TOKEN global variable", "the Flowise variable IDP_SCIM_TOKEN"))


MODEL_NOTE = (f"Model: {MODEL_NAME} through LiteLLM (credential \"{MODEL_CREDENTIAL}\", Base Path {MODEL_BASE_URL} "
              "under Additional Parameters). The Model Name list only knows OpenAI model names, so it can look "
              f"empty: leave it, the agent still uses {MODEL_NAME}.")


def sticky_note(slug: str, count: int | None) -> str:
    lines = [description(slug, count).replace(" " + lf.model_note(), "")]
    if slug in GATEWAY_TWINS or slug in CURATED or slug == "guarded-chat":
        lines.append("MCP: Custom MCP node -> http://mcp-gateway:8080/mcp with Authorization "
                     "\"Bearer {{$vars.MCP_GATEWAY_TOKEN}}\". builders-import keeps the Flowise variable "
                     "MCP_GATEWAY_TOKEN (Variables page) in sync with .env.")
        lines.append(f"Available Actions: {count} tools selected. Refresh lists all "
                     f"{len(TOOL_OWNER)} gateway tools; keep the selection at {MAX_TOOLS} or fewer.")
    elif slug in DIRECT_TWINS:
        lines.append(f"MCP: Custom MCP node -> {SIDECAR_URL[DIRECT_TWINS[slug]]} (no gateway, no token). "
                     f"Available Actions: all {count} tools of the server.")
    elif slug in EXTERNAL_TOKEN:
        var = EXTERNAL_TOKEN[slug]
        lines.append(f"MCP: {EXTERNAL_URL[slug]} with Authorization \"Bearer {{{{$vars.{var}}}}}\". Set DOMAIN "
                     f"and {var} in .env and re-run builders-import; until then this agent cannot connect.")
    elif slug == "security-lab":
        lines.append("MCP: http://vuln-mcp:3099, the simulated vulnerable server. It runs only with the "
                     "security-lab profile: docker compose --profile security-lab up -d vuln-mcp. Until then "
                     "the agent has no tools and answers with a connection error.")
    elif slug == "scim-provisioning":
        lines.append("Tools: SCIM_Create_User (POST) and SCIM_List_Users (GET) on https://idp.<DOMAIN>/scim/v2/Users "
                     "with Authorization \"Bearer {{$vars.IDP_SCIM_TOKEN}}\". Set DOMAIN and IDP_SCIM_TOKEN in "
                     ".env and re-run builders-import.")
    elif slug == "rag-cp-docs":
        lines.append("Retriever: Ollama nomic-embed-text embeddings (http://ollama-cpu:11434) and the Qdrant "
                     "collection cp_docs (http://qdrant:6333), filled by the rag-ingest job. The Similarity Score "
                     f"Threshold Retriever passes on only snippets scoring at least {RAG_MIN_PERCENT} % "
                     f"(RAG_MIN_SCORE {RAG_MIN_SCORE:g}).")
    if slug in GUARDED:
        lines.append("Lakera Guard: \"Lakera Guard (input)\" screens every prompt before the agent runs and "
                     "\"Lakera Guard (output)\" screens every answer. No key, an unreachable service or a "
                     "rejected key blocks the prompt; a failed output check delivers the answer with a note. "
                     "Key: Flowise variable LAKERA_API_KEY (from LAKERA_API_KEY in .env).")
    lines.append(MODEL_NOTE)
    return "\n\n".join(lines)


# ─────────────────────────── node templates ───────────────────────────

def load_defs(snapshot: str | None) -> dict:
    if not snapshot:
        return {}
    defs = {}
    for path in glob.glob(os.path.join(snapshot, "nodes", "*.json")):
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        defs[d["name"]] = d
    missing = sorted(set(NODE_VERSIONS) - set(defs))
    if missing:
        raise SystemExit(f"apply: snapshot {snapshot} lacks node definitions {missing}; run snapshot again")
    for name, version in NODE_VERSIONS.items():
        if defs[name].get("version") != version:
            print(f"apply: NOTE installed {name} is version {defs[name].get('version')}, NODE_VERSIONS says "
                  f"{version}; update NODE_VERSIONS in {os.path.basename(__file__)}")
    return defs


def _show_hide(params: list, inputs: dict) -> list:
    """Port of the Flowise UI's display evaluation (show/hide conditions) for the initial node state."""
    values = dict(inputs)
    for p in params:
        if p.get("default") is not None and values.get(p["name"]) in (None, ""):
            values.setdefault(p["name"], p["default"])
    for p in params:
        p["display"] = True
        for mode in ("show", "hide"):
            for field, want in (p.get(mode) or {}).items():
                have = values.get(field.replace("$index", "0"), "")
                if isinstance(want, list):
                    hit = have in want
                elif isinstance(want, str):
                    hit = isinstance(have, str) and (want == have or bool(re.search(want, have)))
                else:
                    hit = want == have
                if (mode == "show" and not hit) or (mode == "hide" and hit):
                    p["display"] = False
    return params


def init_node(defn: dict, node_id: str, agentflow: bool) -> dict:
    """Port of the Flowise UI's initNode(): the node data a freshly dropped node gets."""
    d = copy.deepcopy(defn)
    anchors, params = [], []
    for inp in d.get("inputs") or []:
        item = {**inp, "id": f"{node_id}-input-{inp['name']}-{inp['type']}"}
        (params if inp["type"] in WHITELIST_PARAM_TYPES else anchors).append(item)
    if d.get("credential"):
        params.insert(0, {**d["credential"], "id": f"{node_id}-input-{d['credential']['name']}-{d['credential']['type']}"})
    # the UI uses `default || ""`, so falsy defaults become ""
    defaults = {inp["name"]: (inp.get("default") or "") for inp in d.get("inputs") or []}
    if agentflow:
        if d.get("hideOutput"):
            out_anchors = []
        elif d.get("outputs"):
            out_anchors = [{"id": f"{node_id}-output-{i}", "label": i, "name": i,
                            "description": o.get("description", "")} for i, o in enumerate(d["outputs"])]
        else:
            out_anchors = [{"id": f"{node_id}-output-{d['name']}", "label": d["label"], "name": d["name"]}]
        outputs = {d["name"]: ""} if d.get("outputs") else {}
    else:
        if d.get("outputs"):
            opts = []
            for o in d["outputs"]:
                bc = o.get("baseClasses") or []
                opts.append({"id": f"{node_id}-output-{o['name']}-{'|'.join(bc)}", "name": o["name"],
                             "label": o["label"], "description": o.get("description", ""),
                             "type": " | ".join(bc)})
            out_anchors = [{"name": "output", "label": "Output", "type": "options",
                            "description": d["outputs"][0].get("description", ""), "options": opts,
                            "default": d["outputs"][0]["name"]}]
            outputs = {"output": d["outputs"][0]["name"]}
        else:
            out_anchors = [{"id": f"{node_id}-output-{d['name']}-{'|'.join(d['baseClasses'])}", "name": d["name"],
                            "label": d["type"], "description": d.get("description", ""),
                            "type": " | ".join(d["baseClasses"])}]
            outputs = {}
    data = {"id": node_id}
    for k in NODE_DATA_KEYS:
        if d.get(k) is not None:
            data[k] = d[k]
    data["inputParams"] = _show_hide(params, defaults)
    data["inputAnchors"] = _show_hide(anchors, defaults)
    data["inputs"] = defaults
    data["outputAnchors"] = out_anchors
    data["outputs"] = outputs
    data["selected"] = False
    if d.get("credential"):
        data["credential"] = ""
    return data


def rebuild(node: dict, defs: dict, agentflow: bool) -> None:
    """Refresh one wrapper node's data from the live definition, keeping its inputs and output choice."""
    old = node["data"]
    defn = defs.get(old["name"])
    if not defn:
        return
    new = init_node(defn, node["id"], agentflow)
    for k, v in (old.get("inputs") or {}).items():
        if k in new["inputs"] or k.endswith("Config") or k == "credential":
            new["inputs"][k] = v
    for k, v in (old.get("outputs") or {}).items():
        if k in new["outputs"]:
            new["outputs"][k] = v
    if agentflow and old.get("label"):
        new["label"] = old["label"]
    node["data"] = new


def wrapper(node_id: str, data: dict, x: float, y: float, agentflow: bool, width=300, height=None) -> dict:
    w = {"id": node_id, "position": {"x": x, "y": y}, "type": "agentFlow" if agentflow else "customNode",
         "data": data, "width": width, "height": height or (70 if agentflow else 500), "selected": False,
         "positionAbsolute": {"x": x, "y": y}, "dragging": False}
    if data.get("name") in ("stickyNote", "stickyNoteAgentflow"):
        w["type"] = "stickyNote"
    return w


def nodes_by_name(flow: dict, name: str) -> list:
    return [n for n in flow["nodes"] if n["data"].get("name") == name]


def is_agentflow(flow: dict) -> bool:
    return any(n.get("type") == "agentFlow" for n in flow.get("nodes", []))


# ─────────────────────────── chatflow edges ───────────────────────────

def source_handle(node: dict) -> str:
    anchors = node["data"].get("outputAnchors") or []
    if not anchors:
        raise ValueError(f"{node['id']} has no output anchor")
    a = anchors[0]
    if a.get("type") == "options":
        choice = (node["data"].get("outputs") or {}).get("output") or a.get("default")
        for o in a["options"]:
            if o["name"] == choice:
                return o["id"]
        raise ValueError(f"{node['id']}: output {choice} not found")
    return a["id"]


def target_handle(node: dict, field: str) -> str:
    for a in node["data"].get("inputAnchors") or []:
        if a["name"] == field:
            return a["id"]
    raise ValueError(f"{node['id']} has no input anchor {field}")


def chat_edge(src: dict, tgt: dict, field: str) -> dict:
    sh, th = source_handle(src), target_handle(tgt, field)
    return {"source": src["id"], "sourceHandle": sh, "target": tgt["id"], "targetHandle": th,
            "type": "buttonedge", "id": f"{src['id']}-{sh}-{tgt['id']}-{th}"}


def chat_links(flow: dict) -> list:
    """(source id, target id, target field) for every chatflow edge, read from the handles."""
    links = []
    for e in flow["edges"]:
        field = e["targetHandle"][len(e["target"]) + len("-input-"):].rsplit("-", 1)[0]
        links.append((e["source"], e["target"], field))
    return links


def rebuild_chat_edges(flow: dict, links: list) -> None:
    by_id = {n["id"]: n for n in flow["nodes"]}
    flow["edges"] = [chat_edge(by_id[s], by_id[t], f) for s, t, f in links]


# ─────────────────────────── value-level fixes ───────────────────────────

def fix_model_inputs(inputs: dict) -> None:
    inputs["modelName"] = MODEL_NAME
    inputs["basepath"] = MODEL_BASE_URL
    inputs["streaming"] = True
    inputs["temperature"] = MODEL_TEMPERATURE
    inputs["credential"] = ""
    inputs.pop("FLOWISE_CREDENTIAL_ID", None)


def fix_chat_model(node: dict) -> None:
    d = node["data"]
    d["credential"] = ""
    fix_model_inputs(d["inputs"])


def actions_json(tools: list) -> str:
    return json.dumps(tools)


def fix_mcp_inputs(slug: str, inputs: dict) -> None:
    cfg = server_config(slug)
    if cfg is not None:
        inputs["mcpServerConfig"] = json.dumps(cfg, indent=4)
    scope = scope_for(slug)
    if scope is not None:
        inputs["mcpActions"] = actions_json(scope)


def mcp_tools(inputs: dict) -> list:
    raw = inputs.get("mcpActions") or "[]"
    return raw if isinstance(raw, list) else json.loads(raw)


def fix_request_tools(flow: dict) -> None:
    headers_get = {"Authorization": "Bearer " + var_ref("IDP_SCIM_TOKEN"), "Accept": "application/scim+json"}
    headers_post = {"Authorization": "Bearer " + var_ref("IDP_SCIM_TOKEN"), "Content-Type": "application/scim+json"}
    for n in nodes_by_name(flow, "requestsPost"):
        n["data"]["inputs"]["requestsPostUrl"] = SCIM_URL
        n["data"]["inputs"]["requestsPostHeaders"] = json.dumps(headers_post, indent=4)
    for n in nodes_by_name(flow, "requestsGet"):
        n["data"]["inputs"]["requestsGetUrl"] = SCIM_URL
        n["data"]["inputs"]["requestsGetHeaders"] = json.dumps(headers_get, indent=4)


def fix_rag(flow: dict, defs: dict, links: list) -> list:
    """Qdrant (Vector Store output) -> Similarity Score Threshold Retriever -> Retriever Tool."""
    by_id = {n["id"]: n for n in flow["nodes"]}
    if RAG_THRESHOLD_ID not in by_id:
        data = init_node(defs.get("similarityThresholdRetriever") or THRESHOLD_RETRIEVER_DEF, RAG_THRESHOLD_ID,
                         agentflow=False)
        node = wrapper(RAG_THRESHOLD_ID, data, *RAG_LAYOUT[RAG_THRESHOLD_ID], agentflow=False, height=480)
        flow["nodes"].insert(next(i for i, n in enumerate(flow["nodes"]) if n["id"] == "qdrant_0") + 1, node)
        by_id[RAG_THRESHOLD_ID] = node
    for nid, (x, y) in RAG_LAYOUT.items():
        by_id[nid]["position"] = {"x": x, "y": y}
        by_id[nid]["positionAbsolute"] = {"x": x, "y": y}
    by_id["qdrant_0"]["data"]["outputs"]["output"] = "vectorStore"
    by_id["qdrant_0"]["data"]["inputs"]["topK"] = RAG_TOP_K
    by_id[RAG_THRESHOLD_ID]["data"]["inputs"].update({
        "vectorStore": "{{qdrant_0.data.instance}}", "query": "", "minSimilarityScore": RAG_MIN_PERCENT,
        "maxK": RAG_TOP_K, "kIncrement": RAG_TOP_K})
    by_id["retrieverTool_0"]["data"]["inputs"]["retriever"] = "{{%s.data.instance}}" % RAG_THRESHOLD_ID
    keep = [link for link in links if link[1] not in ("retrieverTool_0", RAG_THRESHOLD_ID)]
    return keep + RAG_LINKS


def fix_chatflow(slug: str, flow: dict, defs: dict) -> None:
    links = chat_links(flow)
    if defs:
        for n in flow["nodes"]:
            rebuild(n, defs, agentflow=False)
    if slug == "rag-cp-docs":
        links = fix_rag(flow, defs, links)
    for n in nodes_by_name(flow, "chatOpenAI"):
        fix_chat_model(n)
    for n in nodes_by_name(flow, "customMCP"):
        fix_mcp_inputs(slug, n["data"]["inputs"])
    if slug == "scim-provisioning":
        fix_request_tools(flow)
    for n in nodes_by_name(flow, "toolAgent"):
        n["data"]["inputs"]["systemMessage"] = expected_prompt(slug, n["data"]["inputs"].get("systemMessage", ""))
    count = None
    mcps = nodes_by_name(flow, "customMCP")
    if mcps:
        count = len(mcp_tools(mcps[0]["data"]["inputs"]))
    for n in nodes_by_name(flow, "stickyNote"):
        n["type"] = "stickyNote"            # the canvas draws it as a note, not as a node card
        n["data"]["inputs"]["note"] = sticky_note(slug, count)
    rebuild_chat_edges(flow, links)


# ─────────────────────────── guarded agents (Agentflow V2) ───────────────────────────

GUARD_LAYOUT = {
    "startAgentflow_0": (-120, 80),
    "customFunctionAgentflow_0": (40, 80),
    "conditionAgentflow_0": (300, 74),
    "agentAgentflow_0": (520, -20),
    "customFunctionAgentflow_1": (780, -20),
    "directReplyAgentflow_0": (520, 190),
    "stickyNoteAgentflow_0": (-120, -260),
}
GUARD_LABELS = {
    "customFunctionAgentflow_0": "Lakera Guard (input)",
    "conditionAgentflow_0": "Allowed?",
    "customFunctionAgentflow_1": "Lakera Guard (output)",
    "directReplyAgentflow_0": "Blocked (input)",
}
GUARD_EDGES = [
    ("startAgentflow_0", None, "customFunctionAgentflow_0"),
    ("customFunctionAgentflow_0", None, "conditionAgentflow_0"),
    ("conditionAgentflow_0", 0, "agentAgentflow_0"),
    ("conditionAgentflow_0", 1, "directReplyAgentflow_0"),
    ("agentAgentflow_0", None, "customFunctionAgentflow_1"),
]


def agent_label(slug: str) -> str:
    return "Guarded Agent" if slug == "guarded-chat" else "Wellness Agent"


def model_config(defs: dict, old: dict | None) -> dict:
    if old:
        cfg = dict(old)
    else:
        cfg = {"credential": ""}
        for inp in defs["chatOpenAI"]["inputs"]:
            if inp["type"] in WHITELIST_PARAM_TYPES:
                cfg[inp["name"]] = inp.get("default") if inp.get("default") else ""
    fix_model_inputs(cfg)
    cfg["agentModel"] = "chatOpenAI"
    return cfg


def guard_tools(slug: str) -> list | str:
    scope = scope_for(slug)
    if not scope:
        return ""
    cfg = server_config(slug)
    tool = {"agentSelectedTool": "customMCP", "agentSelectedToolRequiresHumanInput": "",
            "agentSelectedToolConfig": {"mcpServerConfig": json.dumps(cfg, indent=4),
                                        "mcpActions": actions_json(scope), "agentSelectedTool": "customMCP"}}
    return [tool]


def fix_guard_values(slug: str, flow: dict, prompt_source: str) -> None:
    by_id = {n["id"]: n for n in flow["nodes"]}
    by_id["customFunctionAgentflow_0"]["data"]["inputs"].update({
        "customFunctionInputVariables": "",
        "customFunctionJavascriptFunction": LAKERA_INPUT_CODE,
        "customFunctionUpdateState": "",
    })
    by_id["customFunctionAgentflow_1"]["data"]["inputs"].update({
        "customFunctionInputVariables": [{"variableName": "answer", "variableValue": "{{ agentAgentflow_0 }}"}],
        "customFunctionJavascriptFunction": LAKERA_OUTPUT_CODE,
        "customFunctionUpdateState": "",
    })
    by_id["conditionAgentflow_0"]["data"]["inputs"]["conditions"] = [
        {"type": "string", "value1": "{{ customFunctionAgentflow_0 }}", "operation": "equal",
         "value2": ALLOWED_TOKEN}]
    by_id["directReplyAgentflow_0"]["data"]["inputs"]["directReplyMessage"] = "{{ customFunctionAgentflow_0 }}"
    agent = by_id["agentAgentflow_0"]["data"]
    agent["inputs"]["agentMessages"] = [{"role": "system", "content": expected_prompt(slug, prompt_source)}]
    agent["inputs"]["agentTools"] = guard_tools(slug)
    agent["inputs"]["agentEnableMemory"] = True
    agent["inputs"]["agentMemoryType"] = "allMessages"
    agent["inputs"]["agentUserMessage"] = ""
    agent["inputs"]["agentReturnResponseAs"] = "userMessage"
    for nid, label in GUARD_LABELS.items():
        by_id[nid]["data"]["label"] = label
    agent["label"] = agent_label(slug)
    count = len(scope_for(slug) or [])
    by_id["stickyNoteAgentflow_0"]["data"]["inputs"]["note"] = sticky_note(slug, count if count else None)


def guard_edges(flow: dict) -> list:
    by_id = {n["id"]: n for n in flow["nodes"]}
    edges = []
    for src, out, tgt in GUARD_EDGES:
        s, t = by_id[src]["data"], by_id[tgt]["data"]
        sh = f"{src}-output-{out}" if out is not None else f"{src}-output-{s['name']}"
        e = {"source": src, "sourceHandle": sh, "target": tgt, "targetHandle": tgt,
             "data": {"sourceColor": s.get("color", ""), "targetColor": t.get("color", ""),
                      "isHumanInput": False},
             "type": "agentFlow", "id": f"{src}-{sh}-{tgt}-{tgt}"}
        if out is not None:
            e["data"]["edgeLabel"] = str(out)
        edges.append(e)
    return edges


def build_guard_flow(slug: str, old: dict, defs: dict) -> dict:
    """Convert the old chatflow (or refresh the agentflow) into the Lakera Guard agentflow."""
    if is_agentflow(old):
        agent = next(n for n in old["nodes"] if n["id"] == "agentAgentflow_0")
        prompt_source = ""
        old_cfg = agent["data"]["inputs"].get("agentModelConfig")
        flow = old
        if defs:
            for n in flow["nodes"]:
                rebuild(n, defs, agentflow=True)
    else:
        if not defs:
            raise SystemExit(f"apply: {slug} is still a chatflow; run apply with --snapshot to convert it")
        prompt_source, old_cfg = "", None
        flow = {"nodes": [], "edges": [], "viewport": {"x": 160, "y": 260, "zoom": 0.8}}
        kinds = {"startAgentflow_0": "startAgentflow", "customFunctionAgentflow_0": "customFunctionAgentflow",
                 "conditionAgentflow_0": "conditionAgentflow", "agentAgentflow_0": "agentAgentflow",
                 "customFunctionAgentflow_1": "customFunctionAgentflow",
                 "directReplyAgentflow_0": "directReplyAgentflow", "stickyNoteAgentflow_0": "stickyNoteAgentflow"}
        for nid, kind in kinds.items():
            x, y = GUARD_LAYOUT[nid]
            data = init_node(defs[kind], nid, agentflow=True)
            w = wrapper(nid, data, x, y, agentflow=True, width=300 if kind == "stickyNoteAgentflow" else 200,
                        height=220 if kind == "stickyNoteAgentflow" else 70)
            flow["nodes"].append(w)
        start = next(n for n in flow["nodes"] if n["id"] == "startAgentflow_0")["data"]["inputs"]
        start["startInputType"] = "chatInput"
        agent = next(n for n in flow["nodes"] if n["id"] == "agentAgentflow_0")["data"]["inputs"]
        agent["agentModel"] = "chatOpenAI"
    agent_node = next(n for n in flow["nodes"] if n["id"] == "agentAgentflow_0")["data"]
    agent_node["inputs"]["agentModel"] = "chatOpenAI"
    agent_node["inputs"]["agentModelConfig"] = model_config(defs, old_cfg)
    fix_guard_values(slug, flow, prompt_source)
    flow["edges"] = guard_edges(flow)
    return flow


# ─────────────────────────── files ───────────────────────────

def load_flows() -> dict:
    flows = {}
    for path in sorted(glob.glob(os.path.join(FLOW_DIR, "*.flowdata.json"))):
        slug = os.path.basename(path)[: -len(".flowdata.json")]
        with open(path, encoding="utf-8") as fh:
            flows[slug] = json.load(fh)
    return flows


def save_flow(slug: str, flow: dict) -> None:
    path = os.path.join(FLOW_DIR, f"{slug}.flowdata.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(flow, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def cmd_apply(args) -> int:
    defs = load_defs(args.snapshot)
    flows = load_flows()
    for slug, flow in flows.items():
        if slug in GUARDED:
            flow = build_guard_flow(slug, flow, defs)
        else:
            fix_chatflow(slug, flow, defs)
        save_flow(slug, flow)
    print(f"apply: {len(flows)} flows written ({'templates refreshed from ' + args.snapshot if defs else 'values only'}).")
    return 0


# ─────────────────────────── check ───────────────────────────

def _node_problems(flow: dict) -> list:
    problems = []
    for n in flow["nodes"]:
        d = n["data"]
        name = d.get("name")
        if name in NODE_VERSIONS and d.get("version") != NODE_VERSIONS[name]:
            problems.append(f"{n['id']}: {name} version {d.get('version')}, installed {NODE_VERSIONS[name]}")
        if d.get("credential"):
            problems.append(f"{n['id']}: carries a credential id (the seeder attaches credentials)")
    return problems


def _model_problems(where: str, inputs: dict) -> list:
    problems = []
    if inputs.get("basepath") != MODEL_BASE_URL:
        problems.append(f"{where}: Base Path is not {MODEL_BASE_URL}")
    if inputs.get("modelName") != MODEL_NAME:
        problems.append(f"{where}: model is {inputs.get('modelName')!r}, expected {MODEL_NAME}")
    if inputs.get("credential") or inputs.get("FLOWISE_CREDENTIAL_ID"):
        problems.append(f"{where}: carries a credential id")
    return problems


def _mcp_problems(slug: str, where: str, inputs: dict) -> list:
    problems = []
    try:
        cfg = json.loads(inputs.get("mcpServerConfig") or "")
        tools = mcp_tools(inputs)
    except ValueError as exc:
        return [f"{where}: MCP config or actions are not JSON ({exc})"]
    want_cfg = server_config(slug)
    if want_cfg is not None and cfg != want_cfg:
        problems.append(f"{where}: MCP server config is {cfg}, expected {want_cfg}")
    scope = scope_for(slug)
    if scope is not None and tools != scope:
        problems.append(f"{where}: selects {len(tools)} tools, expected the {len(scope)}-tool scope in this script")
    if not tools:
        problems.append(f"{where}: selects no tools (Flowise binds only the selected actions)")
    if len(tools) > MAX_TOOLS:
        problems.append(f"{where}: selects {len(tools)} tools (limit {MAX_TOOLS})")
    if cfg.get("url") == GATEWAY_URL or slug in DIRECT_TWINS:
        unknown = [t for t in tools if t not in TOOL_OWNER]
        if unknown:
            problems.append(f"{where}: selects tools no server provides: {unknown[:5]}")
    return problems


def _prompt_problems(slug: str, where: str, prompt: str) -> list:
    problems = []
    if re.search(r"[{}]", prompt):
        problems.append(f"{where}: prompt contains braces (Flowise reads them as template variables)")
    if prompt.startswith("="):
        problems.append(f"{where}: prompt starts with '='")
    hit = lf.UNPROFESSIONAL.search(prompt)
    if hit:
        problems.append(f"{where}: prompt contains unprofessional text ({hit.group(0)!r})")
    if slug not in OWN_PROMPT:
        try:
            if prompt != flowise_safe(langflow_prompt(slug)):
                problems.append(f"{where}: prompt differs from the Langflow twin (run apply)")
        except (OSError, ValueError) as exc:
            problems.append(f"{where}: cannot read the Langflow prompt ({exc})")
    return problems


def _rag_problems(flow: dict) -> list:
    problems = []
    gates = nodes_by_name(flow, "similarityThresholdRetriever")
    if len(gates) != 1 or gates[0]["id"] != RAG_THRESHOLD_ID:
        return [f"expected one Similarity Score Threshold Retriever ({RAG_THRESHOLD_ID}), found {len(gates)}"]
    inputs = gates[0]["data"]["inputs"]
    if inputs.get("minSimilarityScore") != RAG_MIN_PERCENT:
        problems.append(f"{RAG_THRESHOLD_ID}: Minimum Similarity Score is {inputs.get('minSimilarityScore')!r}, "
                        f"expected {RAG_MIN_PERCENT} (RAG_MIN_SCORE {RAG_MIN_SCORE:g})")
    if inputs.get("maxK") != RAG_TOP_K:
        problems.append(f"{RAG_THRESHOLD_ID}: Max K is {inputs.get('maxK')!r}, expected {RAG_TOP_K}")
    links = set(chat_links(flow))
    for link in RAG_LINKS:
        if link not in links:
            problems.append(f"missing edge {link[0]} -> {link[1]}.{link[2]}")
    if any(t == "retrieverTool_0" and s != RAG_THRESHOLD_ID for s, t, _f in links):
        problems.append("the Retriever Tool must read from the threshold retriever only")
    return problems


def check_chatflow(slug: str, flow: dict) -> list:
    problems = _node_problems(flow)
    by_id = {n["id"]: n for n in flow["nodes"]}
    models = nodes_by_name(flow, "chatOpenAI")
    if len(models) != 1:
        problems.append(f"expected one chatOpenAI node, found {len(models)}")
    for n in models:
        problems += _model_problems(n["id"], n["data"]["inputs"])
    mcps = nodes_by_name(flow, "customMCP")
    if slug in NO_MCP and mcps:
        problems.append("unexpected Custom MCP node")
    if slug not in NO_MCP and len(mcps) != 1:
        problems.append(f"expected one Custom MCP node, found {len(mcps)}")
    for n in mcps:
        problems += _mcp_problems(slug, n["id"], n["data"]["inputs"])
    agents = nodes_by_name(flow, "toolAgent")
    if len(agents) != 1:
        problems.append(f"expected one Tool Agent, found {len(agents)}")
    for n in agents:
        problems += _prompt_problems(slug, n["id"], n["data"]["inputs"].get("systemMessage", ""))
    for e in flow["edges"]:
        try:
            want = chat_edge(by_id[e["source"]], by_id[e["target"]],
                             e["targetHandle"][len(e["target"]) + len("-input-"):].rsplit("-", 1)[0])
        except (KeyError, ValueError) as exc:
            problems.append(f"edge {e.get('id', '?')[:60]}: {exc}")
            continue
        if want != {k: e.get(k) for k in want}:
            problems.append(f"edge {e['source']} -> {e['target']}: handles do not match the node templates")
    if slug == "rag-cp-docs":
        problems += _rag_problems(flow)
    if slug == "scim-provisioning":
        for n in nodes_by_name(flow, "requestsPost") + nodes_by_name(flow, "requestsGet"):
            raw = json.dumps(n["data"]["inputs"])
            if var_ref("IDP_SCIM_TOKEN") not in raw or SCIM_URL not in raw:
                problems.append(f"{n['id']}: SCIM URL or {var_ref('IDP_SCIM_TOKEN')} header missing")
    return problems


def check_guarded(slug: str, flow: dict) -> list:
    if not is_agentflow(flow):
        return ["not an Agentflow V2 flow with Lakera Guard screening"]
    problems = _node_problems(flow)
    by_id = {n["id"]: n for n in flow["nodes"]}
    want_ids = set(GUARD_LAYOUT)
    if set(by_id) != want_ids:
        problems.append(f"nodes {sorted(by_id)} differ from {sorted(want_ids)}")
        return problems
    if by_id["customFunctionAgentflow_0"]["data"]["inputs"].get("customFunctionJavascriptFunction") != LAKERA_INPUT_CODE:
        problems.append("Lakera Guard (input) code differs from this script")
    if by_id["customFunctionAgentflow_1"]["data"]["inputs"].get("customFunctionJavascriptFunction") != LAKERA_OUTPUT_CODE:
        problems.append("Lakera Guard (output) code differs from this script")
    agent = by_id["agentAgentflow_0"]["data"]["inputs"]
    problems += _model_problems("agentAgentflow_0", agent.get("agentModelConfig") or {})
    msgs = agent.get("agentMessages") or []
    if len(msgs) != 1 or msgs[0].get("role") != "system":
        problems.append("agent needs exactly one system message")
    else:
        problems += _prompt_problems(slug, "agentAgentflow_0", msgs[0].get("content", ""))
    tools = agent.get("agentTools") or []
    scope = scope_for(slug)
    if scope:
        if len(tools) != 1 or tools[0].get("agentSelectedTool") != "customMCP":
            problems.append("agent needs exactly one Custom MCP tool")
        else:
            problems += _mcp_problems(slug, "agentAgentflow_0 tool", tools[0]["agentSelectedToolConfig"])
    elif tools:
        problems.append("agent should have no tools")
    if flow["edges"] != guard_edges(flow):
        problems.append("edges do not match the guard layout")
    return problems


def cmd_check(_args) -> int:
    bad = 0
    catalog = lf.catalog_names()
    paths = sorted(glob.glob(os.path.join(FLOW_DIR, "*.flowdata.json")))
    for path in paths:
        slug = os.path.basename(path)[: -len(".flowdata.json")]
        with open(path, encoding="utf-8") as fh:
            raw = fh.read()
        try:
            flow = json.loads(raw)
        except ValueError as exc:
            print(f"FAIL {slug}: not valid JSON ({exc})")
            bad += 1
            continue
        problems = []
        leftovers = sorted(set(SECRET_PLACEHOLDER.findall(raw)))
        if leftovers:
            problems.append(f"secret placeholders {leftovers} (use Flowise variables)")
        if CREDENTIAL_ID.search(raw):
            problems.append("contains a credential or object id from another Flowise instance")
        values = json.dumps([n.get("data", {}).get("inputs") for n in flow.get("nodes", [])])
        for name in set(re.findall(r"\{\{\$vars\.([A-Za-z0-9_]+)\}\}", values)) - set(VARIABLES):
            problems.append(f"references unknown Flowise variable {name}")
        if slug not in catalog:
            problems.append("no entry in integrations/builders_agents.json (builders-import would not seed it)")
        for n in flow.get("nodes", []):
            note = str((n.get("data", {}).get("inputs") or {}).get("note") or "")
            hit = lf.UNPROFESSIONAL.search(note)
            if hit:
                problems.append(f"{n.get('id')}: sticky note contains unprofessional text ({hit.group(0)!r})")
        problems += check_guarded(slug, flow) if slug in GUARDED else check_chatflow(slug, flow)
        scope = scope_for(slug)
        if slug in NO_MCP and not scope:
            label = "no MCP tools"
        elif scope is None:
            label = "tools kept from the file"
        else:
            label = f"{len(scope)} tools"
        if slug in GUARDED:
            label += ", Lakera Guard agentflow"
        if problems:
            bad += 1
            print(f"FAIL {slug}:")
            for p in problems:
                print(f"     - {p}")
        else:
            print(f"ok   {slug} ({label})")
    print(f"check: {len(paths) - bad}/{len(paths)} flows pass.")
    return 1 if bad else 0


# ─────────────────────────── snapshot (inside the Docker network) ───────────────────────────

def _http(method: str, url: str, body=None, headers=None, timeout=120):
    h = dict(headers or {})
    payload = None
    if body is not None:
        payload = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=payload, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw, status, cookies = r.read(), r.status, r.headers.get_all("Set-Cookie") or []
    except urllib.error.HTTPError as e:
        raw, status, cookies = e.read(), e.code, e.headers.get_all("Set-Cookie") or []
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    try:
        parsed = json.loads(raw)
    except ValueError:
        parsed = raw.decode("utf-8", "replace")
    return status, parsed, "; ".join(c.split(";", 1)[0] for c in cookies)


def flowise_headers(base: str) -> dict:
    if os.environ.get("FLOWISE_API_KEY"):
        return {"Authorization": f"Bearer {os.environ['FLOWISE_API_KEY']}"}
    status, _, cookies = _http("POST", f"{base}/api/v1/auth/login",
                               {"email": os.environ.get("ADMIN_EMAIL", ""),
                                "password": os.environ.get("ADMIN_PASSWORD", "")})
    if status != 200 or not cookies:
        raise SystemExit(f"snapshot: Flowise login failed (HTTP {status})")
    return {"Cookie": cookies, "x-request-from": "internal"}


def list_actions(base: str, headers: dict, cfg: dict) -> list:
    body = {"loadMethod": "listActions", "name": "customMCP",
            "inputs": {"mcpServerConfig": json.dumps(cfg)}, "inputParams": []}
    status, rows, _ = _http("POST", f"{base}/api/v1/node-load-method/customMCP", body, headers)
    if status != 200 or not isinstance(rows, list):
        return []
    return [r["name"] for r in rows if r.get("name") != "error"]


def cmd_snapshot(args) -> int:
    base = os.environ.get("FLOWISE_URL", "http://flowise:3020").rstrip("/")
    headers = flowise_headers(base)
    os.makedirs(os.path.join(args.out, "nodes"), exist_ok=True)
    for name in NODE_VERSIONS:
        status, d, _ = _http("GET", f"{base}/api/v1/nodes/{name}", headers=headers)
        if status != 200 or not isinstance(d, dict):
            print(f"snapshot: GET /api/v1/nodes/{name} failed (HTTP {status})")
            return 1
        d.pop("filePath", None)
        with open(os.path.join(args.out, "nodes", f"{name}.json"), "w", encoding="utf-8") as fh:
            json.dump(d, fh, indent=1)
    drift = 0
    for server, url in SIDECAR_URL.items():
        live = list_actions(base, headers, {"url": url})
        if sorted(live) != sorted(SERVER_TOOLS[server]):
            drift += 1
            print(f"snapshot: WARNING {server} ({url}) lists {len(live)} tools, SERVER_TOOLS has "
                  f"{len(SERVER_TOOLS[server])}. New: {sorted(set(live) - set(SERVER_TOOLS[server]))[:8]} "
                  f"Gone: {sorted(set(SERVER_TOOLS[server]) - set(live))[:8]}")
    token = os.environ.get("MCP_GATEWAY_TOKEN", "")
    if token:
        live = list_actions(base, headers, {"url": GATEWAY_URL, "headers": {"Authorization": f"Bearer {token}"}})
        if set(live) != set(TOOL_OWNER):
            drift += 1
            print(f"snapshot: WARNING the gateway lists {len(live)} tools; SERVER_TOOLS has {len(TOOL_OWNER)}")
    print(f"snapshot: {len(NODE_VERSIONS)} node definitions written to {args.out}; "
          f"{'tool lists match SERVER_TOOLS' if not drift else f'{drift} tool list(s) differ'}.")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("snapshot", help="save the installed node definitions (inside the Docker network)")
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_snapshot)
    p = sub.add_parser("apply", help="rewrite integrations/flowise/*.flowdata.json in place")
    p.add_argument("--snapshot", help="directory written by the snapshot command")
    p.set_defaults(func=cmd_apply)
    p = sub.add_parser("check", help="validate every flow file (exit 1 on problems)")
    p.set_defaults(func=cmd_check)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
