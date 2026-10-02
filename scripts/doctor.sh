#!/bin/sh
# scripts/doctor.sh: checks the Check Point AI agent lab and prints what works with the keys given.
#
#   ./scripts/doctor.sh --preflight     before the first start: .env complete and valid, 1Password
#                                       references, admin password rule, model provider settings,
#                                       LiteLLM config check, Docker networks and resources, the lab
#                                       components (Standard lab or Complete lab) against the CPUs
#                                       and memory Docker can use
#   ./scripts/doctor.sh --preflight --online
#                                       also validates each supplied model key (harmless model-list call)
#                                       and TCP reachability of the Check Point hosts
#   ./scripts/doctor.sh --post-start    after the start: every service healthy (one-shots exited 0),
#                                       MCP gateway auth and tools, agents seeded in n8n, Flowise and
#                                       Langflow, lab-chat answers, Langfuse received the trace, and the
#                                       MCP Security Lab server on its own network (security-lab profile).
#                                       Parts the lab components leave out (Langflow, Open WebUI) are
#                                       listed as "off", never as failures.
#
# Needs only docker and sh on the host; network checks run in ONE throwaway container at a time
# (python:3.12-alpine, capped CPU and memory). Never prints a secret. Exit 1 when a blocker is found.

set -u
umask 077

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd -P) || exit 2
LAB_ROOT=$(cd "$SCRIPT_DIR/.." && pwd -P) || exit 2
# shellcheck source=SCRIPTDIR/lib/labenv.sh
. "$SCRIPT_DIR/lib/labenv.sh"

PROBE_IMAGE="${LAB_PROBE_IMAGE:-python:3.12-alpine}"
PROBE="$SCRIPT_DIR/lib/stack_probe.py"

usage() {
  cat <<'EOF'
Usage: ./scripts/doctor.sh [--preflight | --post-start] [options]

  --preflight        Check .env and Docker before the first start (default).
  --post-start       Check the running lab: services, MCP gateway, seeded agents, lab-chat, Langfuse.
  --online           With --preflight: also call each supplied model provider (model list only)
                     and test TCP reachability of the Check Point hosts.
  --skip-chat        With --post-start: do not send the one-line lab-chat test prompt.
  --env-file FILE    Settings file (default: .env in the lab directory).
  --project-dir DIR  Lab directory holding docker-compose.yml (default: the repository root).
  --verbose, -v      Also list every healthy service.
  --help

Run it the way you start the lab. With 1Password references in .env:
  op run --env-file=.env -- ./scripts/doctor.sh --post-start
Exit status: 0 = no blockers, 1 = blockers found, 2 = the check itself could not run.
EOF
}

mode=preflight
online=0
chat=1
verbose=0
env_file=
while [ $# -gt 0 ]; do
  case $1 in
    --preflight) mode=preflight ;;
    --post-start) mode=post ;;
    --health) mode=health ;;
    --online) online=1 ;;
    --skip-chat) chat=0 ;;
    --verbose | -v) verbose=1 ;;
    --env-file) [ $# -ge 2 ] || lab_die "--env-file needs a file" 2; env_file=$2; shift ;;
    --project-dir) [ $# -ge 2 ] || lab_die "--project-dir needs a directory" 2
      LAB_ROOT=$(cd "$2" && pwd -P) || lab_die "no such directory: $2" 2; shift ;;
    --profile | --timeout) [ $# -ge 2 ] && shift ;;   # accepted for compatibility, not used
    -h | --help) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done
[ -n "$env_file" ] || env_file="$LAB_ROOT/.env"

lab_tmp_init
trap 'lab_tmp_cleanup' EXIT
trap 'lab_tmp_cleanup; exit 130' INT TERM HUP

# ---------------------------------------------------------------------------- output
blockers=0
warnings=0
res() {
  case $1 in
    ok) _t="ok  " ;;
    warn) _t="warn"; warnings=$((warnings + 1)) ;;
    fail) _t="FAIL"; blockers=$((blockers + 1)) ;;
    info) _t="info" ;;
    skip) _t="skip" ;;
    *) _t="$1" ;;
  esac
  printf '  %s  %s\n' "$_t" "$2"
}
section() { printf '\n%s\n' "$1"; }
rows="$LAB_TMP/matrix"
: > "$rows"
row() { printf '%s|%s|%s\n' "$1" "$2" "$3" >> "$rows"; }

# ---------------------------------------------------------------------------- settings
have_env=0
if lab_env_load "$env_file" F_; then have_env=1; fi

# eff NAME: the value Docker Compose would use (the shell environment wins over .env, as in Compose).
eff() {
  _e_set=
  eval "_e_set=\${$1+x}"
  if [ "$_e_set" = x ]; then eval "printf '%s' \"\${$1}\""; else lab_get F_ "$1"; fi
}
is_real() {
  _ir=$(eff "$1")
  [ -n "$_ir" ] && ! lab_is_op_ref "$_ir" && ! lab_is_placeholder "$_ir"
}
is_ref() { lab_is_op_ref "$(eff "$1")"; }
all_real() { for _ar in "$@"; do is_real "$_ar" || return 1; done; return 0; }
missing_of() {
  _mo=
  for _m in "$@"; do is_real "$_m" || is_ref "$_m" || _mo="$_mo $_m"; done
  printf '%s' "${_mo# }"
}
# export_vars NAME...: put the effective values in this process environment for docker run -e NAME.
export_vars() {
  for _x in "$@"; do
    _xv=$(eff "$_x")
    eval "$_x=\$_xv"
    # shellcheck disable=SC2163
    export "$_x"
  done
}
lower() { printf '%s' "$1" | tr 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' 'abcdefghijklmnopqrstuvwxyz'; }

compose_file="$LAB_ROOT/docker-compose.yml"
known_profiles=
load_profiles() {
  # Profile names from the effective Compose configuration (override files included), else a static read.
  known_profiles=$(lab_compose config --profiles 2>/dev/null </dev/null | tr '\n' ' ')
  if [ -z "$(printf '%s' "$known_profiles" | tr -d ' ')" ]; then
    known_profiles=$(grep -o 'profiles:[^]]*\]' "$compose_file" 2>/dev/null | grep -o '"[^"]*"' | tr -d '"' | sort -u | tr '\n' ' ')
  fi
}
profile_defined() { case " $known_profiles " in *" $1 "*) return 0 ;; esac; return 1; }
profile_on() { lab_profiles_has "$(eff COMPOSE_PROFILES)" "$1"; }
# service_profiles SERVICE: the profiles of a service in docker-compose.yml, comma-separated
# (empty = the service is part of the default lab).
service_profiles() {
  awk -v s="$1" '
    /^services:[ \t]*$/ { insvc = 1; next }
    /^[^ \t#]/ { insvc = 0 }
    insvc && /^  [A-Za-z0-9_.-]+:[ \t]*$/ { cur = $1; sub(/:$/, "", cur); next }
    insvc && cur == s && /^    profiles:/ { p = $0; sub(/^[^[]*\[/, "", p); sub(/\].*$/, "", p); gsub(/[" \t]/, "", p); print p; exit }
  ' "$compose_file" 2>/dev/null
}
# service_on SERVICE: the service starts with this COMPOSE_PROFILES.
service_on() {
  _so=$(service_profiles "$1")
  [ -n "$_so" ] || return 0
  for _sop in $(printf '%s' "$_so" | tr ',' ' '); do profile_on "$_sop" && return 0; done
  return 1
}
# Local chat model: lab-chat on Ollama, or a profile that runs a local chat model (Open WebUI).
local_chat_on() { service_on open-webui || profile_on local-models; }
ollama_mem_low() {
  # OLLAMA_MEM_LIMIT is below what a local chat model needs (blank = the Compose default 1g)
  _oml=$(eff OLLAMA_MEM_LIMIT)
  _omm=$(lab_mem_mib "${_oml:-1g}")
  [ -z "$_omm" ] || [ "$_omm" -lt "$LAB_OLLAMA_CHAT_MIB" ]
}

docker_ok=0
if lab_have_docker && lab_docker_up; then docker_ok=1; fi
load_profiles
docker_cpus=
docker_mib=
docker_desktop=0
raise_hint() {
  if [ "$docker_desktop" = 1 ]; then printf '%s' "increase it in Docker Desktop > Settings > Resources, then apply and restart"
  else printf '%s' "use a machine with more CPUs and memory for Docker"; fi
}
# docker_can_use: "6 CPUs and 15.1 GB" (what Docker reported).
docker_can_use() { printf '%s and %s GB' "$(lab_cpus "$docker_cpus")" "$(lab_gb1 "$docker_mib")"; }

# ---------------------------------------------------------------------------- model provider
# Mirrors integrations/litellm/render_config.py: auto = azure -> openai -> anthropic -> gemini -> ollama.
model_state=
model_detail=
model_provider=
resolve_model() {
  _p=$(lower "$(eff LAB_MODEL_PROVIDER)")
  [ -n "$_p" ] || _p=auto
  _refs=
  for _v in LITELLM_MASTER_KEY AZURE_OPENAI_API_KEY AZURE_OPENAI_ENDPOINT AZURE_OPENAI_DEPLOYMENT OPENAI_API_KEY ANTHROPIC_API_KEY GEMINI_API_KEY; do
    is_ref "$_v" && _refs="$_refs $_v"
  done
  case $_p in
    auto)
      if [ -n "$_refs" ]; then
        model_state=ready; model_provider=1password
        model_detail="provider keys come from 1Password (start with op run)"; return
      fi
      if all_real AZURE_OPENAI_API_KEY AZURE_OPENAI_ENDPOINT AZURE_OPENAI_DEPLOYMENT; then _p=azure
      elif is_real OPENAI_API_KEY; then _p=openai
      elif is_real ANTHROPIC_API_KEY; then _p=anthropic
      elif is_real GEMINI_API_KEY; then _p=gemini
      else _p=ollama; fi ;;
    azure | openai | anthropic | gemini | ollama) ;;
    *)
      model_state=error; model_detail="LAB_MODEL_PROVIDER=$_p is not one of auto, azure, openai, anthropic, gemini, ollama"
      return ;;
  esac
  model_provider=$_p
  case $_p in
    azure) _need=$(missing_of AZURE_OPENAI_API_KEY AZURE_OPENAI_ENDPOINT AZURE_OPENAI_DEPLOYMENT)
      _desc="Azure OpenAI, deployment $(eff AZURE_OPENAI_DEPLOYMENT)" ;;
    openai) _need=$(missing_of OPENAI_API_KEY); _m=$(eff OPENAI_MODEL); _desc="OpenAI ${_m:-gpt-5.1}" ;;
    anthropic) _need=$(missing_of ANTHROPIC_API_KEY); _m=$(eff ANTHROPIC_MODEL); _desc="Anthropic ${_m:-claude-sonnet-5}" ;;
    gemini) _need=$(missing_of GEMINI_API_KEY); _m=$(eff GEMINI_MODEL); _desc="Google Gemini ${_m:-gemini-2.5-flash}" ;;
    ollama) _need=; _m=$(eff OLLAMA_CHAT_MODEL); _desc="local Ollama ${_m:-qwen3.5:4b} (no cloud key set; slow on a CPU)" ;;
  esac
  if [ -n "$_need" ]; then
    model_state=needs; model_detail="LAB_MODEL_PROVIDER=$_p needs $_need"
  elif [ -n "$_refs" ]; then
    model_state=ready; model_detail="$_desc, keys from 1Password (start with op run)"
  else
    model_state=ready; model_detail=$_desc
  fi
}

# ---------------------------------------------------------------------------- management server
# On premises: MANAGEMENT_HOST (+ MANAGEMENT_PORT) with an API key, or a user name and password.
# Smart-1 Cloud: S1C_URL with an API key only. MANAGEMENT_HOST wins when both are set
# (packages/infra api-manager.ts). A host or URL without a way to sign in is dropped by the
# Compose start script, and the servers start without management access.
has() { is_real "$1" || is_ref "$1"; }
mgmt_state() {
  # prints: none | onprem | s1c | nocreds | s1c-userpass | nohost
  if has MANAGEMENT_HOST; then
    if has MANAGEMENT_API_KEY || { has MANAGEMENT_USERNAME && has MANAGEMENT_PASSWORD; }; then printf onprem; else printf nocreds; fi
  elif has S1C_URL; then
    if has MANAGEMENT_API_KEY; then printf s1c
    elif has MANAGEMENT_USERNAME && has MANAGEMENT_PASSWORD; then printf s1c-userpass
    else printf nocreds; fi
  elif has MANAGEMENT_API_KEY || has MANAGEMENT_USERNAME || has MANAGEMENT_PASSWORD; then printf nohost
  else printf none; fi
}
mgmt_checks() {
  if is_real MANAGEMENT_HOST; then
    case $(eff MANAGEMENT_HOST) in *://* | */*) res fail "MANAGEMENT_HOST must be a host name or IP only (no https:// and no path)" ;; esac
  fi
  if is_real S1C_URL; then
    case $(eff S1C_URL) in https://?*/?*) ;; *) res fail "S1C_URL must be the Smart-1 Cloud Web API URL, for example https://<tenant>.maas.checkpoint.com/<id>/web_api" ;; esac
  fi
  _mp=$(eff MANAGEMENT_PORT)
  case $_mp in
    '') ;;
    *[!0-9]*) res fail "MANAGEMENT_PORT must be a port number (default 443)" ;;
    *) if [ "$_mp" -lt 1 ] || [ "$_mp" -gt 65535 ]; then res fail "MANAGEMENT_PORT must be from 1 to 65535"; fi ;;
  esac
  if has MANAGEMENT_USERNAME && ! has MANAGEMENT_PASSWORD; then
    res warn "MANAGEMENT_USERNAME is set without MANAGEMENT_PASSWORD. Set both or neither"
  elif has MANAGEMENT_PASSWORD && ! has MANAGEMENT_USERNAME; then
    res warn "MANAGEMENT_PASSWORD is set without MANAGEMENT_USERNAME. Set both or neither"
  fi
  if has MANAGEMENT_HOST && has S1C_URL; then
    res warn "MANAGEMENT_HOST and S1C_URL are both set: the servers use MANAGEMENT_HOST and ignore S1C_URL. Clear one of them"
  fi
  case $(mgmt_state) in
    none) return 0 ;;
    nocreds)
      res warn "the management server is set without MANAGEMENT_API_KEY (or MANAGEMENT_USERNAME and MANAGEMENT_PASSWORD): the Management MCP servers start without management access" ;;
    s1c-userpass)
      res fail "Smart-1 Cloud signs in with an API key only: set MANAGEMENT_API_KEY (MANAGEMENT_USERNAME and MANAGEMENT_PASSWORD work only with an on-premises MANAGEMENT_HOST)" ;;
    nohost)
      res warn "management credentials are set without MANAGEMENT_HOST or S1C_URL: the Management MCP servers have no server to connect to" ;;
  esac
  _cp_any=1
}
# mgmt_target: host:port for the reachability check (on premises, or the Smart-1 Cloud host).
mgmt_target() {
  if is_real MANAGEMENT_HOST; then
    _mt_p=$(eff MANAGEMENT_PORT)
    printf '%s:%s' "$(eff MANAGEMENT_HOST)" "${_mt_p:-443}"
  elif is_real S1C_URL; then
    _mt=$(eff S1C_URL)
    _mt=${_mt#*://}
    _mt=${_mt%%/*}
    case $_mt in *:*) printf '%s' "$_mt" ;; ?*) printf '%s:443' "$_mt" ;; esac
  fi
}

# ---------------------------------------------------------------------------- preflight
preflight() {
  section "Docker"
  if ! lab_have_docker; then
    res fail "Docker is not installed or not on PATH"
  elif [ "$docker_ok" = 0 ]; then
    res fail "Docker is installed but not running (start Docker Desktop or the Docker service)"
  else
    res ok "Docker is running"
    if docker compose version >/dev/null 2>&1 </dev/null || [ -n "${DOCKER_COMPOSE:-}" ]; then
      res ok "Docker Compose v2 is available"
    else
      res fail "'docker compose' (Compose v2) is not available"
    fi
    _info=$(docker info --format '{{.NCPU}} {{.MemTotal}} {{.OperatingSystem}}' 2>/dev/null </dev/null |
      awk 'NR == 1 && $1 ~ /^[0-9]+$/ && $2 ~ /^[0-9]+$/ { printf "%d %d %d", $1, $2 / 1048576, (index($0, "Docker Desktop") > 0) ? 1 : 0 }')
    if [ -z "$_info" ]; then
      res info "could not read the CPUs and memory Docker can use"
    else
      docker_cpus=${_info%% *}
      _rest=${_info#* }
      docker_mib=${_rest%% *}
      docker_desktop=${_rest#* }
      if ! lab_meets standard "$docker_cpus" "$docker_mib"; then
        res warn "Docker can use $(docker_can_use): less than the Standard lab needs (4 CPUs and 8 GB); $(raise_hint)"
      elif ! lab_meets complete "$docker_cpus" "$docker_mib"; then
        res ok "Docker can use $(docker_can_use): meets the Standard lab requirement (the Complete lab requires 6 CPUs and 16 GB)"
      else
        res ok "Docker can use $(docker_can_use): meets the Complete lab requirement (6 CPUs and 16 GB)"
      fi
    fi
    for _net in $(lab_external_networks); do
      if docker network inspect "$_net" >/dev/null 2>&1 </dev/null; then
        res ok "Docker network $_net exists"
      else
        res fail "Docker network $_net is missing (docker-compose.yml needs it): docker network create $_net"
      fi
    done
  fi

  section "Settings file"
  if [ "$have_env" = 0 ]; then
    res fail "$env_file not found: run ./setup.sh"
  else
    res ok "$env_file found"
    # shellcheck disable=SC2012
    _perm=$(ls -l "$env_file" 2>/dev/null | cut -c1-10)
    case $_perm in
      -rw-------) res ok ".env is readable only by you (mode 600)" ;;
      *) res warn ".env can be read by other users ($_perm): chmod 600 .env" ;;
    esac
  fi

  _refs=
  for _k in $F_KEYS; do is_ref "$_k" && _refs="$_refs $_k"; done
  if [ -n "$_refs" ]; then
    section "1Password"
    res info "$(printf '%s' "$_refs" | wc -w | tr -d ' ') settings are 1Password references that are not resolved in this shell:"
    res info "  ${_refs# }"
    res info "Start the lab with: op run --env-file=.env -- docker compose up -d"
    res info "and check it the same way: op run --env-file=.env -- ./scripts/doctor.sh --preflight"
    if ! command -v op >/dev/null 2>&1; then
      res fail "the 1Password CLI (op) is not installed, so these references cannot be resolved"
    fi
  fi

  section "Required settings"
  _req="N8N_ADMIN_EMAIL N8N_ADMIN_PASSWORD POSTGRES_USER POSTGRES_DB POSTGRES_PASSWORD N8N_ENCRYPTION_KEY N8N_USER_MANAGEMENT_JWT_SECRET LITELLM_MASTER_KEY MCP_GATEWAY_TOKEN NEXTAUTH_SECRET SALT LANGFUSE_ENCRYPTION_KEY LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY WEBUI_SECRET_KEY"
  _missing=
  _placeholder=
  for _k in $_req; do
    _v=$(eff "$_k")
    if [ -z "$_v" ]; then _missing="$_missing $_k"
    elif lab_is_op_ref "$_v"; then :
    elif lab_is_placeholder "$_v" || lab_is_public_default "$_v"; then _placeholder="$_placeholder $_k"
    fi
  done
  if [ -n "$_missing" ]; then res fail "not set:${_missing} (./setup.sh generates them)"; fi
  if [ -n "$_placeholder" ]; then
    res fail "placeholder or published training value:${_placeholder}"
    res info "  ./setup.sh replaces them on a lab that has not started yet. On a lab that has started, the apps"
    res info "  keep the old values: see the setup output, or change them in the apps first."
  fi
  if [ -z "$_missing$_placeholder" ]; then res ok "all $(printf '%s' "$_req" | wc -w | tr -d ' ') required settings are set"; fi

  if is_real N8N_ADMIN_PASSWORD; then
    if _why=$(lab_password_problem "$(eff N8N_ADMIN_PASSWORD)"); then
      res ok "N8N_ADMIN_PASSWORD meets the n8n and Flowise password rule"
    else
      res fail "N8N_ADMIN_PASSWORD $_why (rule: 8-64 characters, upper, lower, digit, one of - _ . ! @ %)"
    fi
  fi
  if is_real N8N_ADMIN_EMAIL && ! lab_email_ok "$(eff N8N_ADMIN_EMAIL)"; then
    res fail "N8N_ADMIN_EMAIL is not an email address"
  fi
  if is_real OPEN_WEBUI_ADMIN_PASSWORD && ! lab_password_problem "$(eff OPEN_WEBUI_ADMIN_PASSWORD)" >/dev/null; then
    res warn "OPEN_WEBUI_ADMIN_PASSWORD does not meet the lab password rule"
  fi
  if is_real POSTGRES_PASSWORD; then
    case $(eff POSTGRES_PASSWORD) in
      *[!A-Za-z0-9._~-]*) res fail "POSTGRES_PASSWORD may contain only letters, digits and . _ ~ - (it is part of the Langfuse database URL)" ;;
    esac
  fi
  if is_real LANGFUSE_ENCRYPTION_KEY; then
    _lk=$(eff LANGFUSE_ENCRYPTION_KEY)
    case $_lk in *[!0-9a-fA-F]*) _lk_ok=0 ;; *) [ ${#_lk} -eq 64 ] && _lk_ok=1 || _lk_ok=0 ;; esac
    [ "$_lk_ok" = 1 ] || res fail "LANGFUSE_ENCRYPTION_KEY must be 64 hex characters"
  fi
  if is_real LANGFUSE_PUBLIC_KEY; then case $(eff LANGFUSE_PUBLIC_KEY) in pk-lf-?*) ;; *) res fail "LANGFUSE_PUBLIC_KEY must start with pk-lf-" ;; esac; fi
  if is_real LANGFUSE_SECRET_KEY; then case $(eff LANGFUSE_SECRET_KEY) in sk-lf-?*) ;; *) res fail "LANGFUSE_SECRET_KEY must start with sk-lf-" ;; esac; fi
  if is_real LITELLM_MASTER_KEY; then
    case $(eff LITELLM_MASTER_KEY) in sk-????????????????*) ;; *) res fail "LITELLM_MASTER_KEY must start with sk- and be at least 19 characters" ;; esac
  fi
  if is_real MCP_GATEWAY_TOKEN; then
    _gt=$(eff MCP_GATEWAY_TOKEN)
    [ ${#_gt} -ge 16 ] || res fail "MCP_GATEWAY_TOKEN is shorter than 16 characters"
  fi

  section "Lab model (lab-chat)"
  resolve_model
  case $model_state in
    ready) res ok "lab-chat: $model_detail" ;;
    *) res fail "lab-chat: $model_detail" ;;
  esac
  _az=$(missing_of AZURE_OPENAI_API_KEY AZURE_OPENAI_ENDPOINT AZURE_OPENAI_DEPLOYMENT)
  if [ "$_az" != "AZURE_OPENAI_API_KEY AZURE_OPENAI_ENDPOINT AZURE_OPENAI_DEPLOYMENT" ] && [ -n "$_az" ] && [ "$model_provider" != azure ]; then
    res warn "Azure OpenAI is only partly set (missing $_az), so auto mode skips it"
  fi
  if is_real AZURE_OPENAI_ENDPOINT; then
    case $(eff AZURE_OPENAI_ENDPOINT) in https://?*) ;; *) res fail "AZURE_OPENAI_ENDPOINT must start with https://" ;; esac
  fi
  # A local chat model needs OLLAMA_MEM_LIMIT=6g (the 1g default holds the RAG embedder only).
  _oml=$(eff OLLAMA_MEM_LIMIT)
  _oml=${_oml:-1g}
  if [ -z "$(lab_mem_mib "$_oml")" ]; then
    res fail "OLLAMA_MEM_LIMIT=$_oml is not a memory size (for example 1g or 6g)"
  elif [ "$model_provider" = ollama ] || local_chat_on; then
    if [ "$model_provider" = ollama ]; then _why="lab-chat runs on the local Ollama model"; else _why="Open WebUI chats with a local Ollama model"; fi
    if ollama_mem_low; then
      res warn "$_why, but OLLAMA_MEM_LIMIT is $_oml: Ollama cannot load a chat model. Set OLLAMA_MEM_LIMIT=6g in .env, then docker compose up -d"
    else
      res ok "$_why; OLLAMA_MEM_LIMIT is $_oml"
    fi
    if [ -n "$docker_mib" ] && [ "$docker_mib" -lt "$LAB_COMPLETE_MIB" ]; then
      res warn "Local models need Docker with 16 GB of memory and run slowly on a CPU. Docker can use $(lab_gb1 "$docker_mib") GB: $(raise_hint)"
    elif [ "$model_provider" = ollama ]; then
      res info "local inference is slow on a CPU; a cloud model key makes the agents much faster"
    fi
  fi

  litellm_check

  if [ "$online" = 1 ]; then online_keys; fi

  section "Check Point settings"
  _cp_any=0
  _cp_issues=$((blockers + warnings))
  mgmt_checks
  for _grp in "Gaia:GAIA_GATEWAY_IP GAIA_USERNAME GAIA_PASSWORD" "Documentation:DOC_CLIENT_ID DOC_SECRET_KEY" \
              "Spark Management:SPARK_MGMT_CLIENT_ID SPARK_MGMT_SECRET_KEY" \
              "SASE:HARMONY_SASE_API_KEY HARMONY_SASE_MANAGEMENT_HOST HARMONY_SASE_ORIGIN" \
              "the IPS service key (Build Your Own MCP):IPS_CLIENT_ID IPS_ACCESS_KEY"; do
    _gname=${_grp%%:*}
    # shellcheck disable=SC2086
    _gmiss=$(missing_of ${_grp#*:})
    _gn=$(printf '%s' "${_grp#*:}" | wc -w | tr -d ' ')
    _gm=$(printf '%s' "$_gmiss" | wc -w | tr -d ' ')
    if [ "$_gm" -lt "$_gn" ]; then
      _cp_any=1
      [ "$_gm" -eq 0 ] || res warn "$_gname is only partly set (missing $_gmiss)"
    fi
  done
  if is_real DOC_CLIENT_ID; then
    case $(eff DOC_REGION) in EU | US) ;; *) res warn "DOC_REGION should be EU or US in uppercase" ;; esac
  fi
  for _su in HARMONY_SASE_MANAGEMENT_HOST HARMONY_SASE_ORIGIN; do
    if is_real "$_su"; then
      case $(eff "$_su") in https://?*) ;; *) res warn "$_su should be an address that starts with https:// (SASE administrator portal: Settings > API support)" ;; esac
    fi
  done
  if [ "$_cp_any" = 0 ] && ! is_real TE_API_KEY && ! is_real REPUTATION_API_KEY; then
    res info "no Check Point product is configured yet: agents start, and say the product is not configured"
  elif [ $((blockers + warnings)) -eq "$_cp_issues" ]; then
    res ok "Check Point settings are consistent"
  fi
  if [ "$online" = 1 ]; then online_tcp; fi

  section "Lab components (COMPOSE_PROFILES)"
  _profiles=$(eff COMPOSE_PROFILES | tr -d ' ')
  _lf=0; _ow=0
  service_on langflow && _lf=1
  service_on open-webui && _ow=1
  case $_lf$_ow in
    11) res ok "Complete lab: the Standard lab plus Langflow and Open WebUI with local models" ;;
    10) res ok "Standard lab plus Langflow" ;;
    01) res ok "Standard lab plus Open WebUI with local models" ;;
    *) res ok "Standard lab: n8n, Flowise, the MCP Gateway and all Check Point MCP servers, lab-chat, Langfuse and RAG (the Complete lab adds Langflow and Open WebUI: COMPOSE_PROFILES=complete)" ;;
  esac
  # "full" is the former name of the complete profile; setup renames it.
  _legacy=0
  if lab_profiles_legacy "$_profiles" && ! profile_defined full; then
    _legacy=1
    if [ "${COMPOSE_PROFILES+x}" = x ]; then
      res warn "the environment variable COMPOSE_PROFILES names full, the former name of complete, so Langflow and Open WebUI do not start. Change full to complete in the environment (on Dokploy: in the project's environment settings)"
    else
      res warn "COMPOSE_PROFILES in .env names full, the former name of complete, so Langflow and Open WebUI do not start. Run ./setup.sh again (it renames full to complete), or change full to complete in .env"
    fi
  fi
  if [ "$_lf$_ow" != 11 ] && [ "$_legacy" = 0 ] && is_real DOMAIN; then
    res warn "DOMAIN is set (a lab host), but COMPOSE_PROFILES does not include complete: Langflow and Open WebUI do not start. Lab hosts should set COMPOSE_PROFILES=complete (on Dokploy: in the project's environment settings)"
  fi
  if [ "$_lf$_ow" = 11 ] && [ -n "$docker_mib" ] && ! lab_meets complete "$docker_cpus" "$docker_mib"; then
    res warn "the Complete lab requires Docker with 6 CPUs and 16 GB of memory, and Docker can use $(docker_can_use). Services may be slow or restart: $(raise_hint), or use the Standard lab (remove complete from COMPOSE_PROFILES)"
  fi
  if [ "$_ow" = 1 ]; then
    _dm=$(eff OPEN_WEBUI_DEFAULT_MODELS); _dm=${_dm:-qwen3.5:4b}
    _om=$(eff OLLAMA_MODELS); _om=${_om:-qwen3.5:4b,nomic-embed-text}
    case ",$(printf '%s' "$_om" | tr -d ' ')," in
      *",$_dm,"*) ;;
      *) res warn "OPEN_WEBUI_DEFAULT_MODELS ($_dm) is not in OLLAMA_MODELS, so Open WebUI has no model to start new chats with" ;;
    esac
  fi
  case ",$_profiles," in
    ,,) ;;
    *",*,"*) res ok "COMPOSE_PROFILES=* turns on every profile" ;;
    *)
      for _pf in $(printf '%s' "$_profiles" | tr ',' ' '); do
        if [ "$_pf" = cpu ]; then
          res info "profile 'cpu' matches no service (harmless; remove it from COMPOSE_PROFILES)"
        elif [ "$_pf" = full ] && [ "$_legacy" = 1 ]; then
          :
        elif profile_defined "$_pf"; then
          res ok "profile $_pf"
        else
          res warn "profile $_pf matches no service in docker-compose.yml"
        fi
      done ;;
  esac
}

litellm_check() {
  if ! grep -q 'render_config.py' "$compose_file" 2>/dev/null; then
    res skip "LiteLLM config check: this docker-compose.yml does not render the lab-chat config yet"
    return
  fi
  if [ "$docker_ok" = 0 ]; then res skip "LiteLLM config check: Docker is not running"; return; fi
  _img=$(awk '/^  litellm:/ { s = 1; next } s && /^  [A-Za-z0-9_-]+:/ { s = 0 } s && $1 == "image:" { print $2; exit }' "$compose_file")
  if [ "$online" = 0 ] && ! docker image inspect "$_img" >/dev/null 2>&1 </dev/null; then
    res skip "LiteLLM config check: image not pulled yet (run with --online, or after docker compose pull)"
    return
  fi
  for _v in LITELLM_MASTER_KEY AZURE_OPENAI_API_KEY AZURE_OPENAI_ENDPOINT AZURE_OPENAI_DEPLOYMENT OPENAI_API_KEY ANTHROPIC_API_KEY GEMINI_API_KEY; do
    if is_ref "$_v"; then
      res skip "LiteLLM config check: $_v is a 1Password reference (run this check under op run)"
      return
    fi
  done
  _out="$LAB_TMP/litellm-check.txt"
  _rc=0
  lab_compose run --rm --no-deps -T litellm --check > "$_out" 2>&1 </dev/null || _rc=$?
  grep '\[lab-litellm\]' "$_out" | sed 's/^/        /' | head -n 12
  case $_rc in
    0) res ok "LiteLLM accepts the settings (docker compose run --rm --no-deps litellm --check)" ;;
    2) res fail "LiteLLM rejects the settings (see the [lab-litellm] lines above)" ;;
    *) res warn "LiteLLM config check could not run (exit $_rc): docker compose run --rm --no-deps litellm --check" ;;
  esac
  rm -f "$_out"
}

run_probe() {
  # run_probe NETWORK MODE NAME...: run stack_probe.py in one throwaway container; NAME... are passed by name.
  _rp_net=$1
  _rp_mode=$2
  shift 2
  _rp_args=
  for _rp_n in "$@"; do _rp_args="$_rp_args -e $_rp_n"; done
  # shellcheck disable=SC2086
  docker run --rm -i --cpus 0.5 --memory 256m --network "$_rp_net" $_rp_args \
    "$PROBE_IMAGE" python - "$_rp_mode" < "$PROBE" 2>/dev/null
}

record_probe() {
  # read probe output lines on stdin: print R lines as results, remember statuses as P_<check>
  while IFS='|' read -r _kind _check _st _detail; do
    _var=$(printf '%s' "$_check" | tr -c 'A-Za-z0-9' '_')
    case $_kind in
      R) eval "P_$_var=\$_st"; eval "PD_$_var=\$_detail"; res "$_st" "$_detail" ;;
      N) eval "PN_$_var=\$_st" ;;
    esac
  done
}
pstat() { lab_get P_ "$(printf '%s' "$1" | tr -c 'A-Za-z0-9' '_')"; }
pdet() { lab_get PD_ "$(printf '%s' "$1" | tr -c 'A-Za-z0-9' '_')"; }

online_keys() {
  section "Model keys (online)"
  _prov=
  all_real AZURE_OPENAI_API_KEY AZURE_OPENAI_ENDPOINT && _prov="$_prov,azure"
  is_real OPENAI_API_KEY && _prov="$_prov,openai"
  is_real ANTHROPIC_API_KEY && _prov="$_prov,anthropic"
  is_real GEMINI_API_KEY && _prov="$_prov,gemini"
  if [ -z "$_prov" ]; then res info "no cloud model key to validate"; return; fi
  [ "$docker_ok" = 1 ] || { res skip "Docker is not running"; return; }
  PROBE_PROVIDERS=${_prov#,}
  export PROBE_PROVIDERS
  export_vars AZURE_OPENAI_API_KEY AZURE_OPENAI_ENDPOINT AZURE_OPENAI_API_VERSION OPENAI_API_KEY OPENAI_BASE_URL ANTHROPIC_API_KEY GEMINI_API_KEY
  run_probe bridge keys PROBE_PROVIDERS AZURE_OPENAI_API_KEY AZURE_OPENAI_ENDPOINT AZURE_OPENAI_API_VERSION \
    OPENAI_API_KEY OPENAI_BASE_URL ANTHROPIC_API_KEY GEMINI_API_KEY > "$LAB_TMP/keys.txt"
  [ -s "$LAB_TMP/keys.txt" ] || res warn "the key check container did not run (needs image $PROBE_IMAGE)"
  # A rejected key is a blocker for the provider lab-chat uses, and a warning for any other provider.
  while IFS='|' read -r _kind _check _st _detail; do
    [ "$_kind" = R ] || continue
    _pp=${_check#key.}
    [ "$_st" = fail ] && [ "$_pp" != "$model_provider" ] && _st=warn
    eval "P_key_$_pp=\$_st"
    res "$_st" "$_pp: $_detail"
  done < "$LAB_TMP/keys.txt"
}

online_tcp() {
  _tcp=
  _mtg=$(mgmt_target)
  [ -n "$_mtg" ] && _tcp="management=$_mtg"
  if is_real GAIA_GATEWAY_IP; then
    _gp=$(eff GAIA_GATEWAY_PORT); _tcp="$_tcp,gaia=$(eff GAIA_GATEWAY_IP):${_gp:-443}"
  fi
  [ -n "$_tcp" ] || return 0
  [ "$docker_ok" = 1 ] || { res skip "TCP reachability: Docker is not running"; return 0; }
  PROBE_TCP=${_tcp#,}
  export PROBE_TCP
  run_probe bridge tcp PROBE_TCP > "$LAB_TMP/tcp.txt"
  # Unreachable hosts are warnings: the lab still starts.
  while IFS='|' read -r _kind _check _st _detail; do
    [ "$_kind" = R ] || continue
    [ "$_st" = fail ] && _st=warn
    _var=$(printf '%s' "$_check" | tr -c 'A-Za-z0-9' '_')
    eval "P_$_var=\$_st"
    eval "PD_$_var=\$_detail"
    res "$_st" "$_detail"
  done < "$LAB_TMP/tcp.txt"
}

# ---------------------------------------------------------------------------- post-start
# Credential-gated servers: they print "not configured" and exit 0 while one of these is blank.
gated="spark-management-mcp:SPARK_MGMT_CLIENT_ID,SPARK_MGMT_SECRET_KEY harmony-sase-mcp:HARMONY_SASE_API_KEY,HARMONY_SASE_MANAGEMENT_HOST,HARMONY_SASE_ORIGIN"
# One-shot model pulls: exit 0 when every model is present, 1 when a pull failed.
pullers="ollama-pull-models-cpu ollama-pull-chat-models"
running=
# oneshot_ok SERVICE CONTAINER: extra notes for a one-shot job that exited 0.
oneshot_ok() {
  if [ "$1" = builders-import ]; then
    # The seeder logs this fixed line when Langflow is not part of the setup (no log text is printed).
    # Only the latest run: compose restarts (not recreates) one-shot jobs, so older runs stay in the log.
    _bi_since=$(docker inspect -f '{{.State.StartedAt}}' "$2" 2>/dev/null </dev/null)
    if docker logs --since "${_bi_since:-0}" "$2" 2>&1 </dev/null | grep -q 'Langflow is not running'; then
      if service_on langflow; then
        res warn "builders-import ran while Langflow was not up, so the Langflow agents were not seeded: docker compose run --rm builders-import"
        return 0
      fi
      [ "$verbose" = 1 ] && res ok "builders-import: Flowise agents seeded; Langflow skipped (not in this setup)"
      return 0
    fi
  fi
  case " $pullers " in
    *" $1 "*) [ "$verbose" = 1 ] && res ok "$1: models ready (exit 0)"; return 0 ;;
  esac
  [ "$verbose" = 1 ] && res ok "$1: one-shot job finished (exit 0)"
  return 0
}
post_services() {
  section "Services"
  if [ "$docker_ok" = 0 ]; then res fail "Docker is not running"; return 1; fi
  lab_compose config --services > "$LAB_TMP/expected.txt" 2>/dev/null </dev/null || true
  if [ ! -s "$LAB_TMP/expected.txt" ]; then
    res fail "docker compose cannot read the lab configuration (run: docker compose config --quiet)"
    return 1
  fi
  lab_compose ps -a --format '{{.Service}}|{{.State}}|{{.Health}}|{{.ExitCode}}|{{.ID}}' > "$LAB_TMP/ps.txt" 2>/dev/null </dev/null || true
  # A one-shot job is a service whose CONFIGURED restart policy (the compose files) is "no".
  # The container's own policy can differ (an override file, docker update), so compose decides;
  # the container's policy is only the fallback. --no-interpolate: no .env value is rendered.
  lab_compose config --no-interpolate 2>/dev/null </dev/null | awk '
    /^services:/ { insvc = 1; next }
    /^[^ ]/ { insvc = 0 }
    insvc && /^  [A-Za-z0-9_.-]+:[ \t]*$/ { svc = $1; sub(/:$/, "", svc); next }
    insvc && svc != "" && /^    restart:/ { pol = $2; gsub(/["\047]/, "", pol); print svc "|" pol }
  ' > "$LAB_TMP/restart.txt" || true
  _ids=$(cut -d'|' -f5 "$LAB_TMP/ps.txt" | tr '\n' ' ')
  : > "$LAB_TMP/inspect.txt"
  if [ -n "$(printf '%s' "$_ids" | tr -d ' ')" ]; then
    # shellcheck disable=SC2086
    docker inspect -f '{{.Id}}|{{.HostConfig.RestartPolicy.Name}}|{{.RestartCount}}' $_ids > "$LAB_TMP/inspect.txt" 2>/dev/null </dev/null || true
  fi
  _n_ok=0; _n_bad=0; _n_off=0; _n_info=0
  while IFS= read -r _svc; do
    [ -n "$_svc" ] || continue
    _line=$(awk -F'|' -v s="$_svc" '$1 == s { print; exit }' "$LAB_TMP/ps.txt")
    if [ -z "$_line" ]; then
      res fail "$_svc: not running (never started: docker compose up -d)"; _n_off=$((_n_off + 1)); continue
    fi
    _state=$(printf '%s' "$_line" | cut -d'|' -f2)
    _health=$(printf '%s' "$_line" | cut -d'|' -f3)
    _code=$(printf '%s' "$_line" | cut -d'|' -f4)
    _id=$(printf '%s' "$_line" | cut -d'|' -f5)
    _pol=$(awk -F'|' -v s="$_svc" '$1 == s { print $2; exit }' "$LAB_TMP/restart.txt")
    [ -n "$_pol" ] || _pol=$(awk -F'|' -v i="$_id" 'index($1, i) == 1 { print $2; exit }' "$LAB_TMP/inspect.txt")
    _rc=$(awk -F'|' -v i="$_id" 'index($1, i) == 1 { print $3; exit }' "$LAB_TMP/inspect.txt")
    case $_pol in no | '') _oneshot=1 ;; *) _oneshot=0 ;; esac
    case $_state in
      running)
        running="$running $_svc"
        if [ "$_oneshot" = 1 ] && [ -z "$_health" ]; then
          case " $pullers " in
            *" $_svc "*) res info "$_svc: still pulling models (check again in a few minutes)" ;;
            *) res info "$_svc: one-shot job still running (check again in a few minutes)" ;;
          esac
          _n_info=$((_n_info + 1))
        elif [ "$_health" = unhealthy ]; then res fail "$_svc: running but unhealthy (docker compose logs $_svc)"; _n_bad=$((_n_bad + 1))
        elif [ "$_health" = starting ]; then res info "$_svc: starting"; _n_info=$((_n_info + 1))
        else
          _n_ok=$((_n_ok + 1))
          [ "$verbose" = 1 ] && res ok "$_svc: running${_health:+ ($_health)}"
          if [ "${_rc:-0}" -gt 5 ] 2>/dev/null; then res warn "$_svc restarted $_rc times (docker compose logs $_svc)"; fi
        fi ;;
      restarting)
        res fail "$_svc: crash loop (restarted ${_rc:-several} times; docker compose logs $_svc)"; _n_bad=$((_n_bad + 1)) ;;
      exited)
        if [ "$_oneshot" = 1 ]; then
          if [ "$_code" = 0 ]; then
            _n_ok=$((_n_ok + 1))
            oneshot_ok "$_svc" "$_id"
          else
            case " $pullers " in
              *" $_svc "*) res fail "$_svc: a model could not be pulled (exit $_code; network?). See docker compose logs $_svc, then retry: docker compose up -d $_svc" ;;
              *) res fail "$_svc: one-shot job failed (exit $_code; docker compose logs $_svc)" ;;
            esac
            _n_bad=$((_n_bad + 1))
          fi
        else
          _gmiss=
          for _g in $gated; do
            [ "${_g%%:*}" = "$_svc" ] || continue
            for _gvar in $(printf '%s' "${_g#*:}" | tr ',' ' '); do has "$_gvar" || _gmiss="$_gmiss $_gvar"; done
          done
          if [ "$_code" = 0 ] && [ -n "$_gmiss" ]; then
            res info "$_svc: stopped because it is not configured (${_gmiss# } not set)"; _n_info=$((_n_info + 1))
          else
            res fail "$_svc: not running (exited $_code; docker compose up -d $_svc)"; _n_off=$((_n_off + 1))
          fi
        fi ;;
      *) res fail "$_svc: $_state (docker compose up -d $_svc)"; _n_off=$((_n_off + 1)) ;;
    esac
  done < "$LAB_TMP/expected.txt"
  res info "$_n_ok healthy, $_n_bad failing, $_n_off not running, $_n_info other (of $(grep -c . "$LAB_TMP/expected.txt") services)"

  # A container that received a literal op:// value was started without op run.
  _opfound=
  for _s in $running; do
    _cid=$(awk -F'|' -v s="$_s" '$1 == s { print $5; exit }' "$LAB_TMP/ps.txt")
    _names=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$_cid" 2>/dev/null </dev/null | awk -F= 'index($0, "=op://") > 0 { print $1 }' | tr '\n' ' ')
    [ -n "$(printf '%s' "$_names" | tr -d ' ')" ] && _opfound="$_opfound $_s($(printf '%s' "$_names" | sed 's/ $//'))"
  done
  if [ -n "$_opfound" ]; then
    res fail "unresolved 1Password references inside:$_opfound. Restart with: op run --env-file=.env -- docker compose up -d"
  fi
  return 0
}

post_network() {
  _pn=
  for _s in mcp-gateway $running; do
    _cid=$(awk -F'|' -v s="$_s" '$1 == s && $2 == "running" { print $5; exit }' "$LAB_TMP/ps.txt")
    [ -n "$_cid" ] || continue
    _ext=" $(lab_external_networks | tr '\n' ' ') "
    for _n in $(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' "$_cid" 2>/dev/null </dev/null); do
      case $_ext in *" $_n "*) continue ;; esac
      # The isolated Security Lab network reaches only vuln-mcp (post_security_lab checks it), and the
      # AI-Infra-Guard network only the scanner and the lab model (LiteLLM is on it too).
      case $_n in *security-lab | *ai-red-team) continue ;; esac
      _pn=$_n
      break
    done
    [ -n "$_pn" ] && break
  done
  printf '%s' "$_pn"
}

post_probe() {
  section "In-network checks"
  _net=$(post_network)
  if [ -z "$_net" ]; then res fail "no lab service is running: docker compose up -d"; return; fi
  PROBE_SERVICES=$(printf '%s' "$running" | sed 's/^ //' | tr ' ' ',')
  # Direct path: every gateway server from the catalog whose sidecar is running.
  _servers=$(sed -n 's/.*--servers=\([^"]*\)".*/\1/p' "$compose_file" | head -n 1)
  PROBE_DIRECT=
  if [ -f "$LAB_ROOT/mcp-gateway/catalog.yaml" ]; then
    PROBE_DIRECT=$(awk -v servers=",$_servers," -v running=",$PROBE_SERVICES," '
      /^  [A-Za-z0-9_-]+:[ \t]*$/ { name = $1; sub(/:$/, "", name); next }
      $1 == "url:" && name != "" {
        u = $2; gsub(/"/, "", u)
        h = u; sub(/^[a-z]+:\/\//, "", h); sub(/[:\/].*$/, "", h)
        if (index(servers, "," name ",") && index(running, "," h ",")) { out = out sep name "=" u; sep = "," }
      }
      END { print out }' "$LAB_ROOT/mcp-gateway/catalog.yaml")
  fi
  PROBE_EXPECT_N8N=$(find "$LAB_ROOT/n8n/backup/workflows" -name '*.json' 2>/dev/null | grep -c .)
  PROBE_EXPECT_FLOWISE=$(find "$LAB_ROOT/integrations/flowise" -name '*.flowdata.json' 2>/dev/null | grep -c .)
  PROBE_EXPECT_LANGFLOW=$(find "$LAB_ROOT/integrations/langflow" -name '*.flow.json' 2>/dev/null | grep -c .)
  PROBE_CHAT=$chat
  _fp=$(eff FLOWISE_PORT)
  FLOWISE_PORT=${_fp:-3020}
  export PROBE_SERVICES PROBE_DIRECT PROBE_EXPECT_N8N PROBE_EXPECT_FLOWISE PROBE_EXPECT_LANGFLOW PROBE_CHAT FLOWISE_PORT
  export_vars MCP_GATEWAY_TOKEN N8N_ADMIN_EMAIL N8N_ADMIN_PASSWORD LITELLM_MASTER_KEY LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY QDRANT_API_KEY
  for _v in MCP_GATEWAY_TOKEN N8N_ADMIN_PASSWORD LITELLM_MASTER_KEY LANGFUSE_SECRET_KEY; do
    is_ref "$_v" && res warn "$_v is an unresolved 1Password reference here; run: op run --env-file=.env -- ./scripts/doctor.sh --post-start"
  done
  _pmode=post
  [ "$mode" = health ] && _pmode=health
  run_probe "$_net" "$_pmode" PROBE_SERVICES PROBE_DIRECT PROBE_EXPECT_N8N PROBE_EXPECT_FLOWISE PROBE_EXPECT_LANGFLOW \
    PROBE_CHAT FLOWISE_PORT MCP_GATEWAY_TOKEN N8N_ADMIN_EMAIL N8N_ADMIN_PASSWORD LITELLM_MASTER_KEY \
    LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY QDRANT_API_KEY > "$LAB_TMP/probe.txt"
  if [ ! -s "$LAB_TMP/probe.txt" ]; then
    res fail "the in-network check container did not run (image $PROBE_IMAGE, network $_net)"
    return
  fi
  record_probe < "$LAB_TMP/probe.txt"
}

# The MCP Security Lab server is only on its own internal network (<project>_security-lab), not on
# the main lab network: a second throwaway container joins that network to check it.
post_security_lab() {
  is_running vuln-mcp || return 0
  _slc=$(awk -F'|' '$1 == "vuln-mcp" && $2 == "running" { print $5; exit }' "$LAB_TMP/ps.txt")
  _sln=
  for _n in $(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' "$_slc" 2>/dev/null </dev/null); do
    case $_n in *security-lab) _sln=$_n; break ;; esac
  done
  [ -n "$_sln" ] || _sln="$(lab_project_name)_security-lab"
  run_probe "$_sln" security-lab > "$LAB_TMP/security-lab.txt"
  if [ ! -s "$LAB_TMP/security-lab.txt" ]; then
    res fail "the MCP Security Lab check container did not run (image $PROBE_IMAGE, network $_sln)"
    return 0
  fi
  record_probe < "$LAB_TMP/security-lab.txt"
}

is_running() { case " $running " in *" $1 "*) return 0 ;; esac; return 1; }

# ---------------------------------------------------------------------------- capability matrix
need_or_ready() {
  # need_or_ready PATH DETAIL_WHEN_READY NAME...: "ready" when every NAME is set, else "needs NAMES"
  _nr_path=$1; _nr_ready=$2; shift 2
  _nr_miss=$(missing_of "$@")
  if [ -z "$_nr_miss" ]; then row ready "$_nr_path" "$_nr_ready"; else row needs "$_nr_path" "$_nr_miss"; fi
}
live() {
  # live PATH CHECK SERVICE CONFIG_STATE CONFIG_DETAIL: refine a row with a post-start result
  _lv_st=$(pstat "$2")
  if [ "$mode" = preflight ]; then row "$4" "$1" "$5"; return; fi
  if [ -n "$3" ] && ! is_running "$3"; then row needs "$1" "$3 running (docker compose up -d $3)"; return; fi
  case $_lv_st in
    ok) row ready "$1" "$(pdet "$2")" ;;
    fail) row error "$1" "$(pdet "$2")" ;;
    *) row "$4" "$1" "$5" ;;
  esac
}
profile_path() {
  # profile_path PATH PROFILE DETAIL [CHECK SERVICE]: an opt-in path; with CHECK and SERVICE the
  # post-start result refines it once the profile is on.
  if ! profile_defined "$2"; then row ready "$1" "runs with the default lab"
  elif profile_on "$2"; then
    if [ -n "${4:-}" ]; then live "$1" "$4" "$5" ready "$3"; else row ready "$1" "$3"; fi
  else row off "$1" "add $2 to COMPOSE_PROFILES in .env, then docker compose up -d"; fi
}
# setup_path PATH SERVICE PROFILE CHECK STATE DETAIL: a part of the Complete lab. Off = not a failure.
setup_path() {
  if service_on "$2"; then live "$1" "$4" "$2" "$5" "$6"
  else row off "$1" "not in this setup: add complete (or $3) to COMPOSE_PROFILES in .env, then docker compose up -d"; fi
}

matrix() {
  [ -n "$model_state" ] || resolve_model
  _base=$(missing_of LITELLM_MASTER_KEY MCP_GATEWAY_TOKEN N8N_ADMIN_PASSWORD)
  if [ "$model_state" = ready ]; then _ms=ready; else _ms=needs; fi
  _md=$model_detail
  if [ "$model_provider" = ollama ] && ollama_mem_low; then
    _mdn=$(eff OLLAMA_MEM_LIMIT)
    _ms=needs; _md="OLLAMA_MEM_LIMIT=6g for the local model (now ${_mdn:-1g})"
  fi
  live "lab-chat model" litellm.chat litellm "$_ms" "$_md"
  live "MCP gateway path" gateway.tools mcp-gateway "$( [ -z "$(missing_of MCP_GATEWAY_TOKEN)" ] && echo ready || echo needs)" \
    "$( [ -z "$(missing_of MCP_GATEWAY_TOKEN)" ] && echo "http://mcp-gateway:8080/mcp with MCP_GATEWAY_TOKEN" || echo MCP_GATEWAY_TOKEN)"
  if [ "$mode" = preflight ]; then row ready "Direct MCP path (no gateway)" "sidecar URLs from docker-compose.yml"
  else
    _dt=$(lab_get PN_ direct_tools)
    if [ -n "$_dt" ]; then row ready "Direct MCP path (no gateway)" "$_dt tools from the running servers"; else row needs "Direct MCP path (no gateway)" "the MCP servers running"; fi
  fi
  if [ -n "$_base" ]; then _bs=needs; _bd=$_base; elif [ "$model_state" != ready ]; then _bs=needs; _bd="a working lab-chat model"; else _bs=ready; _bd="lab-chat + MCP tools"; fi
  if [ "$mode" != health ]; then
    live "Agents in n8n" seed.n8n n8n "$_bs" "$_bd"
    live "Agents in Flowise" seed.flowise flowise "$_bs" "$_bd"
    setup_path "Agents in Langflow" langflow langflow seed.langflow "$_bs" "$_bd"
    _cm=$(missing_of LITELLM_MASTER_KEY MCP_GATEWAY_TOKEN)
    if [ -z "$_cm" ]; then row ready "Code-first agent" "integrations/code-agent (LiteLLM + gateway)"
    else row needs "Code-first agent" "$_cm"; fi
  fi
  _mrow="Check Point: Management (7 servers)"
  case $(mgmt_state) in
    none) row needs "$_mrow" "MANAGEMENT_HOST (or S1C_URL) and MANAGEMENT_API_KEY" ;;
    nocreds) row needs "$_mrow" "MANAGEMENT_API_KEY (or MANAGEMENT_USERNAME and MANAGEMENT_PASSWORD)" ;;
    s1c-userpass) row needs "$_mrow" "MANAGEMENT_API_KEY (Smart-1 Cloud signs in with an API key only)" ;;
    nohost) row needs "$_mrow" "MANAGEMENT_HOST or S1C_URL" ;;
    *)
      if [ "$(pstat tcp.management)" = warn ]; then row check "$_mrow" "$(pdet tcp.management)"
      elif [ "$(mgmt_state)" = s1c ]; then row ready "$_mrow" "Smart-1 Cloud"
      else _mpt=$(eff MANAGEMENT_PORT); row ready "$_mrow" "MANAGEMENT_HOST $(eff MANAGEMENT_HOST), port ${_mpt:-443}"; fi ;;
  esac
  need_or_ready "Check Point: Gaia" "gateway $(eff GAIA_GATEWAY_IP)" GAIA_GATEWAY_IP GAIA_USERNAME GAIA_PASSWORD
  need_or_ready "Check Point: Documentation" "region $(eff DOC_REGION)" DOC_CLIENT_ID DOC_SECRET_KEY
  need_or_ready "Check Point: Threat Emulation" "TE_API_KEY set" TE_API_KEY
  need_or_ready "Check Point: Reputation Service" "REPUTATION_API_KEY set" REPUTATION_API_KEY
  need_or_ready "Check Point: Spark Management" "SPARK_MGMT_* set" SPARK_MGMT_CLIENT_ID SPARK_MGMT_SECRET_KEY
  need_or_ready "Check Point: SASE" "HARMONY_SASE_* set" HARMONY_SASE_API_KEY HARMONY_SASE_MANAGEMENT_HOST HARMONY_SASE_ORIGIN
  [ "$mode" = health ] && return 0
  _lf=$(missing_of NEXTAUTH_SECRET SALT LANGFUSE_ENCRYPTION_KEY LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY)
  live "Langfuse tracing" langfuse.trace langfuse "$( [ -z "$_lf" ] && echo ready || echo needs)" "$( [ -z "$_lf" ] && echo "LiteLLM sends every lab-chat call" || echo "$_lf")"
  if ollama_mem_low; then
    _owm=$(eff OLLAMA_MEM_LIMIT)
    setup_path "Open WebUI (local chat)" open-webui local-chat openwebui.health needs "OLLAMA_MEM_LIMIT=6g to load a chat model (now ${_owm:-1g})"
  else
    setup_path "Open WebUI (local chat)" open-webui local-chat openwebui.health ready "admin from OPEN_WEBUI_ADMIN_* or the lab admin"
  fi
  # The default model pull always includes nomic-embed-text.
  live "RAG (Documentation RAG Agent)" rag.qdrant qdrant ready "local embeddings (nomic-embed-text)"
  need_or_ready "Lakera Guard agents" "LAKERA_API_KEY set" LAKERA_API_KEY
  need_or_ready "Identity Provisioning (SCIM)" "https://idp.$(eff DOMAIN)/scim/v2" IDP_SCIM_TOKEN DOMAIN
  need_or_ready "DevHub Operations Agent" "https://hub.$(eff DOMAIN)/api/mcp" DEVHUB_MCP_TOKEN DOMAIN
  need_or_ready "PolicyPilot agents" "https://policypilot.$(eff DOMAIN)/mcp" PILOT_MCP_TOKEN DOMAIN
  profile_path "Build Your Own MCP exercise" exercises "profile on$( is_real IPS_ACCESS_KEY || echo '; live CVE data needs IPS_CLIENT_ID + IPS_ACCESS_KEY')"
  profile_path "MCP Security Lab" security-lab "profile on (simulated, intentionally vulnerable server)" securitylab.mcp vuln-mcp
  profile_path "AI-Infra-Guard" ai-red-team "profile on"
  profile_path "Evals" evals "profile on: docker compose run --rm evals-run"
}

print_matrix() {
  section "What works with these settings"
  while IFS='|' read -r _st _path _detail; do
    printf '  %-6s  %-36s %s\n' "$_st" "$_path" "$_detail"
  done < "$rows"
}

# ---------------------------------------------------------------------------- main
lab_say "Check Point AI agent lab doctor ($mode)"
lab_say "Lab directory: $LAB_ROOT"
case $mode in
  preflight)
    preflight ;;
  post | health)
    if [ "$have_env" = 0 ]; then res warn "$env_file not found: checks use the shell environment only"; fi
    post_services
    post_probe
    [ "$mode" = post ] && post_security_lab ;;
esac
if [ "$mode" != health ]; then
  matrix
  print_matrix
fi

lab_say ""
if [ "$blockers" -gt 0 ]; then
  lab_say "Result: $blockers blocker(s), $warnings warning(s). Fix the FAIL lines above."
  [ "$mode" = post ] && lab_say "Next: fix the FAIL lines, then run tests/acceptance/run.sh for the end-to-end checks."
  exit 1
fi
lab_say "Result: no blockers, $warnings warning(s)."
[ "$mode" = post ] && lab_say "Next: run tests/acceptance/run.sh for the end-to-end checks."
exit 0
