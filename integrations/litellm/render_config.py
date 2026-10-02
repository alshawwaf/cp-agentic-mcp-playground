#!/usr/bin/env python3
"""Render the lab's LiteLLM config from environment variables, then start LiteLLM.

This is the entrypoint of the `litellm` service. Every seeded agent (n8n, Flowise,
Langflow, the code-first agent) uses ONE model name, `lab-chat`, at
http://litellm:4000/v1. This script decides which provider serves `lab-chat` from
the keys in .env (or 1Password), writes the LiteLLM config, logs one line saying
what it chose, and then replaces itself with `litellm --config <file>`.
To change provider or key: edit .env, then run `docker compose up -d litellm`.

Stdlib only (Python 3.8+). The rendered file holds no secret values: keys are
written as `os.environ/NAME` references that LiteLLM resolves itself, and the log
names variables, never their values.

Provider selection (LAB_MODEL_PROVIDER, default "auto"):
  auto       the first provider that is fully configured, in this order:
             azure -> openai -> anthropic -> gemini -> ollama (local, needs no key)
  azure      AZURE_OPENAI_API_KEY + AZURE_OPENAI_ENDPOINT + AZURE_OPENAI_DEPLOYMENT
             (AZURE_OPENAI_API_VERSION, default 2024-10-21 = the dated GA API: deployment
             path + api-key header, which Azure resources and API Management gateways accept;
             "v1" opts in to the v1 API, which LiteLLM calls with a Bearer token)
  openai     OPENAI_API_KEY (OPENAI_MODEL, default gpt-5.1; OPENAI_BASE_URL optional,
             for a proxy, Azure API Management or any OpenAI-compatible server)
  anthropic  ANTHROPIC_API_KEY (ANTHROPIC_MODEL, default claude-sonnet-5)
  gemini     GEMINI_API_KEY (GEMINI_MODEL, default gemini-2.5-flash)
  ollama     no key (OLLAMA_CHAT_MODEL, default qwen3.5:4b;
             OLLAMA_API_BASE, default http://ollama-cpu:11434;
             OLLAMA_NUM_CTX, default 32768 tokens; OLLAMA_THINK, default false:
             true/false switch the model's thinking on/off, auto keeps the model default)
An explicit provider whose settings are missing stops the service with a message
naming the missing variable. It never falls back to another provider silently.

Other settings:
  LITELLM_MASTER_KEY   REQUIRED. Every client authenticates with it. No default.
                       Any other key gets HTTP 401 (lab_auth.py, copied next to the config).
  LANGFUSE_PUBLIC_KEY + LANGFUSE_SECRET_KEY   turn Langfuse tracing on
                       (LANGFUSE_HOST, default http://langfuse:3000)

Usage:
  render_config.py --exec [litellm args]   render, then exec litellm (the entrypoint)
  render_config.py --check                 validate and log only (exit 2 = config error)
  render_config.py --print                 print the rendered YAML (holds no secrets)
--check and --print win over --exec, so `docker compose run --rm --no-deps litellm --check`
validates the current .env without starting a second proxy.
"""
import argparse
import json
import os
import re
import sys
from urllib.parse import urlsplit, urlunsplit

LOG_PREFIX = "[lab-litellm]"
DEFAULT_OUT = "/tmp/lab-litellm/config.yaml"
LAB_MODEL = "lab-chat"
PROVIDERS = ("azure", "openai", "anthropic", "gemini", "ollama")
AUTO_ORDER = PROVIDERS  # ollama last: always available, needs no key
RESTART_HINT = "then run: docker compose up -d litellm"
# Key check plugin; LiteLLM imports it from the directory of the rendered config.
AUTH_MODULE = "lab_auth"
AUTH_PLUGIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), AUTH_MODULE + ".py")
OP_HINT = "Start the stack through 1Password: op run --env-file=.env -- docker compose up -d"

# Variables a provider needs before it can serve lab-chat.
REQUIRED = {
    "azure": ("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT"),
    "openai": ("OPENAI_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "gemini": ("GEMINI_API_KEY",),
    "ollama": (),
}
DEFAULTS = {
    "AZURE_OPENAI_API_VERSION": "2024-10-21",
    "OPENAI_MODEL": "gpt-5.1",
    "ANTHROPIC_MODEL": "claude-sonnet-5",
    "GEMINI_MODEL": "gemini-2.5-flash",
    "OLLAMA_CHAT_MODEL": "qwen3.5:4b",
    "OLLAMA_API_BASE": "http://ollama-cpu:11434",
    "OLLAMA_NUM_CTX": "32768",
    "OLLAMA_THINK": "false",
    "LANGFUSE_HOST": "http://langfuse:3000",
    "LAB_REASONING_EFFORT": "auto",
}
REASONING_LEVELS = ("auto", "none", "minimal", "low", "medium", "high")
GPT5_NAME = re.compile(r"(^|[/_-])gpt-?5", re.IGNORECASE)
# Every variable this script reads (the compose service passes all of them).
LAB_VARIABLES = sorted({n for need in REQUIRED.values() for n in need} | set(DEFAULTS) | {
    "LAB_MODEL_PROVIDER", "OPENAI_BASE_URL", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"})
PROVIDER_LABEL = {
    "azure": "Azure OpenAI",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "gemini": "Google Gemini",
    "ollama": "local Ollama",
}
# Ollama context window. The seeded agents send their tool catalog with every call:
# Fleet Commander sends about 18k tokens of tool schemas, so 32k is the default and
# anything below 16k cuts the instructions of the agents with many tools.
OLLAMA_MIN_CTX, OLLAMA_RECOMMENDED_CTX, OLLAMA_MAX_CTX = 2048, 16384, 262144
# Values that mean "not configured" even though the variable is non-empty.
PLACEHOLDERS = {"none", "null", "unset", "changeme", "change-me", "change_me", "your-key-here", "xxx"}
# Published in older versions of this repo: anyone could use it, so it is refused.
PUBLIC_MASTER_KEYS = {"sk-cp-litellm-training-key"}


class ConfigError(Exception):
    """A setting is missing or invalid; LiteLLM must not start."""


# -- environment helpers -------------------------------------------------------------

def _raw(env, name):
    value = env.get(name)
    return value.strip() if isinstance(value, str) else ""


def _is_op_ref(value):
    return value.startswith("op://")


def _is_placeholder(value):
    low = value.lower()
    return low in PLACEHOLDERS or low.startswith("change_me") or low.startswith("<")


def _usable(env, name):
    """True when NAME holds a real value (not blank, a placeholder or an unresolved op:// ref)."""
    value = _raw(env, name)
    return bool(value) and not _is_op_ref(value) and not _is_placeholder(value)


def _value(env, name):
    """The variable's value, or its default when it is blank or a placeholder."""
    value = _raw(env, name)
    if not value or _is_placeholder(value):
        return DEFAULTS.get(name, "")
    if _is_op_ref(value):
        raise ConfigError("{0} holds an unresolved 1Password reference (op://...). {1}".format(name, OP_HINT))
    return value


def _op_refs(env, names):
    return [n for n in names if _is_op_ref(_raw(env, n))]


def _missing(env, names):
    return [n for n in names if not _usable(env, n)]


def _bool(env, name):
    value = _value(env, name).lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    raise ConfigError("{0} must be true, false or auto".format(name))


def _safe_url(url):
    """scheme://host[:port]/path only, for logs (drops user:password, query and fragment)."""
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if parts.port:
            host = "{0}:{1}".format(host, parts.port)
    except ValueError:
        return "<unparseable URL>"
    return urlunsplit((parts.scheme, host, parts.path, "", ""))


def _check_http_url(name, url):
    try:
        parts = urlsplit(url)
        parts.port  # raises ValueError on a bad port
    except ValueError:
        raise ConfigError("{0} is not a valid URL".format(name))
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ConfigError("{0} must be a full http:// or https:// URL (got {1!r})".format(name, _safe_url(url)))
    return parts


def _check_model_name(name, value):
    if not value or re.search(r"\s", value):
        raise ConfigError("{0} must be a model or deployment name without spaces".format(name))


# -- provider selection ----------------------------------------------------------------

def select_provider(env, notes):
    """Return (provider, reason). Raise ConfigError when an explicit choice cannot work."""
    requested = (_raw(env, "LAB_MODEL_PROVIDER") or "auto").lower()
    if requested not in PROVIDERS + ("auto",):
        raise ConfigError("LAB_MODEL_PROVIDER={0!r} is not valid. Use one of: auto, {1}".format(
            requested, ", ".join(PROVIDERS)))

    if requested != "auto":
        need = REQUIRED[requested]
        refs = _op_refs(env, need)
        if refs:
            raise ConfigError("LAB_MODEL_PROVIDER={0} but {1} still hold unresolved 1Password "
                              "references (op://...). {2}".format(requested, ", ".join(refs), OP_HINT))
        missing = _missing(env, need)
        if missing:
            raise ConfigError("LAB_MODEL_PROVIDER={0} but {1} {2} not set. Set {3} in .env (or set "
                              "LAB_MODEL_PROVIDER=auto), {4}".format(
                                  requested, " and ".join(missing), "is" if len(missing) == 1 else "are",
                                  "it" if len(missing) == 1 else "them", RESTART_HINT))
        return requested, "set by LAB_MODEL_PROVIDER"

    for provider in AUTO_ORDER:
        need = REQUIRED[provider]
        refs = _op_refs(env, need)
        if refs:
            notes.append("WARNING: skipping {0}: {1} hold unresolved 1Password references (op://...). "
                         "{2}".format(PROVIDER_LABEL[provider], ", ".join(refs), OP_HINT))
            continue
        missing = _missing(env, need)
        if not missing:
            if provider == "ollama":
                return provider, "chosen automatically: no cloud model key is set"
            return provider, "chosen automatically: first configured of azure, openai, anthropic, gemini"
        if provider == "azure" and len(missing) < len(need):
            notes.append("WARNING: Azure OpenAI is only partly configured (missing {0}), so it is "
                         "skipped".format(", ".join(missing)))
    raise AssertionError("unreachable: ollama needs no settings")


def _azure_endpoint(raw):
    """Keep only the resource base URL of what people paste from the Azure portal.

    https://res.openai.azure.com/, .../openai/v1/ and full .../openai/deployments/...
    URLs all become https://res.openai.azure.com (LiteLLM adds the /openai/... path).
    A gateway path is kept: https://apim.example.net/my-api/openai/v1 becomes
    https://apim.example.net/my-api. Only a whole "openai" path segment is cut, so an API
    Management path such as /openai-prod stays intact.
    """
    parts = _check_http_url("AZURE_OPENAI_ENDPOINT", raw)
    if parts.username or parts.password:
        raise ConfigError("AZURE_OPENAI_ENDPOINT must not contain a user name or password")
    path = parts.path
    match = re.search(r"/openai(?=/|$)", path, re.IGNORECASE)
    if match:
        path = path[:match.start()]
    return urlunsplit((parts.scheme, parts.netloc, path.rstrip("/"), "", ""))


def _openai_base(env):
    """api_base for OPENAI_BASE_URL. Adds /v1 when only scheme://host[:port] was given."""
    raw = _value(env, "OPENAI_BASE_URL")
    parts = _check_http_url("OPENAI_BASE_URL", raw)
    if parts.path in ("", "/") and not (parts.username or parts.password or parts.query):
        return urlunsplit((parts.scheme, parts.netloc, "/v1", "", "")), _safe_url(raw) + " (/v1 added)"
    # Anything more specific is passed through untouched and stays off the disk.
    return "os.environ/OPENAI_BASE_URL", _safe_url(raw)


def _ollama_options(env, notes):
    raw = _value(env, "OLLAMA_NUM_CTX")
    if not raw.isdigit() or not OLLAMA_MIN_CTX <= int(raw) <= OLLAMA_MAX_CTX:
        raise ConfigError("OLLAMA_NUM_CTX must be a whole number of tokens between {0} and {1}".format(
            OLLAMA_MIN_CTX, OLLAMA_MAX_CTX))
    num_ctx = int(raw)
    if num_ctx < OLLAMA_RECOMMENDED_CTX:
        notes.append("WARNING: OLLAMA_NUM_CTX={0} is below {1}; agents with many tools (Management, "
                     "Gaia, the MCP Gateway agents) will lose their instructions".format(
                         num_ctx, OLLAMA_RECOMMENDED_CTX))
    options = {"num_ctx": num_ctx}
    if _value(env, "OLLAMA_THINK").lower() != "auto":
        # Thinking multiplies latency on a laptop CPU and can leave the reply empty, so it is off
        # unless asked for. "auto" sends nothing, for models that have no thinking switch.
        options["think"] = _bool(env, "OLLAMA_THINK")
    return options


def _reasoning_params(env, model_name):
    """reasoning_effort for OpenAI-family models, plus a log note.

    GPT-5.4 and later reject function tools in Chat Completions while reasoning is on (the default),
    and LiteLLM then routes the call to the Responses API, which many API Management gateways do not
    serve. "auto" (the default) sends reasoning_effort=none for GPT-5 models, so every tool-using
    agent stays on Chat Completions. Any other level is sent as given: those calls use the Responses
    API (AZURE_OPENAI_API_VERSION=v1 or a recent preview, and an endpoint that serves /responses).
    """
    level = _value(env, "LAB_REASONING_EFFORT").lower()
    if level not in REASONING_LEVELS:
        raise ConfigError("LAB_REASONING_EFFORT must be one of: " + ", ".join(REASONING_LEVELS))
    if level == "auto":
        if GPT5_NAME.search(model_name or ""):
            return {"reasoning_effort": "none"}, "reasoning off for tool calls (LAB_REASONING_EFFORT=auto)"
        return {}, ""
    return {"reasoning_effort": level}, "reasoning_effort {0}".format(level)


def provider_params(env, provider, notes):
    """litellm_params for lab-chat on PROVIDER, plus a log-safe description."""
    if provider == "azure":
        deployment = _value(env, "AZURE_OPENAI_DEPLOYMENT")
        _check_model_name("AZURE_OPENAI_DEPLOYMENT", deployment)
        endpoint = _azure_endpoint(_value(env, "AZURE_OPENAI_ENDPOINT"))
        version = _value(env, "AZURE_OPENAI_API_VERSION")
        _check_model_name("AZURE_OPENAI_API_VERSION", version)
        params = {
            "model": "azure/" + deployment,
            "api_base": endpoint,
            "api_key": "os.environ/AZURE_OPENAI_API_KEY",
            "api_version": version,
        }
        extra, note = _reasoning_params(env, deployment)
        params.update(extra)
        desc = "deployment {0} at {1}, API version {2}".format(deployment, _safe_url(endpoint), version)
        return params, desc + (", " + note if note else "")
    if provider == "openai":
        model = _value(env, "OPENAI_MODEL")
        _check_model_name("OPENAI_MODEL", model)
        params = {"model": "openai/" + model, "api_key": "os.environ/OPENAI_API_KEY"}
        extra, note = _reasoning_params(env, model)
        params.update(extra)
        desc = "model {0}".format(model) + (", " + note if note else "")
        if _raw(env, "OPENAI_BASE_URL") and not _is_placeholder(_raw(env, "OPENAI_BASE_URL")):
            params["api_base"], shown = _openai_base(env)
            desc += " via {0}".format(shown)
        return params, desc
    if provider == "anthropic":
        model = _value(env, "ANTHROPIC_MODEL")
        _check_model_name("ANTHROPIC_MODEL", model)
        # Current Claude models reject sampling parameters (HTTP 400), and the builders send
        # temperature. Dropping them here keeps every seeded agent working on any Claude model.
        # Anthropic requires max_tokens; without one LiteLLM sends the model maximum (128k on
        # claude-sonnet-5) or 4096 for a model it does not know. 16k is the default a client's
        # own max_tokens overrides.
        params = {"model": "anthropic/" + model, "api_key": "os.environ/ANTHROPIC_API_KEY",
                  "max_tokens": 16384, "additional_drop_params": ["temperature", "top_p", "top_k"]}
        return params, "model {0}".format(model)
    if provider == "gemini":
        model = _value(env, "GEMINI_MODEL")
        _check_model_name("GEMINI_MODEL", model)
        return ({"model": "gemini/" + model, "api_key": "os.environ/GEMINI_API_KEY"},
                "model {0}".format(model))
    model = _value(env, "OLLAMA_CHAT_MODEL")
    _check_model_name("OLLAMA_CHAT_MODEL", model)
    base = _value(env, "OLLAMA_API_BASE").rstrip("/")
    _check_http_url("OLLAMA_API_BASE", base)
    params = {"model": "ollama_chat/" + model, "api_base": base}
    options = _ollama_options(env, notes)
    params.update(options)
    desc = "model {0} at {1}, context {2} tokens, thinking {3}".format(
        model, _safe_url(base), options["num_ctx"],
        {True: "on", False: "off"}.get(options.get("think"), "model default"))
    return params, desc


# -- config ----------------------------------------------------------------------------

def check_master_key(env):
    key = _raw(env, "LITELLM_MASTER_KEY")
    if _is_op_ref(key):
        raise ConfigError("LITELLM_MASTER_KEY holds an unresolved 1Password reference (op://...). " + OP_HINT)
    if not key or _is_placeholder(key):
        raise ConfigError("LITELLM_MASTER_KEY is not set. Run ./setup.sh to generate one (or put "
                          "LITELLM_MASTER_KEY=sk-<long random string> in .env), " + RESTART_HINT)
    if key in PUBLIC_MASTER_KEYS:
        raise ConfigError("LITELLM_MASTER_KEY is the old public training value, which anyone can use. "
                          "Run ./setup.sh to generate a new one, " + RESTART_HINT)
    if len(key) < 16 or re.search(r"\s", key):
        raise ConfigError("LITELLM_MASTER_KEY must be 16 or more characters with no spaces. "
                          "Run ./setup.sh to generate one, " + RESTART_HINT)


def tracing(env, notes):
    """(litellm_settings additions, log text) for Langfuse."""
    refs = _op_refs(env, ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"))
    if refs:
        notes.append("WARNING: {0} hold unresolved 1Password references, so Langfuse tracing is "
                     "off. {1}".format(", ".join(refs), OP_HINT))
        return {}, "off"
    if not (_usable(env, "LANGFUSE_PUBLIC_KEY") and _usable(env, "LANGFUSE_SECRET_KEY")):
        return {}, "off (set LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY to turn it on)"
    host = _value(env, "LANGFUSE_HOST")
    _check_http_url("LANGFUSE_HOST", host)
    return ({"success_callback": ["langfuse"], "failure_callback": ["langfuse"]},
            "on ({0})".format(_safe_url(host)))


def build(env):
    """Return (config dict, log lines). Raise ConfigError on any blocking problem."""
    notes = []
    check_master_key(env)
    provider, reason = select_provider(env, notes)
    params, desc = provider_params(env, provider, notes)
    callbacks, trace_text = tracing(env, notes)

    config = {
        "model_list": [{"model_name": LAB_MODEL, "litellm_params": params}],
        "litellm_settings": dict(
            # Builders send parameters some models reject (e.g. temperature on reasoning
            # models); drop them instead of failing the call.
            drop_params=True,
            **callbacks),
        "router_settings": {
            # The builders retry failed model calls themselves. LiteLLM's default of 2 more
            # retries per call tripled every failure (and every ERROR generation in Langfuse)
            # and ran a slow local model up to three times.
            "num_retries": 0,
        },
        "general_settings": {"master_key": "os.environ/LITELLM_MASTER_KEY"},
    }
    # Clients connect directly over the lab network; no reverse proxy sits in front of LiteLLM.
    config["general_settings"]["trusted_proxy_ranges"] = []
    if os.path.isfile(AUTH_PLUGIN):
        # Wrong or missing key -> HTTP 401 (stock LiteLLM without a database says 400 "No connected db.").
        config["general_settings"]["custom_auth"] = AUTH_MODULE + ".user_api_key_auth"
        config["general_settings"]["custom_auth_run_common_checks"] = True
    else:
        notes.append("WARNING: {0} is missing; using LiteLLM's own key check (a wrong key gets HTTP 400 "
                     "instead of 401)".format(os.path.basename(AUTH_PLUGIN)))
    line = "{0} -> {1} {2} ({3}). Langfuse tracing {4}.".format(
        LAB_MODEL, PROVIDER_LABEL[provider], desc, reason, trace_text)
    return config, notes + [line]


# -- YAML output (a small, safe subset: dicts, lists, strings, numbers, booleans) ------

_PLAIN_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _scalar(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return json.dumps(value)
    return json.dumps(str(value))  # a JSON string is a valid double-quoted YAML scalar


def _key(key):
    return key if _PLAIN_KEY.match(key) else json.dumps(key)


def to_yaml(value, indent=0):
    pad = " " * indent
    lines = []
    if isinstance(value, dict):
        for k, v in value.items():
            if isinstance(v, (dict, list)) and v:
                lines.append("{0}{1}:".format(pad, _key(k)))
                lines.append(to_yaml(v, indent + 2))
            elif isinstance(v, (dict, list)):
                lines.append("{0}{1}: {2}".format(pad, _key(k), "{}" if isinstance(v, dict) else "[]"))
            else:
                lines.append("{0}{1}: {2}".format(pad, _key(k), _scalar(v)))
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict) and item:
                body = to_yaml(item, indent + 2).splitlines()
                lines.append("{0}- {1}".format(pad, body[0].lstrip()))
                lines.extend(body[1:])
            else:
                lines.append("{0}- {1}".format(pad, _scalar(item)))
    else:
        lines.append(pad + _scalar(value))
    return "\n".join(lines)


HEADER = """\
# Rendered at container start by integrations/litellm/render_config.py. Do not edit:
# change .env (LAB_MODEL_PROVIDER, the provider key, *_MODEL) and run
#   docker compose up -d litellm
# Keys appear only as os.environ/NAME references; LiteLLM reads them from its environment.
"""


def render(env):
    config, log = build(env)
    return HEADER + to_yaml(config) + "\n", log


# -- main ------------------------------------------------------------------------------

def _say(line):
    print("{0} {1}".format(LOG_PREFIX, line), file=sys.stderr, flush=True)


def _write(path, text):
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, mode=0o700, exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(tmp, path)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(
        description="Render the lab LiteLLM config from the environment (see the module docstring).")
    parser.add_argument("--out", default=DEFAULT_OUT, help="where to write the config (default %(default)s)")
    parser.add_argument("--exec", dest="run", action="store_true",
                        help="after rendering, replace this process with litellm; other arguments go to litellm")
    parser.add_argument("--check", action="store_true", help="validate and log only; write nothing")
    parser.add_argument("--print", dest="show", action="store_true", help="print the rendered YAML to stdout")
    args, passthrough = parser.parse_known_args(argv)
    if passthrough and passthrough[0] == "--":
        passthrough = passthrough[1:]

    try:
        text, log = render(os.environ)
    except ConfigError as exc:
        _say("ERROR: {0}".format(exc))
        _say("LiteLLM is not started, so lab-chat stays unavailable until this is fixed.")
        return 2

    for line in log:
        _say(line)
    if args.check:
        return 0
    if args.show:
        sys.stdout.write(text)
        return 0

    _write(args.out, text)
    if os.path.isfile(AUTH_PLUGIN):
        with open(AUTH_PLUGIN, encoding="utf-8") as handle:
            _write(os.path.join(os.path.dirname(args.out) or ".", AUTH_MODULE + ".py"), handle.read())
    if not args.run:
        return 0

    # Compose passes every optional variable, blank when unset. Drop the blank ones so that
    # LiteLLM and the provider SDKs fall back to their defaults instead of using "".
    for name in LAB_VARIABLES:
        if name in os.environ and not os.environ[name].strip():
            del os.environ[name]
    if not _usable(os.environ, "LANGFUSE_HOST"):
        os.environ["LANGFUSE_HOST"] = DEFAULTS["LANGFUSE_HOST"]
    # Offline-safe start: use the model cost map bundled in the image instead of fetching it
    # from GitHub at every start, and keep the admin UI off (no host port, no database).
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    os.environ.setdefault("DISABLE_ADMIN_UI", "True")
    cmd = ["litellm", "--config", args.out, "--telemetry", "False"] + (passthrough or ["--port", "4000"])
    try:
        os.execvp(cmd[0], cmd)
    except OSError as exc:
        _say("ERROR: could not start litellm: {0}".format(exc))
        return 127
    return 0  # not reached


if __name__ == "__main__":
    sys.exit(main())
