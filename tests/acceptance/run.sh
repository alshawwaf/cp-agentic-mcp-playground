#!/bin/sh
# tests/acceptance/run.sh: acceptance tests for the Check Point AI agent lab.
#
# Every promise the lab makes, as an executable check, run against a lab that is already up
# (docker compose up -d): the MCP gateway and the direct MCP path, the agents in n8n, Flowise
# and Langflow, lab-chat on LiteLLM, Langfuse traces, RAG, Open WebUI, the code-first agent and
# the opt-in labs. scripts/doctor.sh stays the quick health and settings check; this suite goes
# end to end.
#
# Needs only sh and docker on the host. The checks run in ONE throwaway python:3.12-alpine
# container (the digest docker-compose.yml pins, capped CPU and memory, removed afterwards) on
# the lab network. Secrets reach it through a mode-600 env file that is deleted as soon as the
# container is created. No secret value is printed. No lab service is started, stopped or
# changed; checks of profiles that are off are reported as SKIP.
#
# Exit status: 0 = no check failed, 1 = at least one check failed, 2 = the suite could not run.

set -u
umask 077

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd -P) || exit 2
REPO_ROOT=$(cd "$SCRIPT_DIR/../.." && pwd -P) || exit 2
LAB_ROOT=$REPO_ROOT
CALLER_DIR=$(pwd)
# shellcheck source=SCRIPTDIR/../../scripts/lib/labenv.sh
. "$REPO_ROOT/scripts/lib/labenv.sh"

CHECK_IDS="STACK GW-AUTH GW-TOOLS DIRECT LITELLM KEYS N8N-SEED N8N-RUN FLOWISE-SEED FLOWISE-RUN LANGFLOW-SEED LANGFLOW-RUN RAG CODE-AGENT LANGFUSE OPENWEBUI SECLAB AIG EXERCISES EVALS"
# Fallback when docker-compose.yml cannot be read: the image builders-import and rag-ingest use.
PY_IMAGE_PIN="python:3.12.14-alpine@sha256:4c47124a8391cb7a9f571164147d154777cf012a4ece5f86097130d7a4478111"
# Settings the checks use, passed by value (secrets included; never printed).
VALUE_NAMES="MCP_GATEWAY_TOKEN N8N_ADMIN_EMAIL N8N_ADMIN_PASSWORD LITELLM_MASTER_KEY LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY QDRANT_API_KEY OPEN_WEBUI_ADMIN_EMAIL OPEN_WEBUI_ADMIN_PASSWORD FLOWISE_API_KEY LANGFLOW_API_KEY FLOWISE_PORT DOMAIN N8N_HOST NIGHTLY_SELF_QA OPEN_WEBUI_DEFAULT_MODELS COMPOSE_PROFILES"
# Settings the checks only need to know are set (passed as LAB_SET_<NAME>=1, never by value).
PRESENCE_NAMES="OPENAI_API_KEY AZURE_OPENAI_API_KEY AZURE_OPENAI_ENDPOINT AZURE_OPENAI_DEPLOYMENT ANTHROPIC_API_KEY GEMINI_API_KEY SPARK_MGMT_CLIENT_ID SPARK_MGMT_SECRET_KEY HARMONY_SASE_API_KEY HARMONY_SASE_MANAGEMENT_HOST HARMONY_SASE_ORIGIN LAKERA_API_KEY IDP_SCIM_TOKEN DEVHUB_MCP_TOKEN PILOT_MCP_TOKEN IPS_CLIENT_ID IPS_ACCESS_KEY TE_API_KEY REPUTATION_API_KEY MANAGEMENT_HOST S1C_URL MANAGEMENT_API_KEY DOC_CLIENT_ID DOC_SECRET_KEY GAIA_GATEWAY_IP"
PROVIDER_KEYS="OPENAI_API_KEY AZURE_OPENAI_API_KEY ANTHROPIC_API_KEY GEMINI_API_KEY"
BUILDER_SERVICES="n8n n8n-import flowise langflow builders-import"

usage() {
  cat <<'EOF'
Usage: tests/acceptance/run.sh [options]

Runs the lab's acceptance tests against the running lab and prints a PASS / FAIL / SKIP table.

  --only IDS          Run only these checks (comma-separated), for example --only GW-AUTH,GW-TOOLS
  --skip IDS          Leave these checks out
  --profile-aware     Report checks of profiles that are off as SKIP, naming the profile (default)
  --no-profile-aware  Run those checks anyway (they fail: their services are not running)
  --with-model        Make the real model calls (agents answer a one-line prompt, tool calls).
                      Default: on when .env has a cloud model key or a local Ollama chat model
  --no-model          No model calls; checks that need one are SKIP
  --mock-provider     KEYS: start a mock provider and a throwaway LiteLLM (the image docker-compose.yml
                      pins) next to it, with no network, and prove the provider key from .env reaches
                      the provider while the LiteLLM master key does not (2 extra containers, removed)
  --with-scan         AIG: allow an AI-Infra-Guard scan (the scan test is owner-supervised; not automated)
  --json FILE         Also write the results as JSON (for CI)
  --env-file FILE     Settings file (default: .env in the lab directory)
  --project-dir DIR   Lab directory holding docker-compose.yml and .env (default: this repository)
  --project-name NAME Compose project name (default: as docker compose decides)
  --list              List the checks
  -h, --help

Checks: STACK GW-AUTH GW-TOOLS DIRECT LITELLM KEYS N8N-SEED N8N-RUN FLOWISE-SEED FLOWISE-RUN
        LANGFLOW-SEED LANGFLOW-RUN RAG CODE-AGENT LANGFUSE OPENWEBUI SECLAB AIG EXERCISES EVALS

Run it the way you start the lab. With 1Password references in .env:
  op run --env-file=.env -- tests/acceptance/run.sh
A Compose wrapper that adds -p / -f options: DOCKER_COMPOSE=/path/to/wrapper tests/acceptance/run.sh
Exit status: 0 = no check failed, 1 = a check failed, 2 = the suite could not run.
EOF
}

upper() { printf '%s' "$1" | tr 'abcdefghijklmnopqrstuvwxyz' 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'; }
lower() { printf '%s' "$1" | tr 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' 'abcdefghijklmnopqrstuvwxyz'; }
need_value() { [ "$1" -ge 2 ] || lab_die "$2 needs a value (see --help)" 2; }
say() { printf 'lab-acceptance: %s\n' "$*"; }

only=
skip=
profile_aware=1
model=auto
mock=0
with_scan=0
json_out=
env_file=
list=0
while [ $# -gt 0 ]; do
  case $1 in
    --only) need_value $# "$1"; only=$2; shift ;;
    --only=*) only=${1#*=} ;;
    --skip) need_value $# "$1"; skip=$2; shift ;;
    --skip=*) skip=${1#*=} ;;
    --profile-aware) profile_aware=1 ;;
    --no-profile-aware) profile_aware=0 ;;
    --with-model) model=on ;;
    --no-model) model=off ;;
    --mock-provider) mock=1 ;;
    --with-scan) with_scan=1 ;;
    --json) need_value $# "$1"; json_out=$2; shift ;;
    --json=*) json_out=${1#*=} ;;
    --env-file) need_value $# "$1"; env_file=$2; shift ;;
    --project-dir) need_value $# "$1"; LAB_ROOT=$(cd "$2" && pwd -P) || lab_die "no such directory: $2" 2; shift ;;
    --project-name | -p) need_value $# "$1"; COMPOSE_PROJECT_NAME=$2; export COMPOSE_PROJECT_NAME; shift ;;
    --list) list=1 ;;
    -h | --help) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

for _id in $(printf '%s,%s' "$only" "$skip" | tr ',' ' '); do
  case " $CHECK_IDS " in *" $(upper "$_id") "*) ;; *) lab_die "unknown check id: $_id (known: $CHECK_IDS)" 2 ;; esac
done
# selected ID: the check runs with these --only / --skip options.
selected() {
  if [ -n "$only" ]; then
    case ",$(upper "$only" | tr -d ' ')," in *",$1,"*) ;; *) return 1 ;; esac
  fi
  case ",$(upper "$skip" | tr -d ' ')," in *",$1,"*) return 1 ;; esac
  return 0
}
case $json_out in '' | /*) ;; *) json_out="$CALLER_DIR/$json_out" ;; esac
[ -n "$env_file" ] || env_file="$LAB_ROOT/.env"

lab_have_docker || lab_die "docker is not installed or not on PATH" 2
lab_docker_up || lab_die "Docker is not running (start Docker Desktop or the Docker service)" 2

# image SERVICE: the image docker-compose.yml (this repository) pins for SERVICE.
compose_image() {
  awk -v s="$1" '
    $0 ~ "^  " s ":[ \t]*$" { f = 1; next }
    f && /^  [A-Za-z0-9_.-]+:[ \t]*$/ { f = 0 }
    f && $1 == "image:" { print $2; exit }' "$REPO_ROOT/docker-compose.yml" 2>/dev/null
}
PY_IMAGE=${LAB_ACCEPTANCE_IMAGE:-$(compose_image builders-import)}
case $PY_IMAGE in python:*) ;; *) PY_IMAGE=$PY_IMAGE_PIN ;; esac
LITELLM_IMAGE=$(compose_image litellm)

if [ "$list" = 1 ]; then
  docker run --rm --network none --cpus 0.5 --memory 128m --read-only --cap-drop ALL \
    --security-opt no-new-privileges -e PYTHONDONTWRITEBYTECODE=1 -v "$REPO_ROOT:/lab:ro" \
    "$PY_IMAGE" python /lab/tests/acceptance/acceptance.py --list
  exit $?
fi

THROWAWAY=
LOGPID=
# Removes the throwaway containers (by their own names only) and the private temp directory.
# shellcheck disable=SC2329  # invoked from the traps below
cleanup() {
  for _c in $THROWAWAY; do docker rm -f "$_c" >/dev/null 2>&1 </dev/null || true; done
  THROWAWAY=
  if [ -n "$LOGPID" ]; then kill "$LOGPID" 2>/dev/null || true; LOGPID=; fi
  lab_tmp_cleanup
}
lab_tmp_init
trap 'cleanup' EXIT
trap 'cleanup; exit 130' INT TERM HUP
FACTS="$LAB_TMP/facts"
mkdir -p "$FACTS" "$LAB_TMP/out" || lab_die "cannot create the temporary directory" 2

# ---------------------------------------------------------------------------- settings
have_env=0
if lab_env_load "$env_file" F_; then have_env=1; fi
# eff NAME: the value Docker Compose uses (the shell environment wins over .env), as doctor.sh does.
eff() {
  _e_set=
  eval "_e_set=\${$1+x}"
  if [ "$_e_set" = x ]; then eval "printf '%s' \"\${$1}\""; else lab_get F_ "$1"; fi
}
is_real() {
  _ir=$(eff "$1")
  [ -n "$_ir" ] && ! lab_is_op_ref "$_ir" && ! lab_is_placeholder "$_ir"
}
all_real() { for _ar in "$@"; do is_real "$_ar" || return 1; done; return 0; }
nl='
'

# ---------------------------------------------------------------------------- the running lab
project=$(lab_project_name)
[ -n "$project" ] || lab_die "cannot work out the Compose project name (use --project-name)" 2
docker ps -a -q --filter "label=com.docker.compose.project=$project" 2>/dev/null </dev/null | grep -q . ||
  lab_die "no container of the Compose project '$project' exists. Start the lab first (docker compose up -d), or name it: --project-name NAME (or DOCKER_COMPOSE=<wrapper>)" 2

# Services of the active profiles (COMPOSE_PROFILES), as docker compose sees them.
if [ "$env_file" != "$LAB_ROOT/.env" ]; then
  lab_compose --env-file "$env_file" config --services > "$FACTS/expected.txt" 2>/dev/null </dev/null || : > "$FACTS/expected.txt"
else
  lab_compose config --services > "$FACTS/expected.txt" 2>/dev/null </dev/null || : > "$FACTS/expected.txt"
fi
if [ ! -s "$FACTS/expected.txt" ]; then
  say "warning: docker compose config --services failed (see docker compose config --quiet); STACK cannot list the expected services"
  profile_aware=0
fi
# The restart policy each service is configured with ("no" = one-shot job). Read without
# interpolation, so no .env value is involved.
lab_compose config --no-interpolate 2>/dev/null </dev/null | awk '
  /^services:/ { s = 1; next }
  s && /^[^ ]/ { s = 0 }
  s && /^  [A-Za-z0-9_.-]+:[ \t]*$/ { svc = $1; sub(/:$/, "", svc) }
  s && /^    restart:/ { p = $2; gsub(/["\047]/, "", p); print svc "|" p }' > "$FACTS/restart.txt" || true

# Containers of the project (not one-off "compose run" containers), newest first:
# service|status|health|exit code|restarts|restart policy|name|networks|OOM killed
docker ps -a -q --no-trunc --filter "label=com.docker.compose.project=$project" \
  --filter "label=com.docker.compose.oneoff=False" > "$LAB_TMP/ids.txt" 2>/dev/null </dev/null || true
: > "$FACTS/containers.txt"
if [ -s "$LAB_TMP/ids.txt" ]; then
  # shellcheck disable=SC2046
  docker inspect -f '{{index .Config.Labels "com.docker.compose.service"}}|{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}|{{.State.ExitCode}}|{{.RestartCount}}|{{.HostConfig.RestartPolicy.Name}}|{{.Name}}|{{range $k, $v := .NetworkSettings.Networks}}{{$k}},{{end}}|{{.State.OOMKilled}}' \
    $(cat "$LAB_TMP/ids.txt") > "$FACTS/containers.txt" 2>/dev/null </dev/null || true
fi
cname_of() { awk -F'|' -v s="$1" '$1 == s { n = $7; sub(/^\//, "", n); print n; exit }' "$FACTS/containers.txt"; }

# The lab network (doctor.sh's rule): a network of the gateway, else of any lab container,
# that is neither external (dokploy-network) nor the isolated security-lab network.
external=" $(lab_external_networks | tr '\n' ' ') "
net=
for _pass in gateway running any; do
  while IFS='|' read -r _svc _st _h _x _r _p _n _nets _o; do
    case $_pass in
      gateway) [ "$_svc" = mcp-gateway ] || continue ;;
      running) [ "$_st" = running ] || continue ;;
    esac
    for _net in $(printf '%s' "$_nets" | tr ',' ' '); do
      case $external in *" $_net "*) continue ;; esac
      case $_net in *security-lab|*ai-red-team) continue ;; esac
      net=$_net
      break
    done
    [ -n "$net" ] && break
  done < "$FACTS/containers.txt"
  [ -n "$net" ] && break
done
[ -n "$net" ] || lab_die "cannot find the lab network of project '$project'" 2
docker network inspect "$net" >/dev/null 2>&1 </dev/null || lab_die "the lab network $net does not exist (docker compose up -d creates it)" 2
seclab_net=$(awk -F'|' '$1 == "vuln-mcp" { n = split($8, a, ","); for (i = 1; i <= n; i++) if (a[i] ~ /security-lab$/) { print a[i]; exit } }' "$FACTS/containers.txt")
[ -n "$seclab_net" ] || seclab_net="${project}_security-lab"
docker network inspect "$seclab_net" >/dev/null 2>&1 </dev/null || seclab_net=
# AI-Infra-Guard runs only on its own internal network (no internet, no route to the host).
aig_net=$(awk -F'|' '$1 == "aig-webserver" { n = split($8, a, ","); for (i = 1; i <= n; i++) if (a[i] ~ /ai-red-team$/) { print a[i]; exit } }' "$FACTS/containers.txt")
[ -n "$aig_net" ] || aig_net="${project}_ai-red-team"
docker network inspect "$aig_net" >/dev/null 2>&1 </dev/null || aig_net=

# Builder containers: the NAMES of their non-empty environment variables, and the names whose
# value equals a provider key from .env. Values are compared inside awk and never printed.
: > "$FACTS/builder_env.txt"
pv="$LAB_TMP/provider-values"
: > "$pv"
for _v in $PROVIDER_KEYS; do
  if is_real "$_v"; then eff "$_v" >> "$pv"; printf '\n' >> "$pv"; fi
done
for _b in $BUILDER_SERVICES; do
  _cn=$(cname_of "$_b")
  [ -n "$_cn" ] || continue
  docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$_cn" > /dev/null 2>&1 </dev/null || continue
  _names=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$_cn" 2>/dev/null </dev/null |
    awk '{ i = index($0, "="); if (i > 1 && i < length($0)) print substr($0, 1, i - 1) }' | tr '\n' ',')
  _vals=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$_cn" 2>/dev/null </dev/null |
    awk -v vf="$pv" 'BEGIN { while ((getline l < vf) > 0) if (length(l) >= 8) v[l] = 1 }
      { i = index($0, "="); if (i > 1 && (substr($0, i + 1) in v)) print substr($0, 1, i - 1) }' | tr '\n' ',')
  printf '%s|%s|%s|%s\n' "$_b" "$_cn" "$_names" "$_vals" >> "$FACTS/builder_env.txt"
done
rm -f "$pv"

# Log facts (grep -q only: no log text leaves this script).
: > "$FACTS/logflags.txt"
logflag() {
  # logflag SERVICE PATTERN FLAG
  _lc=$(cname_of "$1")
  [ -n "$_lc" ] || return 0
  # Only the latest run: compose restarts (not recreates) one-shot jobs, so older runs stay in the log.
  _ls=$(docker inspect -f '{{.State.StartedAt}}' "$_lc" 2>/dev/null </dev/null)
  if docker logs --since "${_ls:-0}" --tail 200 "$_lc" 2>&1 </dev/null | grep -q -i -e "$2"; then
    printf '%s|%s\n' "$1" "$3" >> "$FACTS/logflags.txt"
  fi
}
logflag spark-management-mcp "is not configured" not-configured
logflag harmony-sase-mcp "is not configured" not-configured
logflag builders-import "Langflow is not running" langflow-skipped
logflag aig-agent "register_ack" connected
if [ -r "$LAB_ROOT/n8n/shared/evals_report.json" ]; then
  cp "$LAB_ROOT/n8n/shared/evals_report.json" "$FACTS/evals_report.json" 2>/dev/null || true
fi

# ---------------------------------------------------------------------------- model calls
if [ "$model" = auto ]; then
  if all_real AZURE_OPENAI_API_KEY AZURE_OPENAI_ENDPOINT AZURE_OPENAI_DEPLOYMENT || is_real OPENAI_API_KEY ||
     is_real ANTHROPIC_API_KEY || is_real GEMINI_API_KEY; then
    model=on; model_reason="a cloud model key is configured"
  elif [ "$(lower "$(eff LAB_MODEL_PROVIDER)")" = ollama ] || is_real OLLAMA_CHAT_MODEL; then
    model=on; model_reason="a local Ollama chat model is configured"
  else
    model=off; model_reason="no cloud model key or Ollama chat model is configured (--with-model forces them)"
  fi
elif [ "$model" = off ]; then
  model_reason="--no-model"
else
  model_reason="--with-model"
fi

# ---------------------------------------------------------------------------- provider mock (KEYS)
mock_result() {
  # mock_result STATUS DETAIL [HINT]: the provider mock result when the mock itself did not report.
  printf '{"status": "%s", "detail": "%s", "hint": "%s"}\n' "$1" "$2" "${3:-}" > "$FACTS/provider-mock.json"
}
provider_mock() {
  if [ -z "$LITELLM_IMAGE" ]; then mock_result skip "the litellm image is not in docker-compose.yml"; return; fi
  if ! docker image inspect "$LITELLM_IMAGE" >/dev/null 2>&1 </dev/null; then
    mock_result skip "image $LITELLM_IMAGE is not pulled" "docker compose pull litellm"
    return
  fi
  # The provider LiteLLM would use (render_config.py: azure, openai, anthropic, gemini, then ollama).
  _prov=$(lower "$(eff LAB_MODEL_PROVIDER)")
  if [ -z "$_prov" ] || [ "$_prov" = auto ]; then
    if all_real AZURE_OPENAI_API_KEY AZURE_OPENAI_ENDPOINT AZURE_OPENAI_DEPLOYMENT; then _prov=azure
    elif is_real OPENAI_API_KEY; then _prov=openai
    elif is_real ANTHROPIC_API_KEY; then _prov=anthropic
    elif is_real GEMINI_API_KEY; then _prov=gemini
    else _prov=none; fi
  fi
  case $_prov in
    azure) _kv=AZURE_OPENAI_API_KEY ;;
    openai) _kv=OPENAI_API_KEY ;;
    anthropic) _kv=ANTHROPIC_API_KEY ;;
    gemini) _kv=GEMINI_API_KEY ;;
    ollama) _kv= ;;
    *) _prov=openai; _kv= ;;
  esac
  _src="env"
  if [ "$_prov" = ollama ]; then _key=
  elif [ -n "$_kv" ] && is_real "$_kv"; then _key=$(eff "$_kv")
  else _key="sk-acceptance-$(lab_rand_hex 24)"; _src=generated; fi
  if is_real LITELLM_MASTER_KEY; then _mk=$(eff LITELLM_MASTER_KEY); else _mk="sk-acceptance-$(lab_rand_hex 24)"; fi
  case "$_key$_mk" in *"$nl"*) mock_result fail "a key in .env spans several lines"; return ;; esac

  _le="$LAB_TMP/litellm.env"
  {
    printf 'LITELLM_MASTER_KEY=%s\nLAB_MODEL_PROVIDER=%s\n' "$_mk" "$_prov"
    for _v in AZURE_OPENAI_API_KEY AZURE_OPENAI_ENDPOINT AZURE_OPENAI_DEPLOYMENT AZURE_OPENAI_API_VERSION \
              OPENAI_API_KEY OPENAI_MODEL OPENAI_BASE_URL ANTHROPIC_API_KEY ANTHROPIC_MODEL GEMINI_API_KEY \
              GEMINI_MODEL OLLAMA_CHAT_MODEL OLLAMA_API_BASE LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY LANGFUSE_HOST; do
      printf '%s=\n' "$_v"
    done
    case $_prov in
      azure)
        _dep=$(eff AZURE_OPENAI_DEPLOYMENT)
        printf 'AZURE_OPENAI_API_KEY=%s\nAZURE_OPENAI_ENDPOINT=http://127.0.0.1:8099\nAZURE_OPENAI_DEPLOYMENT=%s\nAZURE_OPENAI_API_VERSION=%s\n' \
          "$_key" "${_dep:-lab-chat}" "$(eff AZURE_OPENAI_API_VERSION)" ;;
      openai)
        printf 'OPENAI_API_KEY=%s\nOPENAI_BASE_URL=http://127.0.0.1:8099/v1\nOPENAI_MODEL=%s\n' "$_key" "$(eff OPENAI_MODEL)" ;;
      anthropic)
        printf 'ANTHROPIC_API_KEY=%s\nANTHROPIC_API_BASE=http://127.0.0.1:8099\nANTHROPIC_MODEL=%s\n' "$_key" "$(eff ANTHROPIC_MODEL)" ;;
      gemini)
        printf 'GEMINI_API_KEY=%s\nGEMINI_API_BASE=http://127.0.0.1:8099/v1beta\nGEMINI_MODEL=%s\n' "$_key" "$(eff GEMINI_MODEL)" ;;
      ollama)
        printf 'OLLAMA_API_BASE=http://127.0.0.1:8099\nOLLAMA_CHAT_MODEL=%s\n' "$(eff OLLAMA_CHAT_MODEL)" ;;
    esac
    printf 'LITELLM_LOCAL_MODEL_COST_MAP=True\nDISABLE_ADMIN_UI=True\n'
  } > "$_le"
  _ke="$LAB_TMP/keys.env"
  printf 'LAB_MOCK_PROVIDER=%s\nLAB_MOCK_PROVIDER_KEY=%s\nLAB_MOCK_MASTER_KEY=%s\nLAB_MOCK_ORIGIN=%s\nPYTHONDONTWRITEBYTECODE=1\n' \
    "$_prov" "$_key" "$_mk" "$_src" > "$_ke"
  _key=; _mk=

  _mock_c="lab-acceptance-mock-$$"
  _llm_c="lab-acceptance-litellm-$$"
  say "provider mock: mock provider + throwaway LiteLLM ($_prov), no network, 1.5 CPUs and 1.25 GB in total"
  THROWAWAY="$THROWAWAY $_mock_c"
  if ! docker run -d --rm --name "$_mock_c" --network none --cpus 0.5 --memory 256m --pids-limit 64 \
      --read-only --tmpfs /tmp:rw,size=16m --cap-drop ALL --security-opt no-new-privileges --user 65534:65534 \
      -e PYTHONDONTWRITEBYTECODE=1 -v "$REPO_ROOT:/lab:ro" \
      "$PY_IMAGE" python /lab/tests/acceptance/mock_provider.py >/dev/null 2>"$LAB_TMP/mock.err" </dev/null; then
    rm -f "$_le" "$_ke"
    mock_result fail "the mock provider container did not start"
    return
  fi
  THROWAWAY="$THROWAWAY $_llm_c"
  if ! docker run -d --rm --name "$_llm_c" --network "container:$_mock_c" --cpus 1 --memory 1g --pids-limit 256 \
      --cap-drop ALL --security-opt no-new-privileges --tmpfs /tmp/lab-litellm:rw,size=1m,mode=0700 \
      -v "$REPO_ROOT/integrations/litellm:/lab/litellm:ro" --env-file "$_le" \
      --entrypoint python "$LITELLM_IMAGE" /lab/litellm/render_config.py --exec --port 4000 \
      >/dev/null 2>"$LAB_TMP/litellm.err" </dev/null; then
    rm -f "$_le" "$_ke"
    mock_result fail "the throwaway LiteLLM container did not start"
    docker rm -f "$_mock_c" >/dev/null 2>&1 </dev/null
    return
  fi
  rm -f "$_le"
  docker logs -f "$_llm_c" > "$LAB_TMP/litellm.log" 2>&1 </dev/null &
  LOGPID=$!
  _ready=0
  _i=0
  while [ "$_i" -lt 90 ]; do
    _i=$((_i + 1))
    [ "$(docker inspect -f '{{.State.Running}}' "$_llm_c" 2>/dev/null </dev/null)" = true ] || break
    if docker exec "$_mock_c" python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:4000/health/liveliness', timeout=3)" >/dev/null 2>&1 </dev/null; then
      _ready=1
      break
    fi
    sleep 2
  done
  if [ "$_ready" = 1 ]; then
    docker exec --env-file "$_ke" "$_mock_c" python /lab/tests/acceptance/acceptance.py --phase provider-mock \
      > "$FACTS/provider-mock.json" 2>/dev/null </dev/null || true
    [ -s "$FACTS/provider-mock.json" ] || mock_result fail "the provider mock check did not report"
  elif [ "$_i" -ge 90 ]; then
    mock_result fail "the throwaway LiteLLM did not answer within 180 s" "docker compose run --rm --no-deps litellm --check"
  else
    mock_result fail "the throwaway LiteLLM stopped while starting (LiteLLM refused the settings)" \
      "docker compose run --rm --no-deps litellm --check"
  fi
  rm -f "$_ke"
  docker stop -t 5 "$_llm_c" >/dev/null 2>&1 </dev/null || true
  docker stop -t 2 "$_mock_c" >/dev/null 2>&1 </dev/null || true
  if [ -n "$LOGPID" ]; then kill "$LOGPID" 2>/dev/null || true; wait "$LOGPID" 2>/dev/null; LOGPID=; fi
  # Only render_config.py's own lines ([lab-litellm], never a secret value) are kept.
  grep '\[lab-litellm\]' "$LAB_TMP/litellm.log" 2>/dev/null | head -n 8 > "$FACTS/provider-mock.log"
  rm -f "$LAB_TMP/litellm.log"
  THROWAWAY=
}
if [ "$mock" = 1 ] && selected KEYS; then provider_mock; fi

# ---------------------------------------------------------------------------- the checks
main_env="$LAB_TMP/acceptance.env"
: > "$main_env"
put() { printf '%s=%s\n' "$1" "$2" >> "$main_env"; }
oprefs=
for _v in $VALUE_NAMES; do
  _val=$(eff "$_v")
  [ -n "$_val" ] || continue
  if lab_is_op_ref "$_val"; then put "LAB_OPREF_$_v" 1; oprefs="$oprefs $_v"; continue; fi
  case $_val in *"$nl"*) continue ;; esac
  put "$_v" "$_val"
done
for _v in $PRESENCE_NAMES; do
  [ -n "$(eff "$_v")" ] && put "LAB_SET_$_v" 1
done
_val=
put LAB_PROJECT "$project"
put LAB_NETWORK "$net"
put LAB_SECLAB_NETWORK "$seclab_net"
put LAB_MODEL_CALLS "$model"  # not LAB_MODEL: agent_loop.py reads that as the model name
put LAB_MODEL_REASON "$model_reason"
put LAB_PROFILE_AWARE "$profile_aware"
put LAB_ONLY "$only"
put LAB_SKIP "$skip"
put LAB_WITH_SCAN "$with_scan"
[ -n "$json_out" ] && put LAB_JSON 1
# Optional tuning: LAB_DETAIL_WIDTH (table width), LAB_MODEL_TIMEOUT (s per model call), LAB_TRACE_WAIT (s).
for _v in LAB_DETAIL_WIDTH LAB_MODEL_TIMEOUT LAB_TRACE_WAIT; do
  eval "_val=\${$_v:-}"
  case $_val in '' | *[!0-9]*) ;; *) put "$_v" "$_val" ;; esac
done
[ -n "$oprefs" ] && say "warning: unresolved 1Password references:$oprefs. Run: op run --env-file=.env -- tests/acceptance/run.sh"
[ "$have_env" = 1 ] || say "warning: $env_file not found: the checks use the shell environment only"

main_c="lab-acceptance-$$"
THROWAWAY="$main_c"
if ! docker create --rm --name "$main_c" --network "$net" --cpus 1 --memory 384m --pids-limit 128 \
    --read-only --tmpfs /tmp:rw,size=32m --cap-drop ALL --security-opt no-new-privileges \
    --user "$(id -u):$(id -g)" -e HOME=/tmp -e PYTHONDONTWRITEBYTECODE=1 --env-file "$main_env" \
    -v "$REPO_ROOT:/lab:ro" -v "$FACTS:/facts:ro" -v "$LAB_TMP/out:/out" \
    "$PY_IMAGE" python /lab/tests/acceptance/acceptance.py >/dev/null 2>"$LAB_TMP/create.err" </dev/null; then
  rm -f "$main_env"
  sed 's/^/  /' "$LAB_TMP/create.err" | head -n 5 >&2
  lab_die "could not create the test container (image $PY_IMAGE, network $net)" 2
fi
rm -f "$main_env"
if [ -n "$seclab_net" ] && selected SECLAB; then
  if grep -qx 'vuln-mcp' "$FACTS/expected.txt" 2>/dev/null || [ "$profile_aware" = 0 ]; then
    docker network connect "$seclab_net" "$main_c" >/dev/null 2>&1 </dev/null ||
      say "warning: could not join the test container to $seclab_net (SECLAB fails)"
  fi
fi
if [ -n "$aig_net" ] && selected AIG; then
  if grep -qx 'aig-webserver' "$FACTS/expected.txt" 2>/dev/null || [ "$profile_aware" = 0 ]; then
    docker network connect "$aig_net" "$main_c" >/dev/null 2>&1 </dev/null ||
      say "warning: could not join the test container to $aig_net (AIG fails)"
  fi
fi
docker start -a "$main_c" </dev/null
rc=$?
THROWAWAY=
case $rc in
  0 | 1) ;;
  *) say "the test container ended with exit $rc (the suite could not finish)"; rc=2 ;;
esac
if [ -n "$json_out" ]; then
  if [ -s "$LAB_TMP/out/result.json" ] && cp "$LAB_TMP/out/result.json" "$json_out"; then
    chmod 644 "$json_out" 2>/dev/null || true
    say "JSON result: $json_out"
  else
    say "warning: no JSON result was written to $json_out"
  fi
fi
exit "$rc"
