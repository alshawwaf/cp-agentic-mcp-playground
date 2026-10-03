#!/bin/sh
# Check Point AI agent lab: guided setup. Writes .env once, with every key the lab needs.
#
#   ./setup.sh                    guided setup (asks questions; typed keys are hidden)
#   ./setup.sh --non-interactive  no questions: takes keys from the environment and generates the
#                                 lab secrets (aliases: -y, --ci). Used by CI.
#   ./setup.sh --1password        guided setup that writes op://vault/item/field references for your
#                                 keys; start the lab with: op run --env-file=.env -- docker compose up -d
#   ./setup.sh --help
#
# The first step chooses the lab components. Setup shows the CPUs and memory of this machine and
# what Docker can use, offers the Standard lab (no profile; Docker with 4 CPUs and 8 GB) or the
# Complete lab (COMPOSE_PROFILES=complete: adds Langflow and Open WebUI with local models; Docker
# with 6 CPUs and 16 GB), marks the one this machine can run as recommended, and writes
# COMPOSE_PROFILES. --non-interactive takes COMPOSE_PROFILES from the environment (blank = the
# Standard lab). Lab hosts use COMPOSE_PROFILES=complete. An existing COMPOSE_PROFILES that names
# "full" (the former name of "complete") is renamed.
#
# Re-running is safe: existing values are kept, blank ones are filled, and setup asks before it
# changes a value. It never prints a secret. POSIX sh: runs with /bin/sh on macOS and Linux.

# shellcheck disable=SC2329  # trap handlers and input checks are called indirectly
set -u
umask 077

LAB_ROOT=$(cd "$(dirname "$0")" && pwd -P) || exit 2
# shellcheck source=SCRIPTDIR/scripts/lib/labenv.sh
. "$LAB_ROOT/scripts/lib/labenv.sh"

ENV_FILE="$LAB_ROOT/.env"
TEMPLATE="$LAB_ROOT/.env-example"

usage() {
  cat <<'EOF'
Usage: ./setup.sh [--non-interactive | --1password] [--help]

Creates or updates .env for the Check Point AI agent lab.

  (no option)         Guided setup: choose the lab components and the model provider, enter the
                      Check Point keys you have (each step can be skipped), and let setup
                      generate every lab secret.
  --non-interactive   No questions (aliases: -y, --ci). Keys come from environment variables with
                      the same names as in .env-example; every lab secret is generated. The lab
                      components come from COMPOSE_PROFILES in the environment: blank = the
                      Standard lab, complete = the Complete lab.
  --1password         Guided setup that writes 1Password references (op://vault/item/field)
                      instead of your keys. Start the lab with:
                        op run --env-file=.env -- docker compose up -d

Lab components:
  Standard lab        n8n and Flowise agent builders, the MCP Gateway and all Check Point MCP
                      servers, lab-chat model access, Langfuse tracing and RAG.
                      Requires Docker with 4 CPUs and 8 GB of memory.
  Complete lab        Everything in the Standard lab, plus the Langflow agent builder and
                      Open WebUI with local models (COMPOSE_PROFILES=complete).
                      Requires Docker with 6 CPUs and 16 GB of memory.

Existing values are kept, blank ones are filled, and setup asks before it changes a value.
.env is written with mode 600 and no secret is ever printed.
EOF
}

mode=interactive
op_mode=0
for arg in "$@"; do
  case $arg in
    --non-interactive | --ci | -y) mode=auto ;;
    --1password | --op) op_mode=1 ;;
    -h | --help) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n\n' "$arg" >&2; usage >&2; exit 2 ;;
  esac
done
if [ "$op_mode" = 1 ] && [ "$mode" = auto ]; then
  lab_die "--1password is a guided mode. For unattended runs put op:// references in the environment or in .env." 2
fi

# ---------------------------------------------------------------------------- terminal handling
is_tty=0
[ -t 0 ] && is_tty=1
stty_saved=
stty_hidden=0
written=0
tmp_env=

restore_tty() {
  if [ "$stty_hidden" = 1 ]; then
    if [ -n "$stty_saved" ]; then stty "$stty_saved" 2>/dev/null || stty echo 2>/dev/null; else stty echo 2>/dev/null; fi
    stty_hidden=0
  fi
}
on_exit() {
  restore_tty
  [ -n "$tmp_env" ] && rm -f "$tmp_env"
  lab_tmp_cleanup
}
on_signal() {
  [ "$is_tty" = 1 ] && stty_hidden=1
  restore_tty
  printf '\n'
  if [ "$written" = 1 ]; then
    lab_say "Setup stopped. .env was already written."
  else
    lab_say "Setup cancelled. Nothing was written."
  fi
  exit 130
}
trap on_exit EXIT
trap on_signal INT TERM HUP
if [ "$is_tty" = 1 ]; then stty_saved=$(stty -g 2>/dev/null) || stty_saved=; fi

lab_tmp_init

ANSWER=
input_eof=0

trim() {
  printf '%s' "$1" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'
}

# ask PROMPT: read one line into ANSWER (trimmed). Returns 1 when the input has ended.
ask() {
  printf '%s' "$1"
  ANSWER=
  if [ "$input_eof" = 1 ]; then printf '\n'; return 1; fi
  if IFS= read -r ANSWER || [ -n "$ANSWER" ]; then
    [ "$is_tty" = 1 ] || printf '\n'
    ANSWER=$(trim "$ANSWER")
    return 0
  fi
  input_eof=1
  ANSWER=
  printf '\n'
  return 1
}

# ask_hidden PROMPT: like ask, but the typed text is not shown on a terminal. Echo goes off before
# the prompt appears, so nothing typed or pasted right after the prompt is shown.
ask_hidden() {
  ANSWER=
  if [ "$input_eof" = 1 ]; then printf '%s\n' "$1"; return 1; fi
  if [ "$is_tty" = 1 ]; then
    stty_hidden=1
    stty -echo 2>/dev/null
  fi
  printf '%s' "$1"
  _ah_rc=0
  IFS= read -r ANSWER || [ -n "$ANSWER" ] || _ah_rc=1
  restore_tty
  printf '\n'
  if [ "$_ah_rc" = 1 ]; then input_eof=1; ANSWER=; return 1; fi
  ANSWER=$(trim "$ANSWER")
  return 0
}

# yes_no PROMPT DEFAULT(y|n): returns 0 for yes.
yes_no() {
  if [ "$2" = y ]; then _yn_hint="[Y/n]"; else _yn_hint="[y/N]"; fi
  while :; do
    if ! ask "$1 $_yn_hint "; then [ "$2" = y ]; return; fi
    case $ANSWER in
      '') [ "$2" = y ]; return ;;
      [Yy] | [Yy][Ee][Ss]) return 0 ;;
      [Nn] | [Nn][Oo]) return 1 ;;
    esac
    lab_say "  Please answer y or n."
  done
}

# ---------------------------------------------------------------------------- value store
# V_<NAME> = value that will be written, S_<NAME> = where it came from (old, template, env, entered,
# generated, 1password). Names only are ever printed.
val() { lab_get V_ "$1"; }
src() { lab_get S_ "$1"; }
setv() {
  # setv NAME VALUE SOURCE
  case $2 in
    *"'"*)
      lab_warn "$1: values with a single quote cannot be stored in .env by setup; edit .env by hand."
      return 1 ;;
  esac
  eval "V_$1=\$2"
  eval "S_$1=\$3"
  return 0
}
note_list() {
  # note_list VAR NAME: append NAME to the space-separated list in VAR
  eval "_nl=\${$1:-}"
  # shellcheck disable=SC2154
  case " $_nl " in *" $2 "*) ;; *) eval "$1=\"\${_nl} \$2\"" ;; esac
}
list_or_none() {
  if [ -n "$(trim "$1")" ]; then trim "$1"; else printf '%s' "none"; fi
}

# ---------------------------------------------------------------------------- start
lab_say "Check Point AI agent lab setup"
lab_say "=============================="
if [ "$mode" = auto ]; then lab_say "Mode: non-interactive (keys from the environment)."; fi
if [ "$op_mode" = 1 ]; then lab_say "Mode: 1Password references."; fi
if [ "$mode" = interactive ] && [ "$is_tty" = 0 ]; then
  lab_warn "input is not a terminal, so typed keys cannot be hidden. They are read as plain lines."
fi
lab_say ""

# Prerequisites
if ! lab_have_docker; then
  lab_die "Docker is not installed or not on PATH. Install Docker Desktop (macOS) or Docker Engine with the Compose plugin (Linux), then run ./setup.sh again."
fi
if ! docker compose version >/dev/null 2>&1 </dev/null; then
  lab_die "'docker compose' (Compose v2) is not available. Install the Docker Compose plugin, then run ./setup.sh again."
fi
docker_running=1
if ! lab_docker_up; then
  docker_running=0
  lab_warn "Docker is not running. Setup still writes .env; start Docker before 'docker compose up -d'."
fi
# CPUs and memory (MiB) of this machine and what Docker can use (empty when unknown).
host_cpus=$(lab_host_cpus)
host_mib=$(lab_host_mem_mib)
docker_cpus=
docker_mib=
docker_desktop=0
if [ "$docker_running" = 1 ]; then
  _dr=$(lab_docker_resources)
  if [ -n "$_dr" ]; then
    docker_cpus=${_dr%% *}
    _dr=${_dr#* }
    docker_mib=${_dr%% *}
    docker_desktop=${_dr#* }
  fi
fi
# The recommendation and the requirement checks use what Docker can use, else this machine.
if [ -n "$docker_mib" ]; then
  res_basis=docker; res_cpus=$docker_cpus; res_mib=$docker_mib
else
  res_basis=host; res_cpus=$host_cpus; res_mib=$host_mib
fi
res_known=0
[ -n "$res_cpus" ] && [ -n "$res_mib" ] && res_known=1
[ -f "$TEMPLATE" ] || lab_die ".env-example not found next to setup.sh ($TEMPLATE)." 2
if [ -e "$ENV_FILE" ] && [ ! -f "$ENV_FILE" ]; then
  lab_die "$ENV_FILE exists but is not a regular file." 2
fi

# Template: active settings (T_) and optional settings shown commented out (# NAME=value).
lab_env_load "$TEMPLATE" T_ || lab_die "cannot read .env-example" 2
optional_keys=$(sed -n 's/^#[[:space:]]*\([A-Z][A-Z0-9_]*\)=.*/\1/p' "$TEMPLATE" | awk '!seen[$0]++' | tr '\n' ' ')
# shellcheck disable=SC2153  # T_KEYS is set by lab_env_load
known_keys="$T_KEYS"
for k in $optional_keys; do
  case " $known_keys " in *" $k "*) ;; *) known_keys="$known_keys $k" ;; esac
done

have_old=0
O_KEYS=
if [ -f "$ENV_FILE" ]; then
  lab_env_load "$ENV_FILE" O_ || lab_die "cannot read $ENV_FILE" 2
  have_old=1
  lab_say "Found an existing .env: its values are kept and only blank settings are filled."
fi

for k in $known_keys; do
  if [ -n "$(lab_get O_SET_ "$k")" ]; then
    eval "V_$k=\${O_$k}"; eval "S_$k=old"
  else
    eval "V_$k=\${T_$k:-}"; eval "S_$k=template"
  fi
done

# Settings that nothing reads any more (n8n 2.x ignores basic auth; the port variables are unused).
# Flowise always uses the lab Postgres now (FLOWISE_DATABASE_TYPE), and the SASE MCP server takes
# HARMONY_SASE_ORIGIN instead of a region.
retired="N8N_BASIC_AUTH_ACTIVE N8N_BASIC_AUTH_USER N8N_BASIC_AUTH_PASSWORD N8N_USER_MANAGEMENT_EMAIL N8N_USER_MANAGEMENT_FIRST_NAME N8N_USER_MANAGEMENT_LAST_NAME N8N_USER_MANAGEMENT_PASSWORD N8N_USER_MANAGEMENT_DISABLED N8N_PORT POSTGRES_PORT OLLAMA_PORT OLLAMA_HOST LANGFLOW_PORT OPEN_WEBUI_PORT AIG_PORT DEPLOY_FLOWISE DEPLOY_LANGFLOW FLOWISE_DATABASE_TYPE HARMONY_SASE_REGION"
removed=
extra_keys=
for k in $O_KEYS; do
  case " $known_keys " in *" $k "*) continue ;; esac
  case " $retired " in *" $k "*) note_list removed "$k"; continue ;; esac
  extra_keys="$extra_keys $k"
done
flowise_sqlite_note=0
case " $removed " in *" FLOWISE_DATABASE_TYPE "*)
  if [ "$(lab_get O_ FLOWISE_DATABASE_TYPE)" != postgres ] && [ -f "$LAB_ROOT/flowise_data/database.sqlite" ]; then
    flowise_sqlite_note=1
  fi ;;
esac

# Old defaults that this version replaces (only when the value is exactly the old default).
migrations=
migrate() {
  # migrate NAME OLD_DEFAULT NEW_VALUE
  if [ "$(src "$1")" = old ] && [ "$(val "$1")" = "$2" ]; then
    note_list migrations "$1"
    eval "MIG_$1=\$3"
  fi
}
migrate OLLAMA_MODELS "gemma4:e2b,qwen3.5:4b,nomic-embed-text" "$(lab_get T_ OLLAMA_MODELS)"
migrate OLLAMA_MODELS "gemma4:e2b,qwen3.5:4b" "$(lab_get T_ OLLAMA_MODELS)"
migrate OPEN_WEBUI_DEFAULT_MODELS "gemma4:e2b" "$(lab_get T_ OPEN_WEBUI_DEFAULT_MODELS)"
migrate OLLAMA_KEEP_ALIVE "-1" "$(lab_get T_ OLLAMA_KEEP_ALIVE)"
migrate COMPOSE_PROFILES "cpu" ""
migrate ANTHROPIC_MODEL "claude-opus-4-8" ""
migrate AIG_LLM_MODEL "huihui_ai/deepseek-r1-abliterated:8b" ""
migrate AIG_LLM_MODEL "huihui_ai/deepseek-r1-abliterated:latest" ""
migrate AIG_LLM_BASE_URL "http://ollama-cpu:11434/v1" ""
migrate AIG_LLM_API_KEY "ollama" ""
migrate DOC_REGION "eu" "EU"
migrate DOC_REGION "us" "US"
if [ -n "$migrations" ]; then
  lab_say ""
  lab_say "These settings still hold an old default that this version replaces:"
  lab_say "  $(trim "$migrations")"
  apply_migrations=1
  if [ "$mode" = interactive ]; then
    yes_no "Update them to the current defaults?" y || apply_migrations=0
  fi
  if [ "$apply_migrations" = 1 ]; then
    for k in $migrations; do eval "setv $k \"\${MIG_$k}\" updated"; done
    lab_say "  Updated to the current defaults."
  fi
fi

# The "full" profile is now called "complete": rename it in COMPOSE_PROFILES, keeping the other
# profiles (and where the value came from).
rename_full_profile() {
  _rf=$(val COMPOSE_PROFILES)
  lab_profiles_legacy "$_rf" || return 1
  _rf_new=$(printf '%s' "$_rf" | tr -d ' ' | awk -F, '
    { o = ""; for (i = 1; i <= NF; i++) { p = ($i == "full") ? "complete" : $i; if (p == "" || seen[p]++) continue; o = o (o == "" ? "" : ",") p }; printf "%s", o }')
  setv COMPOSE_PROFILES "$_rf_new" "$(src COMPOSE_PROFILES)" || return 1
  lab_say "COMPOSE_PROFILES: full renamed to complete."
  return 0
}
rename_full_profile
if lab_profiles_legacy "${COMPOSE_PROFILES:-}"; then
  lab_warn "the environment variable COMPOSE_PROFILES names full, the former name of complete. Docker Compose reads the environment before .env: change it to complete (on Dokploy: in the project's environment settings)."
fi

# Published training values are never kept: the importers re-sync both on every start.
for k in MCP_GATEWAY_TOKEN LITELLM_MASTER_KEY; do
  if lab_is_public_default "$(val "$k")"; then
    setv "$k" "" template
    note_list replaced_public "$k"
  fi
done
if [ -n "${replaced_public:-}" ]; then
  lab_say "Replacing published training values (a new random value is generated): $(trim "$replaced_public")"
fi

deployment=$(lab_deployment_state)

# Langfuse creates its organization on first start and never renames it. A lab that ran before
# LANGFUSE_ORG_ID existed has the organization cp-playground: keep that ID (a new one would add a second,
# empty organization). New labs use the .env-example value.
if [ "$have_old" = 1 ] && [ "$(src LANGFUSE_ORG_ID)" != old ]; then
  _lf_keep=0
  case $deployment in
    yes) _lf_keep=1 ;;
    unknown) [ -n "$(lab_get O_ LANGFUSE_PUBLIC_KEY)" ] && _lf_keep=1 ;;
  esac
  if [ "$_lf_keep" = 1 ]; then
    setv LANGFUSE_ORG_ID cp-playground migrated
    lab_say "LANGFUSE_ORG_ID=cp-playground: this lab keeps its Langfuse organization."
  fi
fi

# ---------------------------------------------------------------------------- prompts
op_vault=
op_item=
op_fields=

# prompt_secret NAME LABEL: a key the user supplies (hidden input, or a 1Password reference).
prompt_secret() {
  _ps_cur=$(val "$1")
  if lab_is_placeholder "$_ps_cur"; then _ps_cur=; fi
  if [ "$op_mode" = 1 ]; then
    _ps_ref="op://$op_vault/$op_item/$1"
    if lab_is_op_ref "$_ps_cur"; then
      lab_say "  $2: 1Password reference kept ($_ps_cur)."
      return 0
    fi
    if [ -n "$_ps_cur" ]; then
      yes_no "  $2 has a value in .env. Replace it with the 1Password reference $_ps_ref?" n || return 0
    fi
    setv "$1" "$_ps_ref" 1password && note_list op_fields "$1"
    lab_say "  $2: $_ps_ref"
    return 0
  fi
  while :; do
    if [ -n "$_ps_cur" ]; then
      ask_hidden "  $2 [set; Enter keeps it]: " || return 0
      [ -z "$ANSWER" ] && return 0
      [ "$ANSWER" = "$_ps_cur" ] && return 0
      _ps_new=$ANSWER
      yes_no "  Replace the current $1?" n || return 0
      ANSWER=$_ps_new
    else
      ask_hidden "  $2 (Enter skips): " || return 0
      [ -z "$ANSWER" ] && return 0
    fi
    setv "$1" "$ANSWER" entered && return 0
  done
}

# prompt_text NAME LABEL [CHECK]: a plain value; CHECK is a function that prints a problem and
# returns 1 for a bad value (it may rewrite ANSWER, e.g. strip https://).
prompt_text() {
  _pt_cur=$(val "$1")
  if lab_is_placeholder "$_pt_cur"; then _pt_cur=; fi
  while :; do
    if [ -n "$_pt_cur" ]; then
      ask "  $2 [$_pt_cur]: " || return 0
      [ -z "$ANSWER" ] && return 0
      [ "$ANSWER" = "$_pt_cur" ] && return 0
    else
      ask "  $2 (Enter skips): " || return 0
      [ -z "$ANSWER" ] && return 0
    fi
    if [ -n "${3:-}" ]; then
      if ! _pt_msg=$("$3" "$ANSWER"); then
        lab_say "    $_pt_msg"
        continue
      fi
      [ -n "$_pt_msg" ] && ANSWER=$_pt_msg
    fi
    if [ -n "$_pt_cur" ] && [ "$(src "$1")" = old ]; then
      _pt_new=$ANSWER
      yes_no "  Change $1 from $_pt_cur to $_pt_new?" n || return 0
      ANSWER=$_pt_new
    fi
    setv "$1" "$ANSWER" entered && return 0
  done
}

check_host() {
  # host name or IP only; strips a scheme and path
  _ch=${1#*://}
  _ch=${_ch%%/*}
  case $_ch in
    '' | *[!A-Za-z0-9.:_-]*) printf '%s' "Enter a host name or IP address only, for example 203.0.113.10"; return 1 ;;
  esac
  if [ "$_ch" != "$1" ]; then printf '%s' "$_ch"; fi
  return 0
}
check_https_url() {
  case $1 in
    https://?*) printf '%s' "${1%/}"; return 0 ;;
  esac
  printf '%s' "Enter an address that starts with https://, for example https://my-resource.openai.azure.com"
  return 1
}
check_s1c_url() {
  # the Web API URL of a Smart-1 Cloud tenant; a copied ".../login" suffix is removed
  _cs=${1%/}
  _cs=${_cs%/login}
  case $_cs in
    https://?*/?*) printf '%s' "$_cs"; return 0 ;;
  esac
  printf '%s' "Enter the Web API URL from the Smart-1 Cloud portal, for example https://<tenant>.maas.checkpoint.com/<id>/web_api"
  return 1
}
check_sase_url() {
  case $1 in
    https://?*) printf '%s' "${1%/}"; return 0 ;;
  esac
  printf '%s' "Enter the address from the SASE administrator portal (it starts with https://)"
  return 1
}
check_port() {
  case $1 in '' | *[!0-9]*) printf '%s' "Enter a port number, for example 443"; return 1 ;; esac
  if [ "$1" -lt 1 ] || [ "$1" -gt 65535 ]; then printf '%s' "Enter a port number from 1 to 65535"; return 1; fi
  return 0
}
check_region() {
  _cr=$(printf '%s' "$1" | tr 'abcdefghijklmnopqrstuvwxyz' 'ABCDEFGHIJKLMNOPQRSTUVWXYZ')
  case $_cr in EU | US) printf '%s' "$_cr"; return 0 ;; esac
  printf '%s' "Enter EU or US"
  return 1
}
check_domain() {
  case $1 in
    *://* | */* | *[!A-Za-z0-9.-]* | .* | *.) printf '%s' "Enter a domain name only, for example lab.example.com"; return 1 ;;
    *.*) return 0 ;;
  esac
  printf '%s' "Enter a domain name only, for example lab.example.com"
  return 1
}
check_name() {
  case $1 in
    *[!A-Za-z0-9_.-]*) printf '%s' "Use letters, digits, dot, dash or underscore only (no spaces)"; return 1 ;;
  esac
  return 0
}

group_is_set() {
  for _g in "$@"; do
    _gv=$(val "$_g")
    if [ -n "$_gv" ] && ! lab_is_placeholder "$_gv"; then return 0; fi
  done
  return 1
}

# group TITLE NAME...: ask whether to set up a product; returns 0 when the user wants to.
group() {
  _gt=$1
  shift
  if group_is_set "$@"; then
    _gt_first=$(printf '%s' "$_gt" | cut -c1 | tr 'abcdefghijklmnopqrstuvwxyz' 'ABCDEFGHIJKLMNOPQRSTUVWXYZ')
    yes_no "${_gt_first}${_gt#?} is configured. Change it?" n
  else
    yes_no "Set up $_gt now?" n
  fi
}

current_provider() {
  _cp=$(val LAB_MODEL_PROVIDER)
  case $_cp in azure | openai | anthropic | gemini | ollama) printf '%s' "$_cp"; return ;; esac
  if group_is_set AZURE_OPENAI_API_KEY; then printf '%s' azure
  elif group_is_set OPENAI_API_KEY; then printf '%s' openai
  elif group_is_set ANTHROPIC_API_KEY; then printf '%s' anthropic
  elif group_is_set GEMINI_API_KEY; then printf '%s' gemini
  else printf '%s' none; fi
}

# ---------------------------------------------------------------------------- lab components and resources
# Standard lab = the default (no profile): n8n, Flowise, the MCP Gateway and the Check Point MCP
# servers, lab-chat, Langfuse and RAG. Complete lab = COMPOSE_PROFILES with "complete" (adds
# Langflow and Open WebUI with local models). Lab hosts use the Complete lab.

# resources_lines PREFIX BASIS_NOTE: what this machine has and what Docker can use.
resources_lines() {
  if [ -n "$host_cpus" ] && [ -n "$host_mib" ]; then _rl="This machine: $(lab_cpus "$host_cpus"), $(lab_gb "$host_mib") GB of memory."
  elif [ -n "$host_cpus" ]; then _rl="This machine: $(lab_cpus "$host_cpus") (memory unknown)."
  elif [ -n "$host_mib" ]; then _rl="This machine: $(lab_gb "$host_mib") GB of memory (CPUs unknown)."
  else _rl="This machine: CPUs and memory unknown."; fi
  if [ -n "$docker_mib" ]; then
    lab_say "$1$_rl Docker can use $(lab_cpus "$docker_cpus") and $(lab_gb1 "$docker_mib") GB."
  elif [ "$docker_running" = 0 ]; then
    lab_say "$1$_rl"
    lab_say "${1}Docker is not running, so $2 is based on this machine."
  else
    lab_say "$1$_rl"
    lab_say "${1}Docker did not report its CPUs and memory, so $2 is based on this machine."
  fi
}
recommended_setup() {
  # Complete lab on a lab host (DOMAIN set) and when Docker can use 6 CPUs and 16 GB; else Standard lab.
  if [ -n "$(val DOMAIN)" ]; then printf '%s' complete; return; fi
  if lab_meets complete "$res_cpus" "$res_mib"; then printf '%s' complete; else printf '%s' standard; fi
}
resource_hinted=0
resource_hint() {
  # resource_hint: how to give Docker more CPUs and memory (once per run).
  [ "$resource_hinted" = 1 ] && return 0
  resource_hinted=1
  if [ "$docker_desktop" = 1 ]; then
    lab_say "  Increase it in Docker Desktop > Settings > Resources, then apply and restart."
    if [ -n "$host_mib" ] && [ "$host_mib" -lt 12288 ]; then
      lab_say "  This machine has $(lab_gb "$host_mib") GB of memory: leave at least 4 GB of it for the system."
    fi
  else
    lab_say "  Use a machine with more CPUs and memory for Docker."
  fi
}
# standard_warning PREFIX: a clear warning when Docker can use less than the Standard lab needs.
standard_warning() {
  [ "$res_known" = 1 ] || return 1
  lab_meets standard "$res_cpus" "$res_mib" && return 1
  if [ "$res_basis" = docker ]; then
    lab_say "${1}Warning: Docker can use less than the Standard lab needs (4 CPUs and 8 GB)."
  else
    lab_say "${1}Warning: This machine has less than the Standard lab needs (4 CPUs and 8 GB for Docker)."
  fi
  resource_hint
  return 0
}
complete_short() {
  # complete_short: the machine is known to have less than the Complete lab requires.
  [ "$res_known" = 1 ] && ! lab_meets complete "$res_cpus" "$res_mib"
}
# requirement_advice KIND PREFIX: warn (never fail) when the machine is below what KIND requires.
requirement_advice() {
  standard_warning "$2" && return 0
  if [ "$1" = complete ] && complete_short; then
    lab_say "${2}Warning: This machine has less than the Complete lab requires (6 CPUs and 16 GB for Docker). Services may be slow or restart."
    resource_hint
  fi
  return 0
}
profiles_without() {
  # profiles_without LIST NAME: LIST without NAME (comma-separated, spaces removed)
  printf '%s' "$1" | tr -d ' ' | awk -F, -v drop="$2" '
    { o = ""; for (i = 1; i <= NF; i++) if ($i != "" && $i != drop) o = o (o == "" ? "" : ",") $i; printf "%s", o }'
}
# apply_setup KIND: write the choice into COMPOSE_PROFILES, keeping the other optional parts.
apply_setup() {
  _as_cur=$(val COMPOSE_PROFILES | tr -d ' ')
  [ "$(lab_setup_kind "$_as_cur")" = "$1" ] && return 0
  if [ "$1" = complete ]; then
    _as_new=${_as_cur:+$_as_cur,}complete
  else
    case ",$_as_cur," in *",*,"*)
      lab_say "  COMPOSE_PROFILES=* turns on every profile; edit .env to change it."
      return 0 ;;
    esac
    _as_new=$(profiles_without "$_as_cur" complete)
  fi
  setv COMPOSE_PROFILES "$_as_new" entered
}
# show_components PREFIX: the chosen lab components and the COMPOSE_PROFILES value.
show_components() {
  _shc=$(val COMPOSE_PROFILES)
  if [ -n "$_shc" ]; then _shc_p="COMPOSE_PROFILES=$_shc"; else _shc_p="COMPOSE_PROFILES is blank"; fi
  lab_say "${1}Lab components: $(lab_kind_label "$(lab_setup_kind "$_shc")") ($_shc_p)."
}
# ran_langflow_before: this Compose project has a Langflow container (an earlier version started
# Langflow by default; now it needs the complete or langflow profile).
ran_langflow_before() {
  [ "$docker_running" = 1 ] || return 1
  [ "$deployment" = yes ] || return 1
  _rl=$(docker ps -aq --filter "label=com.docker.compose.project=$(lab_project_name)" \
    --filter "label=com.docker.compose.service=langflow" 2>/dev/null </dev/null | head -n 1)
  [ -n "$_rl" ]
}
langflow_note() {
  if [ "$(lab_setup_kind "$(val COMPOSE_PROFILES)")" = standard ] && ! lab_profiles_has "$(val COMPOSE_PROFILES)" langflow && ran_langflow_before; then
    lab_say "  This lab ran Langflow before. Langflow now starts only in the Complete lab or with the langflow"
    lab_say "  profile: choose the Complete lab, or add langflow to COMPOSE_PROFILES, to keep it."
  fi
}

step_setup() {
  lab_say "Step 1 of 6: Lab components"
  lab_say ""
  resources_lines "  " "the recommendation"
  lab_say ""
  standard_warning "  " && lab_say ""
  _ss_cur=$(lab_setup_kind "$(val COMPOSE_PROFILES)")
  _ss_rec=$(recommended_setup)
  _ss_m1=; _ss_m2=
  if [ "$_ss_rec" = complete ]; then _ss_m2="   (recommended for this machine)"; else _ss_m1="   (recommended for this machine)"; fi
  lab_say "  Choose the components to run:"
  lab_say "    1) Standard lab$_ss_m1"
  lab_say "       n8n and Flowise agent builders, the MCP Gateway and all Check Point MCP servers,"
  lab_say "       lab-chat model access, Langfuse tracing and RAG."
  lab_say "       Requires Docker with 4 CPUs and 8 GB of memory."
  lab_say "    2) Complete lab$_ss_m2"
  lab_say "       Everything in the Standard lab, plus the Langflow agent builder and Open WebUI with local models."
  lab_say "       Requires Docker with 6 CPUs and 16 GB of memory."
  langflow_note
  lab_say ""
  # A new .env starts at the recommendation; an existing one keeps its choice unless you change it.
  if [ "$have_old" = 1 ]; then _ss_def=$_ss_cur; else _ss_def=$_ss_rec; fi
  if [ "$_ss_def" = complete ]; then _ss_def=2; else _ss_def=1; fi
  _ss_asked=0
  while :; do
    _ss_typed=1
    ask "  Enter 1 or 2 [$_ss_def]: " || { ANSWER=$_ss_def; _ss_typed=0; }
    [ -z "$ANSWER" ] && ANSWER=$_ss_def
    case $ANSWER in
      1 | [Ss]tandard | [Ss]tandard\ lab) _ss_pick=standard ;;
      2 | [Cc]omplete | [Cc]omplete\ lab) _ss_pick=complete ;;
      *) lab_say "    Enter 1 or 2."; continue ;;
    esac
    if [ "$_ss_pick" = complete ] && [ "$_ss_typed" = 1 ] && complete_short; then
      _ss_asked=1
      lab_say "  This machine has less than the Complete lab requires (6 CPUs and 16 GB for Docker)."
      lab_say "  Services may be slow or restart."
      if ! yes_no "  Continue with the Complete lab?" n; then
        _ss_def=1
        continue
      fi
    fi
    break
  done
  apply_setup "$_ss_pick"
  show_components "  "
  if [ "$_ss_pick" = complete ] && [ "$_ss_asked" = 0 ] && complete_short; then
    requirement_advice complete "  "
  fi
  lab_say ""
}

# Non-interactive: report the lab components that COMPOSE_PROFILES gives, with the same warnings.
report_setup() {
  _rp_kind=$(lab_setup_kind "$(val COMPOSE_PROFILES)")
  show_components ""
  resources_lines "  " "the requirement check"
  if [ "$_rp_kind" = standard ]; then
    lab_say "  For the Complete lab (adds Langflow and Open WebUI with local models), set"
    lab_say "  COMPOSE_PROFILES=complete in the environment or in .env."
    if [ -n "$(val DOMAIN)" ]; then
      lab_warn "DOMAIN is set (a lab host), but COMPOSE_PROFILES does not include complete, so Langflow and Open WebUI do not start. Lab hosts should set COMPOSE_PROFILES=complete (on Dokploy: in the project's environment settings)."
    fi
  fi
  langflow_note
  requirement_advice "$_rp_kind" ""
}

# lab_chat_local: lab-chat will run on the local Ollama model. Mirrors the model puller in
# docker-compose.yml: LAB_MODEL_PROVIDER=ollama, or auto with no cloud provider fully set.
lab_chat_local() {
  case $(val LAB_MODEL_PROVIDER | tr 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' 'abcdefghijklmnopqrstuvwxyz') in
    ollama) return 0 ;;
    auto | '') ;;
    *) return 1 ;;
  esac
  group_is_set OPENAI_API_KEY ANTHROPIC_API_KEY GEMINI_API_KEY && return 1
  if group_is_set AZURE_OPENAI_API_KEY && group_is_set AZURE_OPENAI_ENDPOINT && group_is_set AZURE_OPENAI_DEPLOYMENT; then return 1; fi
  return 0
}

# apply_ollama_memory: a local chat model needs OLLAMA_MEM_LIMIT=6g (the 1g default holds the RAG
# embedder only). Raised when it still has the template value; asked (or warned) when it came from .env.
ollama_noted=0
apply_ollama_memory() {
  if lab_chat_local; then
    _om_why="lab-chat runs on the local Ollama model (no cloud model key is set)"
  elif lab_profiles_local_chat "$(val COMPOSE_PROFILES)"; then
    _om_why="Open WebUI chats with a local Ollama model in this setup"
  else
    return 0
  fi
  _om_cur=$(val OLLAMA_MEM_LIMIT)
  _om_mib=$(lab_mem_mib "${_om_cur:-1g}")
  if [ -z "$_om_mib" ] || [ "$_om_mib" -lt "$LAB_OLLAMA_CHAT_MIB" ]; then
    case $(src OLLAMA_MEM_LIMIT) in
      template | generated)
        setv OLLAMA_MEM_LIMIT 6g generated
        lab_say "  OLLAMA_MEM_LIMIT set to 6g: $_om_why." ;;
      *)
        if [ "$mode" = interactive ]; then
          lab_say "  $_om_why, which needs OLLAMA_MEM_LIMIT=6g (now ${_om_cur:-1g})."
          yes_no "  Change OLLAMA_MEM_LIMIT to 6g?" y && setv OLLAMA_MEM_LIMIT 6g entered
        else
          lab_warn "$_om_why, which needs OLLAMA_MEM_LIMIT=6g. It is ${_om_cur:-1g} (kept from .env or the environment): Ollama cannot load a chat model until you raise it."
        fi ;;
    esac
  fi
  [ "$ollama_noted" = 1 ] && return 0
  ollama_noted=1
  lab_warn "Local models need Docker with 16 GB of memory and run slowly on a CPU."
  if [ -n "$docker_mib" ] && [ "$docker_mib" -lt "$LAB_COMPLETE_MIB" ]; then
    lab_say "  Docker can use $(lab_gb1 "$docker_mib") GB now."
    resource_hint
  fi
  if lab_chat_local; then
    lab_say "  A cloud model key (run ./setup.sh again and choose a provider) makes every agent much faster."
  fi
}

# ---------------------------------------------------------------------------- complete groups
# all_or_none NAME...: print the missing names when some, but not all, are set.
all_or_none() {
  _an_miss=
  _an_have=0
  for _an in "$@"; do
    if group_is_set "$_an"; then _an_have=1; else _an_miss="$_an_miss $_an"; fi
  done
  [ "$_an_have" = 1 ] && printf '%s' "${_an_miss# }"
  return 0
}
# mgmt_missing: what the Management server settings still need (empty = complete, or none set).
mgmt_missing() {
  _mm_h=0; _mm_s=0; _mm_k=0; _mm_u=0; _mm_p=0
  group_is_set MANAGEMENT_HOST && _mm_h=1
  group_is_set S1C_URL && _mm_s=1
  group_is_set MANAGEMENT_API_KEY && _mm_k=1
  group_is_set MANAGEMENT_USERNAME && _mm_u=1
  group_is_set MANAGEMENT_PASSWORD && _mm_p=1
  if [ "$_mm_u" != "$_mm_p" ]; then
    if [ "$_mm_u" = 1 ]; then printf '%s' MANAGEMENT_PASSWORD; else printf '%s' MANAGEMENT_USERNAME; fi
  elif [ "$_mm_h" = 1 ]; then
    [ "$_mm_k" = 1 ] || [ "$_mm_u" = 1 ] || printf '%s' "MANAGEMENT_API_KEY (or MANAGEMENT_USERNAME and MANAGEMENT_PASSWORD)"
  elif [ "$_mm_s" = 1 ]; then
    [ "$_mm_k" = 1 ] || printf '%s' "MANAGEMENT_API_KEY (Smart-1 Cloud signs in with an API key only)"
  elif [ "$_mm_k" = 1 ] || [ "$_mm_u" = 1 ]; then
    printf '%s' "MANAGEMENT_HOST or S1C_URL"
  fi
  return 0
}
# finish_group TITLE PROMPT_FN CHECK_FN NAME...: a product needs all of its settings or none.
# CHECK_FN NAME... prints what is missing; offer to enter it (PROMPT_FN) or to clear NAME...
finish_group() {
  _fg_title=$1
  _fg_prompt=$2
  _fg_check=$3
  shift 3
  while _fg_miss=$("$_fg_check" "$@"); [ -n "$_fg_miss" ]; do
    lab_say "    $_fg_title is only partly set: $_fg_miss is missing. Set all of it, or none."
    [ "$input_eof" = 1 ] && return 0
    if yes_no "    Enter it now?" y; then "$_fg_prompt"; continue; fi
    if yes_no "    Clear the $_fg_title settings?" y; then
      for _fg in "$@"; do setv "$_fg" "" entered; done
      lab_say "    Cleared. The $_fg_title agents say that the product is not configured."
    fi
    return 0
  done
  return 0
}
# clear_offer PROMPT NAME...: when NAME... has a value, ask (default yes) to clear it.
clear_offer() {
  _co_q=$1
  shift
  group_is_set "$@" || return 0
  if yes_no "$_co_q" y; then
    for _co in "$@"; do setv "$_co" "" entered; done
  fi
}

prompt_management() {
  lab_say "  Where is the management server?"
  lab_say "    1) On premises (Security Management Server or Multi-Domain Server)"
  lab_say "    2) Smart-1 Cloud"
  _pm_def=1
  if group_is_set S1C_URL && ! group_is_set MANAGEMENT_HOST; then _pm_def=2; fi
  while :; do
    ask "  Choice [$_pm_def]: " || ANSWER=$_pm_def
    [ -z "$ANSWER" ] && ANSWER=$_pm_def
    case $ANSWER in 1 | 2) _pm_where=$ANSWER ;; *) lab_say "    Enter 1 or 2."; continue ;; esac
    break
  done
  if [ "$_pm_where" = 1 ]; then
    lab_say "  The host must be reachable from this Docker host (on CloudShare: the public IP)."
    prompt_text MANAGEMENT_HOST "Management server host name or IP" check_host
    [ -n "$(val MANAGEMENT_PORT)" ] || setv MANAGEMENT_PORT 443 template
    prompt_text MANAGEMENT_PORT "Management API port" check_port
    clear_offer "  S1C_URL is set too, and the servers use MANAGEMENT_HOST when both are set. Clear S1C_URL?" S1C_URL
    lab_say "  Sign in with:"
    lab_say "    1) An API key (recommended)"
    lab_say "    2) An administrator user name and password"
    _pm_adef=1
    if group_is_set MANAGEMENT_USERNAME && ! group_is_set MANAGEMENT_API_KEY; then _pm_adef=2; fi
    while :; do
      ask "  Choice [$_pm_adef]: " || ANSWER=$_pm_adef
      [ -z "$ANSWER" ] && ANSWER=$_pm_adef
      case $ANSWER in 1 | 2) _pm_auth=$ANSWER ;; *) lab_say "    Enter 1 or 2."; continue ;; esac
      break
    done
    if [ "$_pm_auth" = 1 ]; then
      prompt_secret MANAGEMENT_API_KEY "Management API key"
      clear_offer "  MANAGEMENT_USERNAME and MANAGEMENT_PASSWORD are set too; an API key does not need them. Clear them?" MANAGEMENT_USERNAME MANAGEMENT_PASSWORD
    else
      prompt_text MANAGEMENT_USERNAME "Administrator user name"
      prompt_secret MANAGEMENT_PASSWORD "Administrator password"
      clear_offer "  MANAGEMENT_API_KEY is set too, and the servers use the API key first. Clear MANAGEMENT_API_KEY?" MANAGEMENT_API_KEY
    fi
  else
    lab_say "  Smart-1 Cloud portal: Settings > API & SmartConsole. Copy the Web API URL (without /login)"
    lab_say "  and create an API key there. Smart-1 Cloud signs in with an API key only."
    prompt_text S1C_URL "Smart-1 Cloud Web API URL" check_s1c_url
    prompt_secret MANAGEMENT_API_KEY "Smart-1 Cloud API key"
    clear_offer "  MANAGEMENT_HOST is set too, and the servers use it instead of Smart-1 Cloud. Clear MANAGEMENT_HOST?" MANAGEMENT_HOST
    clear_offer "  MANAGEMENT_USERNAME and MANAGEMENT_PASSWORD are set; Smart-1 Cloud does not use them. Clear them?" MANAGEMENT_USERNAME MANAGEMENT_PASSWORD
  fi
}
prompt_gaia() {
  prompt_text GAIA_GATEWAY_IP "Gateway host name or IP" check_host
  prompt_text GAIA_GATEWAY_PORT "Gaia port" check_port
  prompt_text GAIA_USERNAME "Gaia user name"
  prompt_secret GAIA_PASSWORD "Gaia password"
}
prompt_documentation() {
  prompt_text DOC_CLIENT_ID "Client ID"
  prompt_secret DOC_SECRET_KEY "Secret key"
  prompt_text DOC_REGION "Region (EU or US)" check_region
}
prompt_spark() {
  prompt_text SPARK_MGMT_CLIENT_ID "Client ID"
  prompt_secret SPARK_MGMT_SECRET_KEY "Secret key"
  prompt_text SPARK_MGMT_REGION "Region (EU or US)" check_region
}
prompt_sase() {
  lab_say "  SASE administrator portal: Settings > API support. The server needs all three settings."
  prompt_secret HARMONY_SASE_API_KEY "SASE API key"
  prompt_text HARMONY_SASE_MANAGEMENT_HOST "Management API URL (https://api.<management-host>/api)" check_sase_url
  prompt_text HARMONY_SASE_ORIGIN "Origin domain (https://<tenant-origin-domain>)" check_sase_url
}
prompt_ips() {
  prompt_text IPS_CLIENT_ID "IPS service client ID"
  prompt_secret IPS_ACCESS_KEY "IPS service access key"
}

step_1password() {
  lab_say "1Password"
  lab_say "  Your keys stay in 1Password. .env gets references such as op://<vault>/<item>/<field>,"
  lab_say "  and 'op run' fills them in when the lab starts. Field names are the setting names"
  lab_say "  (for example MANAGEMENT_API_KEY). Use names without spaces."
  if ! command -v op >/dev/null 2>&1; then
    lab_warn "the 1Password CLI (op) is not installed. Install it before you start the lab."
  fi
  op_vault=Private
  op_item=checkpoint-ai-lab
  ask "  Vault [$op_vault]: " && [ -n "$ANSWER" ] && op_vault=$ANSWER
  while ! _m=$(check_name "$op_vault"); do lab_say "    $_m"; ask "  Vault [Private]: " || break; op_vault=${ANSWER:-Private}; done
  ask "  Item [$op_item]: " && [ -n "$ANSWER" ] && op_item=$ANSWER
  while ! _m=$(check_name "$op_item"); do lab_say "    $_m"; ask "  Item [checkpoint-ai-lab]: " || break; op_item=${ANSWER:-checkpoint-ai-lab}; done
  check_name "$op_vault" >/dev/null || op_vault=Private
  check_name "$op_item" >/dev/null || op_item=checkpoint-ai-lab
  lab_say ""
}

step_model() {
  lab_say "Step 2 of 6: Lab model"
  lab_say "  Every agent in n8n, Flowise and Langflow uses one model, \"lab-chat\", served by LiteLLM."
  lab_say "  Choose the provider for lab-chat:"
  lab_say "    1) Azure OpenAI"
  lab_say "    2) OpenAI"
  lab_say "    3) Anthropic"
  lab_say "    4) Google Gemini"
  lab_say "    5) Local model only (Ollama, no key; runs slowly on a CPU)"
  _sm_cur=$(current_provider)
  case $_sm_cur in
    openai) _sm_def=2 ;; anthropic) _sm_def=3 ;; gemini) _sm_def=4 ;; ollama) _sm_def=5 ;; *) _sm_def=1 ;;
  esac
  while :; do
    ask "  Choice [$_sm_def]: " || ANSWER=$_sm_def
    [ -z "$ANSWER" ] && ANSWER=$_sm_def
    case $ANSWER in
      1) _sm_p=azure ;; 2) _sm_p=openai ;; 3) _sm_p=anthropic ;; 4) _sm_p=gemini ;; 5) _sm_p=ollama ;;
      *) lab_say "    Enter a number from 1 to 5."; continue ;;
    esac
    break
  done
  # An existing "auto" that already resolves to the chosen provider stays "auto" (no silent change).
  if [ "$(src LAB_MODEL_PROVIDER)" = old ] && [ "$(val LAB_MODEL_PROVIDER)" = auto ] && [ "$_sm_p" = "$_sm_cur" ]; then
    :
  else
    setv LAB_MODEL_PROVIDER "$_sm_p" entered
  fi
  case $_sm_p in
    azure)
      lab_say "  Azure OpenAI needs the key, the endpoint and the deployment name."
      prompt_secret AZURE_OPENAI_API_KEY "Azure OpenAI API key"
      prompt_text AZURE_OPENAI_ENDPOINT "Endpoint (https://<resource>.openai.azure.com)" check_https_url
      prompt_text AZURE_OPENAI_DEPLOYMENT "Deployment name (as shown in Azure, e.g. gpt-5.1)" ;;
    openai) prompt_secret OPENAI_API_KEY "OpenAI API key" ;;
    anthropic) prompt_secret ANTHROPIC_API_KEY "Anthropic API key" ;;
    gemini) prompt_secret GEMINI_API_KEY "Google AI Studio (Gemini) API key" ;;
    ollama) lab_say "  lab-chat will use the local Ollama model (qwen3.5:4b by default). No key needed." ;;
  esac
  apply_ollama_memory
  lab_say ""
}

step_checkpoint() {
  lab_say "Step 3 of 6: Check Point products (each one optional)"
  lab_say "  Agents for a product you skip still start and say that the product is not configured."
  _sc_mgmt="MANAGEMENT_HOST S1C_URL MANAGEMENT_API_KEY MANAGEMENT_USERNAME MANAGEMENT_PASSWORD"
  # shellcheck disable=SC2086
  if group "the Management server (Management, Logs, Threat Prevention, HTTPS Inspection, Policy Insights, Gateway CLI)" $_sc_mgmt; then
    prompt_management
  fi
  # shellcheck disable=SC2086
  finish_group "The Management server" prompt_management mgmt_missing $_sc_mgmt
  if group "Gaia (a Security Gateway's Gaia REST API)" GAIA_GATEWAY_IP GAIA_USERNAME GAIA_PASSWORD; then
    prompt_gaia
  fi
  finish_group "Gaia" prompt_gaia all_or_none GAIA_GATEWAY_IP GAIA_USERNAME GAIA_PASSWORD
  lab_say "  Check Point portal keys (portal.checkpoint.com): Global Settings > API Keys > New."
  if group "the Documentation MCP server (Check Point portal)" DOC_CLIENT_ID DOC_SECRET_KEY; then
    prompt_documentation
  fi
  finish_group "The Documentation MCP server" prompt_documentation all_or_none DOC_CLIENT_ID DOC_SECRET_KEY
  if group "Threat Emulation" TE_API_KEY; then
    prompt_secret TE_API_KEY "Threat Emulation API key"
  fi
  if group "the Reputation Service" REPUTATION_API_KEY; then
    prompt_secret REPUTATION_API_KEY "Reputation Service API key"
  fi
  if group "Spark Management" SPARK_MGMT_CLIENT_ID SPARK_MGMT_SECRET_KEY; then
    prompt_spark
  fi
  finish_group "Spark Management" prompt_spark all_or_none SPARK_MGMT_CLIENT_ID SPARK_MGMT_SECRET_KEY
  if group "SASE" HARMONY_SASE_API_KEY HARMONY_SASE_MANAGEMENT_HOST HARMONY_SASE_ORIGIN; then
    prompt_sase
  fi
  finish_group "SASE" prompt_sase all_or_none HARMONY_SASE_API_KEY HARMONY_SASE_MANAGEMENT_HOST HARMONY_SASE_ORIGIN
  lab_say ""
}

step_integrations() {
  lab_say "Step 4 of 6: Optional integrations and lab host"
  if group "Lakera Guard (prompt and answer screening)" LAKERA_API_KEY; then
    prompt_secret LAKERA_API_KEY "Lakera Guard API key"
    prompt_text LAKERA_PROJECT_ID "Lakera project ID (optional)"
  fi
  if group "the SCIM identity provider token (Identity Provisioning Agent)" IDP_SCIM_TOKEN; then
    prompt_secret IDP_SCIM_TOKEN "SCIM bearer token"
  fi
  if group "DevHub (DevHub Operations Agent)" DEVHUB_MCP_TOKEN; then
    prompt_secret DEVHUB_MCP_TOKEN "DevHub MCP API key"
  fi
  if group "PolicyPilot (PolicyPilot agents)" PILOT_MCP_TOKEN; then
    prompt_secret PILOT_MCP_TOKEN "PolicyPilot MCP key"
  fi
  if group "the Build Your Own MCP exercise (IPS service key)" IPS_CLIENT_ID IPS_ACCESS_KEY; then
    prompt_ips
  fi
  finish_group "The IPS service key" prompt_ips all_or_none IPS_CLIENT_ID IPS_ACCESS_KEY
  lab_say "  Lab host: a shared server with its own domain behind a reverse proxy. Skip this on your own computer."
  _si_old_domain=$(val DOMAIN)
  prompt_text DOMAIN "Lab host domain, e.g. lab.example.com" check_domain
  _si_domain=$(val DOMAIN)
  if [ -n "$_si_domain" ] && [ "$_si_domain" != "$_si_old_domain" ]; then
    if [ "$(val N8N_HOST)" = localhost ] || [ -z "$(val N8N_HOST)" ]; then
      if yes_no "  Also set the n8n addresses to https://n8n.$_si_domain/?" y; then
        setv N8N_HOST "n8n.$_si_domain" entered
        setv WEBHOOK_URL "https://n8n.$_si_domain/" entered
        setv N8N_EDITOR_BASE_URL "https://n8n.$_si_domain/" entered
        if [ "$(val LANGFUSE_URL)" = "http://localhost:3100" ]; then setv LANGFUSE_URL "" entered; fi
      fi
    fi
  fi
  # A lab host runs the Complete lab (Langflow and Open WebUI are part of the course there).
  if [ -n "$(val DOMAIN)" ] && [ "$(lab_setup_kind "$(val COMPOSE_PROFILES)")" = standard ]; then
    lab_say "  Lab hosts use the Complete lab (COMPOSE_PROFILES=complete)."
    if yes_no "  Switch to the Complete lab?" y; then
      apply_setup complete
      show_components "  "
      requirement_advice complete "  "
      apply_ollama_memory
    fi
  fi
  lab_say ""
}

step_admin() {
  lab_say "Step 5 of 6: Lab admin and lab secrets"
  _sa_cur=$(val N8N_ADMIN_EMAIL)
  [ -n "$_sa_cur" ] || _sa_cur=admin@lab.local
  while :; do
    ask "  Lab admin email (n8n, Flowise, Langflow, Langfuse, Open WebUI) [$_sa_cur]: " || ANSWER=
    [ -z "$ANSWER" ] && ANSWER=$_sa_cur
    if lab_email_ok "$ANSWER"; then break; fi
    lab_say "    Enter an email address, for example admin@lab.local"
    [ "$input_eof" = 1 ] && { ANSWER=$_sa_cur; break; }
  done
  _sa_new=$ANSWER
  if [ "$_sa_new" != "$(val N8N_ADMIN_EMAIL)" ]; then
    if [ "$(src N8N_ADMIN_EMAIL)" = old ] && [ "$deployment" != no ]; then
      lab_say "    The lab has started before: n8n, Flowise and Langflow keep the admin they created first."
      yes_no "    Change N8N_ADMIN_EMAIL anyway?" n && setv N8N_ADMIN_EMAIL "$_sa_new" entered
    else
      setv N8N_ADMIN_EMAIL "$_sa_new" entered
    fi
  fi
  _sa_pw=$(val N8N_ADMIN_PASSWORD)
  if [ -n "$_sa_pw" ] && ! lab_is_placeholder "$_sa_pw" && lab_password_problem "$_sa_pw" >/dev/null; then
    lab_say "  Admin password: set (kept)."
  elif lab_is_op_ref "$_sa_pw"; then
    lab_say "  Admin password: 1Password reference (kept)."
  else
    lab_say "  Admin password: 8 to 64 characters with upper- and lowercase letters, a digit and one of - _ . ! @ %"
    while :; do
      ask_hidden "  Admin password (Enter generates a strong one): " || { ANSWER=; break; }
      [ -z "$ANSWER" ] && break
      if ! _sa_msg=$(lab_password_problem "$ANSWER"); then
        lab_say "    The password $_sa_msg."
        continue
      fi
      _sa_first=$ANSWER
      ask_hidden "  Type it again: " || { ANSWER=; break; }
      if [ "$ANSWER" != "$_sa_first" ]; then lab_say "    The two entries differ. Try again."; continue; fi
      if [ -n "$_sa_pw" ] && ! lab_is_placeholder "$_sa_pw" && [ "$deployment" != no ]; then
        lab_say "    The lab has started before: the apps keep the admin password they were created with."
      fi
      setv N8N_ADMIN_PASSWORD "$ANSWER" entered
      break
    done
  fi
}

# ---------------------------------------------------------------------------- collect values
if [ "$mode" = interactive ]; then
  [ "$op_mode" = 1 ] && step_1password
  step_setup
  step_model
  step_checkpoint
  step_integrations
  step_admin
else
  # Non-interactive: every setting can come from an environment variable of the same name. A value
  # already in .env wins (re-runs never change a value silently); the environment fills blanks.
  env_used=
  env_ignored=
  for k in $known_keys; do
    _ev_set=
    eval "_ev_set=\${$k+x}"
    [ "$_ev_set" = x ] || continue
    eval "_ev=\${$k}"
    [ -n "$_ev" ] || continue
    _cur=$(val "$k")
    if [ "$(src "$k")" = old ] && [ -n "$_cur" ] && ! lab_is_placeholder "$_cur"; then
      [ "$_cur" = "$_ev" ] || note_list env_ignored "$k"
      continue
    fi
    setv "$k" "$_ev" env && note_list env_used "$k"
  done
  [ -n "$env_used" ] && lab_say "Taken from the environment: $(trim "$env_used")"
  if [ -n "$env_ignored" ]; then
    lab_say "Kept the value already in .env (the environment differs): $(trim "$env_ignored")"
    lab_say "  To change one of these, edit .env or run ./setup.sh without options."
  fi
  rename_full_profile
  case $(val N8N_ADMIN_EMAIL) in '') setv N8N_ADMIN_EMAIL admin@lab.local generated ;; esac
  # A lab host domain from the environment also sets the n8n addresses still at their template defaults.
  _dom=$(val DOMAIN)
  if [ -n "$_dom" ]; then
    [ "$(src N8N_HOST)" = template ] && setv N8N_HOST "n8n.$_dom" env
    [ "$(src WEBHOOK_URL)" = template ] && setv WEBHOOK_URL "https://n8n.$_dom/" env
    [ "$(src N8N_EDITOR_BASE_URL)" = template ] && setv N8N_EDITOR_BASE_URL "https://n8n.$_dom/" env
  fi
  report_setup
  apply_ollama_memory
  _nm=$(mgmt_missing)
  [ -z "$_nm" ] || lab_warn "the Management server settings are only partly set: $_nm is missing (set all of them, or none)."
fi

# ---------------------------------------------------------------------------- lab secrets
if [ "$mode" = auto ]; then lab_say "Lab secrets"; else lab_say "  Lab secrets:"; fi
generated=
kept_unsafe=

bad_value() {
  # bad_value NAME VALUE: the value is set but unusable (placeholder or wrong format)
  if lab_is_placeholder "$2"; then return 0; fi
  case $1 in
    N8N_ADMIN_PASSWORD) lab_password_problem "$2" >/dev/null && return 1; return 0 ;;
    LANGFUSE_ENCRYPTION_KEY)
      case $2 in *[!0123456789abcdefABCDEF]*) return 0 ;; esac
      [ ${#2} -eq 64 ] && return 1; return 0 ;;
    LANGFUSE_PUBLIC_KEY) case $2 in pk-lf-?*) return 1 ;; esac; return 0 ;;
    LANGFUSE_SECRET_KEY) case $2 in sk-lf-?*) return 1 ;; esac; return 0 ;;
    LITELLM_MASTER_KEY) case $2 in sk-????????????*) return 1 ;; esac; return 0 ;;
  esac
  return 1
}

new_secret() {
  case $1 in
    POSTGRES_PASSWORD | MCP_GATEWAY_TOKEN) lab_rand_hex 24 ;;
    LANGFUSE_PUBLIC_KEY) printf 'pk-lf-%s' "$(lab_rand_hex 20)" ;;
    LANGFUSE_SECRET_KEY) printf 'sk-lf-%s' "$(lab_rand_hex 20)" ;;
    LITELLM_MASTER_KEY) printf 'sk-%s' "$(lab_rand_hex 24)" ;;
    N8N_ADMIN_PASSWORD) lab_rand_password ;;
    *) lab_rand_hex 32 ;;
  esac
}

# Secrets an existing lab already uses: a new value would lock the apps out of their own data.
bound="POSTGRES_PASSWORD N8N_ENCRYPTION_KEY N8N_ADMIN_PASSWORD NEXTAUTH_SECRET SALT LANGFUSE_ENCRYPTION_KEY LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY"
secrets="POSTGRES_PASSWORD N8N_ENCRYPTION_KEY N8N_USER_MANAGEMENT_JWT_SECRET N8N_ADMIN_PASSWORD LITELLM_MASTER_KEY MCP_GATEWAY_TOKEN NEXTAUTH_SECRET SALT LANGFUSE_ENCRYPTION_KEY LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY WEBUI_SECRET_KEY PILOT_SESSION_SECRET"
for k in $secrets; do
  _cur=$(val "$k")
  lab_is_op_ref "$_cur" && continue
  if [ -z "$_cur" ]; then
    if [ "$k" = N8N_ENCRYPTION_KEY ] && [ "$deployment" = yes ]; then
      lab_warn "N8N_ENCRYPTION_KEY is blank but this lab has started before. n8n keeps its own key in its"
      lab_say "  volume; copy it into .env instead of a new one: docker compose exec n8n cat /home/node/.n8n/config"
      note_list kept_unsafe "$k"
      continue
    fi
    setv "$k" "$(new_secret "$k")" generated && note_list generated "$k"
    continue
  fi
  bad_value "$k" "$_cur" || continue
  case " $bound " in
    *" $k "*)
      if [ "$deployment" != no ]; then
        if [ "$deployment" = yes ]; then _why="this lab has started before (Docker holds its volumes)"; else _why="Docker is not running, so setup cannot tell whether this lab has started before"; fi
        lab_say "$k holds a placeholder or an invalid value, and $_why."
        lab_say "  A new value would lock the apps out of the data they created with the old one."
        if [ "$mode" = interactive ] && yes_no "  Generate a new $k anyway?" n; then
          setv "$k" "$(new_secret "$k")" generated && note_list generated "$k"
        else
          note_list kept_unsafe "$k"
        fi
        continue
      fi ;;
  esac
  setv "$k" "$(new_secret "$k")" generated && note_list generated "$k"
done
# Langfuse sign-in address: a local lab reaches it on a local port; a lab host at https://trace.<DOMAIN>
# (the Compose default when LANGFUSE_URL is blank).
if [ -z "$(val LANGFUSE_URL)" ] && [ -z "$(val DOMAIN)" ]; then
  setv LANGFUSE_URL "http://localhost:3100" generated
elif [ -n "$(val DOMAIN)" ] && [ "$(val LANGFUSE_URL)" = "http://localhost:3100" ]; then
  setv LANGFUSE_URL "" generated
  lab_say "  LANGFUSE_URL cleared: with DOMAIN set, Langfuse is at https://trace.$(val DOMAIN)"
fi
lab_say "  Generated: $(list_or_none "$generated")"
if [ -n "$kept_unsafe" ]; then
  lab_warn "kept as they are (the checks below report them): $(trim "$kept_unsafe")"
fi
case " $generated " in *" N8N_ADMIN_PASSWORD "*)
  lab_say "  The lab admin password was generated. View it with: grep '^N8N_ADMIN_PASSWORD=' .env" ;;
esac

# 1Password: optionally store the generated secrets there too (only when the user confirms).
if [ "$op_mode" = 1 ] && [ -n "$generated" ]; then
  lab_say ""
  if yes_no "Store the generated lab secrets in 1Password too (new item $op_item-secrets)?" n; then
    if ! command -v op >/dev/null 2>&1; then
      lab_warn "the 1Password CLI (op) is not installed; the secrets stay in .env."
    elif ! op whoami >/dev/null 2>&1 </dev/null; then
      lab_warn "the 1Password CLI is not signed in; the secrets stay in .env. Sign in (op signin) and run ./setup.sh --1password again."
    else
      _tpl="$LAB_TMP/op-item.json"
      {
        printf '{"title":"%s-secrets","category":"SECURE_NOTE","fields":[' "$op_item"
        _sep=
        for k in $generated; do
          printf '%s{"id":"%s","label":"%s","type":"CONCEALED","value":"%s"}' "$_sep" "$k" "$k" "$(val "$k")"
          _sep=,
        done
        printf ']}\n'
      } > "$_tpl"
      if op item create --vault "$op_vault" - < "$_tpl" >/dev/null 2>&1; then
        for k in $generated; do setv "$k" "op://$op_vault/$op_item-secrets/$k" 1password; done
        lab_say "  Stored in 1Password item $op_item-secrets; .env now holds references for them."
      else
        lab_warn "1Password did not create the item; the secrets stay in .env."
      fi
      rm -f "$_tpl"
    fi
  fi
fi

# ---------------------------------------------------------------------------- write .env
values="$LAB_TMP/values.env"
: > "$values"
for k in $known_keys; do printf '%s=%s\n' "$k" "$(val "$k")" >> "$values"; done
for k in $extra_keys; do printf '%s=%s\n' "$k" "$(lab_get O_ "$k")" >> "$values"; done

tmp_env="$LAB_ROOT/.env.setup.$$.tmp"
awk -v sq="'" -v dq='"' '
  function fmt(v) {
    if (v ~ /^[A-Za-z0-9_.\/:@%+=,~!^*?&-]*$/) return v
    if (index(v, sq) == 0) return sq v sq
    if (index(v, dq) == 0 && index(v, "$") == 0 && index(v, "\\") == 0) return dq v dq
    return v
  }
  FNR == 1 { pass++ }
  pass == 1 { eq = index($0, "="); k = substr($0, 1, eq - 1); val[k] = substr($0, eq + 1); order[++n] = k; next }
  pass == 2 { if (match($0, /^[A-Za-z_][A-Za-z0-9_]*=/)) active[substr($0, 1, RLENGTH - 1)] = 1; next }
  {
    line = $0
    if (match(line, /^[A-Za-z_][A-Za-z0-9_]*=/)) {
      k = substr(line, 1, RLENGTH - 1)
      if (k in val) { print k "=" fmt(val[k]); done[k] = 1; next }
      print line; next
    }
    if (match(line, /^#[ \t]*[A-Z][A-Z0-9_]*=/)) {
      k = substr(line, 2); sub(/^[ \t]*/, "", k); k = substr(k, 1, index(k, "=") - 1)
      print line
      if ((k in val) && val[k] != "" && !(k in active) && !(k in done)) { print k "=" fmt(val[k]); done[k] = 1 }
      next
    }
    print line
  }
  END {
    hdr = 0
    for (i = 1; i <= n; i++) {
      k = order[i]
      if ((k in done) || (k in active) || val[k] == "") continue
      if (!hdr) { print ""; print "# ---- Kept from your previous .env (not in .env-example) ----"; hdr = 1 }
      print k "=" fmt(val[k]); done[k] = 1
    }
  }
' "$values" "$TEMPLATE" "$TEMPLATE" > "$tmp_env" || lab_die "cannot write the new .env" 2
rm -f "$values"
chmod 600 "$tmp_env"

lab_say ""
lab_say "Step 6 of 6: Write .env and check it"
if [ "$have_old" = 1 ] && cmp -s "$tmp_env" "$ENV_FILE"; then
  rm -f "$tmp_env"
  tmp_env=
  chmod 600 "$ENV_FILE" 2>/dev/null
  lab_say "  .env is up to date (no changes)."
else
  if [ "$have_old" = 1 ]; then
    cp "$ENV_FILE" "$LAB_ROOT/.env.bak" && chmod 600 "$LAB_ROOT/.env.bak"
    lab_say "  Previous .env saved as .env.bak (mode 600)."
  fi
  mv -f "$tmp_env" "$ENV_FILE" || lab_die "cannot replace $ENV_FILE" 2
  tmp_env=
  written=1
  chmod 600 "$ENV_FILE"
  lab_say "  Wrote .env (mode 600)."
fi
[ -n "$removed" ] && lab_say "  Removed settings nothing reads any more: $(trim "$removed")"
if [ "$flowise_sqlite_note" = 1 ]; then
  lab_say "  Flowise now keeps its data in the lab Postgres. Flows you saved in the old SQLite database"
  lab_say "  (flowise_data/database.sqlite) are not moved: the lab agents are seeded again, and your own"
  lab_say "  flows need an export from the old Flowise and an import into the new one."
fi

if [ "$op_mode" = 1 ] && [ -n "$op_fields" ]; then
  lab_say ""
  lab_say "  In 1Password, create item '$op_item' in vault '$op_vault' with these fields (field name = setting name):"
  lab_say "    $(trim "$op_fields")"
fi

# External Docker networks the compose file expects (for example the lab host's reverse-proxy network).
if [ "$docker_running" = 1 ]; then
  for net in $(lab_external_networks); do
    if ! docker network inspect "$net" >/dev/null 2>&1 </dev/null; then
      _create=1
      if [ "$mode" = interactive ]; then
        lab_say "  docker-compose.yml expects the Docker network '$net' (used by a lab host's reverse proxy)."
        yes_no "  Create it now (an empty local network)?" y || _create=0
      fi
      if [ "$_create" = 1 ]; then
        if docker network create "$net" >/dev/null 2>&1 </dev/null; then
          lab_say "  Created the Docker network $net."
        else
          lab_warn "could not create the Docker network $net. Create it with: docker network create $net"
        fi
      fi
    fi
  done
fi

lab_say ""
doctor_rc=0
sh "$LAB_ROOT/scripts/doctor.sh" --preflight --env-file "$ENV_FILE" </dev/null || doctor_rc=$?

uses_op=0
if grep -q '=op://' "$ENV_FILE" 2>/dev/null || grep -q "='op://" "$ENV_FILE" 2>/dev/null; then uses_op=1; fi
_admin=$(val N8N_ADMIN_EMAIL)
_kind=$(lab_setup_kind "$(val COMPOSE_PROFILES)")
if [ "$_kind" = complete ]; then
  _apps="n8n, Flowise, Langflow, Langfuse and Open WebUI"
else
  _apps="n8n, Flowise and Langfuse"
fi
lab_say ""
lab_say "Next steps ($(lab_kind_label "$_kind"))"
if [ "$uses_op" = 1 ]; then
  lab_say "  1. Start the lab:  op run --env-file=.env -- docker compose up -d"
else
  lab_say "  1. Start the lab:  docker compose up -d"
fi
if [ "$uses_op" = 1 ]; then
  lab_say "  2. After 5 to 10 minutes check it:  op run --env-file=.env -- ./scripts/doctor.sh --post-start"
else
  lab_say "  2. After 5 to 10 minutes check it:  ./scripts/doctor.sh --post-start"
fi
lab_say "  3. Sign in to $_apps as $_admin."
lab_say "     The password is N8N_ADMIN_PASSWORD in .env: grep '^N8N_ADMIN_PASSWORD=' .env"
lab_say "  To add or change a key later: ./setup.sh (or edit .env), then docker compose up -d"
if [ "$_kind" = standard ]; then
  lab_say "  To add Langflow and Open WebUI later (the Complete lab: Docker with 6 CPUs and 16 GB of memory),"
  lab_say "  run ./setup.sh and choose the Complete lab, or add complete to COMPOSE_PROFILES in .env, then"
  lab_say "  docker compose up -d"
fi
if [ "$doctor_rc" -ne 0 ]; then
  lab_say ""
  lab_say "The checks above found problems to fix before you start the lab."
  exit 1
fi
exit 0
