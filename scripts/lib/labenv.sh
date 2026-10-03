# shellcheck shell=sh
# Shared helpers for setup.sh and scripts/*.sh. POSIX sh (dash, busybox ash, bash 3.2 as /bin/sh).
# Sourced, never executed. Callers set LAB_ROOT (the directory that holds docker-compose.yml and
# .env) before sourcing. Nothing here prints a secret value.

LC_ALL=C
export LC_ALL

# ---------------------------------------------------------------------------- output
lab_say() { printf '%s\n' "$*"; }
lab_warn() { printf 'Warning: %s\n' "$*"; }
lab_die() {
  # lab_die MESSAGE [EXIT_CODE]
  printf 'Error: %s\n' "$1" >&2
  exit "${2:-1}"
}

# ---------------------------------------------------------------------------- temp dir
# lab_tmp_init: private temp directory (mode 700) in $LAB_TMP, removed by lab_tmp_cleanup.
lab_tmp_init() {
  LAB_TMP=$(mktemp -d "${TMPDIR:-/tmp}/lab.XXXXXX") || lab_die "cannot create a temporary directory" 2
  chmod 700 "$LAB_TMP"
}
lab_tmp_cleanup() {
  if [ -n "${LAB_TMP:-}" ] && [ -d "$LAB_TMP" ]; then
    rm -rf "$LAB_TMP"
  fi
  LAB_TMP=
}

# ---------------------------------------------------------------------------- .env parsing
# lab_env_parse FILE: print NAME=value per setting (Docker Compose rules: last one wins, surrounding
# quotes removed, " #" comments after unquoted values removed, "export " prefix allowed).
lab_env_parse() {
  awk -v sq="'" '
    { line = $0; sub(/\r$/, "", line) }
    line ~ /^[ \t]*#/ || line ~ /^[ \t]*$/ { next }
    {
      sub(/^[ \t]*export[ \t]+/, "", line)
      eq = index(line, "=")
      if (eq == 0) next
      k = substr(line, 1, eq - 1)
      v = substr(line, eq + 1)
      gsub(/^[ \t]+/, "", k); gsub(/[ \t]+$/, "", k)
      if (k !~ /^[A-Za-z_][A-Za-z0-9_]*$/) next
      sub(/^[ \t]+/, "", v)
      q = substr(v, 1, 1)
      if (q == "\"" || q == sq) {
        rest = substr(v, 2)
        e = index(rest, q)
        v = (e > 0) ? substr(rest, 1, e - 1) : rest
      } else {
        p = index(v, " #"); if (p > 0) v = substr(v, 1, p - 1)
        p = index(v, "\t#"); if (p > 0) v = substr(v, 1, p - 1)
        sub(/[ \t]+$/, "", v)
      }
      print k "=" v
    }' "$1"
}

# lab_env_load FILE PREFIX: for every setting NAME in FILE set the shell variables
# <PREFIX>NAME=value and <PREFIX>SET_NAME=1, and append NAME to <PREFIX>KEYS (file order).
# Needs LAB_TMP (the parsed copy lives there only while loading).
lab_env_load() {
  _el_file=$1
  _el_prefix=$2
  eval "${_el_prefix}KEYS="
  [ -f "$_el_file" ] || return 1
  lab_env_parse "$_el_file" > "$LAB_TMP/parsed.env" || return 1
  while IFS= read -r _el_line || [ -n "$_el_line" ]; do
    _el_key=${_el_line%%=*}
    _el_val=${_el_line#*=}
    case $_el_key in '' | [!A-Za-z_]* | *[!A-Za-z0-9_]*) continue ;; esac
    eval "_el_seen=\${${_el_prefix}SET_${_el_key}:-}"
    if [ -z "$_el_seen" ]; then
      eval "${_el_prefix}KEYS=\"\${${_el_prefix}KEYS} \$_el_key\""
    fi
    eval "${_el_prefix}${_el_key}=\$_el_val"
    eval "${_el_prefix}SET_${_el_key}=1"
  done < "$LAB_TMP/parsed.env"
  rm -f "$LAB_TMP/parsed.env"
  return 0
}

# lab_get PREFIX NAME: print the value of <PREFIX>NAME (empty when unset).
lab_get() {
  eval "printf '%s' \"\${$1$2:-}\""
}

# ---------------------------------------------------------------------------- value checks
lab_is_op_ref() {
  case $1 in op://*) return 0 ;; esac
  return 1
}

# Blank or a template placeholder (change_me, <your key>, ...): treated as "not set".
lab_is_placeholder() {
  _ip=$(printf '%s' "$1" | tr 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' 'abcdefghijklmnopqrstuvwxyz')
  case $_ip in
    '' | change_me* | changeme* | change-me* | none | null | unset | '<'* | your-key-here | your_key_here | xxx | todo) return 0 ;;
  esac
  return 1
}

# Values that were published in this repository and must never protect a lab.
lab_is_public_default() {
  case $1 in cp-mcp-gateway-training-token | sk-cp-litellm-training-key) return 0 ;; esac
  return 1
}

# lab_password_problem PASSWORD: print what is wrong and return 1, or return 0 when it passes the
# n8n + Flowise admin password rule (8-64 characters; upper, lower, digit, one of - _ . ! @ %).
lab_password_problem() {
  _pw=$1
  if [ ${#_pw} -lt 8 ] || [ ${#_pw} -gt 64 ]; then
    printf '%s' "must be 8 to 64 characters long"; return 1
  fi
  case $_pw in *[!A-Za-z0-9._@%!-]*)
    printf '%s' "may contain only letters, digits and - _ . ! @ %"; return 1 ;;
  esac
  case $_pw in *[ABCDEFGHIJKLMNOPQRSTUVWXYZ]*) ;; *) printf '%s' "needs an uppercase letter"; return 1 ;; esac
  case $_pw in *[abcdefghijklmnopqrstuvwxyz]*) ;; *) printf '%s' "needs a lowercase letter"; return 1 ;; esac
  case $_pw in *[0123456789]*) ;; *) printf '%s' "needs a digit"; return 1 ;; esac
  case $_pw in *[-_.!@%]*) ;; *) printf '%s' "needs one of - _ . ! @ %"; return 1 ;; esac
  return 0
}

lab_email_ok() {
  case $1 in
    *[!A-Za-z0-9._%+@-]* | *@*@* | @* | *@ | *@.* | *.) return 1 ;;
    ?*@?*.?*) return 0 ;;
  esac
  return 1
}

# ---------------------------------------------------------------------------- random values
# lab_rand_hex BYTES: 2*BYTES lowercase hex characters from /dev/urandom.
lab_rand_hex() {
  _rh=$(od -An -tx1 -N"$1" /dev/urandom 2>/dev/null | tr -d ' \n\t')
  [ ${#_rh} -eq $(($1 * 2)) ] || lab_die "cannot read random bytes (od and /dev/urandom are required)" 2
  printf '%s' "$_rh"
}

# lab_rand_password: 23 characters (four groups of letters and digits joined by "-"),
# always matching lab_password_problem.
lab_rand_password() {
  _rp_tries=0
  while :; do
    _rp_tries=$((_rp_tries + 1))
    [ "$_rp_tries" -le 20 ] || lab_die "cannot generate an admin password" 2
    _rp=$(od -An -tu1 -N20 /dev/urandom 2>/dev/null | awk '
      BEGIN { a = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"; n = length(a); s = "" }
      { for (i = 1; i <= NF; i++) { s = s substr(a, ($i % n) + 1, 1); if (length(s) == 5 || length(s) == 11 || length(s) == 17) s = s "-" } }
      END { print s }')
    if lab_password_problem "$_rp" >/dev/null; then
      printf '%s' "$_rp"
      return 0
    fi
  done
}

# ---------------------------------------------------------------------------- docker / compose
# lab_compose ARGS...: docker compose for this lab. DOCKER_COMPOSE may name another executable
# (for example a wrapper that adds -p / -f); it is run from LAB_ROOT either way.
lab_compose() {
  if [ -n "${DOCKER_COMPOSE:-}" ]; then
    (cd "$LAB_ROOT" && "$DOCKER_COMPOSE" "$@")
  else
    (cd "$LAB_ROOT" && docker compose "$@")
  fi
}

lab_have_docker() { command -v docker >/dev/null 2>&1; }
lab_docker_up() { docker info >/dev/null 2>&1 </dev/null; }

# lab_project_name: the Compose project name (same rules as docker compose: COMPOSE_PROJECT_NAME,
# the top-level name: in the compose file, else the directory name).
lab_project_name() {
  if [ -n "${COMPOSE_PROJECT_NAME:-}" ]; then
    printf '%s' "$COMPOSE_PROJECT_NAME"
    return 0
  fi
  _pn=$(lab_compose config --no-interpolate 2>/dev/null </dev/null | sed -n 's/^name: *//p' | head -n 1)
  if [ -z "$_pn" ]; then
    _pn=$(basename "$LAB_ROOT" | tr 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' 'abcdefghijklmnopqrstuvwxyz' | tr -cd 'a-z0-9_-')
  fi
  printf '%s' "$_pn"
}

# lab_deployment_state: "yes" when Docker holds the data volumes of this project that the lab's
# secrets protect (n8n_storage, postgres_storage: the lab has started before), "no" when it holds
# neither (other volumes, such as the downloaded Ollama models, do not count), "unknown" when
# Docker cannot be asked.
lab_deployment_state() {
  if ! lab_have_docker || ! lab_docker_up; then
    printf '%s' unknown
    return 0
  fi
  _ds_project=$(lab_project_name)
  if [ -z "$_ds_project" ]; then
    printf '%s' unknown
    return 0
  fi
  _ds_vol=
  for _ds_name in n8n_storage postgres_storage; do
    _ds_vol=$(docker volume ls -q --filter "label=com.docker.compose.project=$_ds_project" \
      --filter "label=com.docker.compose.volume=$_ds_name" 2>/dev/null </dev/null | head -n 1)
    [ -n "$_ds_vol" ] && break
  done
  if [ -n "$_ds_vol" ]; then printf '%s' yes; else printf '%s' no; fi
}

# ---------------------------------------------------------------------------- resources and lab components
# Lab components: the Standard lab (no profile) and the Complete lab (COMPOSE_PROFILES=complete).
# Requirements are what Docker can use. Thresholds in MiB: a machine sold as 8 GB (or Docker
# Desktop set to 8 GB) reports a little less, and Docker Desktop set to 16 GB reports 15.1 GB.
# shellcheck disable=SC2034
LAB_STANDARD_CPUS=4         # Standard lab: 4 CPUs
# shellcheck disable=SC2034
LAB_STANDARD_MIB=7168       # Standard lab: "8 GB"
# shellcheck disable=SC2034
LAB_COMPLETE_CPUS=6         # Complete lab: 6 CPUs
# shellcheck disable=SC2034
LAB_COMPLETE_MIB=15360      # Complete lab, or lab-chat on a local model: "16 GB"
# shellcheck disable=SC2034
LAB_OLLAMA_CHAT_MIB=5120    # OLLAMA_MEM_LIMIT a local chat model needs (setup writes 6g)

# lab_mem_mib VALUE: a Compose memory size (6g, 6144m, 1.5g, 512mb, plain bytes) in MiB; empty when
# it is not a size.
lab_mem_mib() {
  printf '%s' "$1" | awk '
    NR == 1 {
      v = tolower($0); gsub(/[ \t]/, "", v)
      if (v !~ /^[0-9]+(\.[0-9]+)?[bkmg]?b?$/) exit
      n = v; sub(/[bkmg]?b?$/, "", n)
      u = substr(v, length(n) + 1); sub(/b$/, "", u)
      if (u == "k") m = n / 1024; else if (u == "m") m = n + 0; else if (u == "g") m = n * 1024; else m = n / 1048576
      printf "%d", m
    }'
}

# lab_gb MIB: whole GB as people set it. A VM or kernel reports a little less than its setting
# (Docker Desktop at 16 GB reports about 15.1), so a value within 6% below a whole GB rounds up.
lab_gb() {
  awk -v m="$1" 'BEGIN { g = m / 1024; c = int(g); if (c < g) c++; if (c - g <= 0.06 * c) printf "%d", c; else printf "%.0f", g }'
}
# lab_gb1 MIB: GB with one decimal, as Docker reports it (15.1); a whole number without ".0".
lab_gb1() {
  awk -v m="$1" 'BEGIN { s = sprintf("%.1f", m / 1024); sub(/\.0$/, "", s); printf "%s", s }'
}
# lab_cpus N: "1 CPU" or "N CPUs".
lab_cpus() {
  if [ "$1" = 1 ]; then printf '%s' "1 CPU"; else printf '%s CPUs' "$1"; fi
}

# lab_host_mem_mib: memory of this computer in MiB (Linux /proc/meminfo, macOS sysctl hw.memsize).
lab_host_mem_mib() {
  _hm=
  if [ -r /proc/meminfo ]; then
    _hm=$(awk '$1 == "MemTotal:" { printf "%d", $2 / 1024; exit }' /proc/meminfo 2>/dev/null)
  else
    _hm_sc=$(command -v sysctl 2>/dev/null) || _hm_sc=/usr/sbin/sysctl
    [ -x "$_hm_sc" ] && _hm=$("$_hm_sc" -n hw.memsize 2>/dev/null | awk '/^[0-9]+$/ { printf "%d", $1 / 1048576 }')
  fi
  case $_hm in '' | *[!0-9]*) _hm= ;; esac
  printf '%s' "$_hm"
}

# lab_host_cpus: CPUs of this computer (macOS sysctl hw.ncpu; Linux nproc, getconf or /proc/cpuinfo).
lab_host_cpus() {
  _hc=
  if [ -r /proc/cpuinfo ] || [ "$(uname -s 2>/dev/null)" = Linux ]; then
    _hc=$(nproc 2>/dev/null) || _hc=
    case $_hc in '' | *[!0-9]*) _hc=$(getconf _NPROCESSORS_ONLN 2>/dev/null) || _hc= ;; esac
    case $_hc in '' | *[!0-9]*) _hc=$(grep -c '^processor' /proc/cpuinfo 2>/dev/null) || _hc= ;; esac
  else
    _hc_sc=$(command -v sysctl 2>/dev/null) || _hc_sc=/usr/sbin/sysctl
    [ -x "$_hc_sc" ] && _hc=$("$_hc_sc" -n hw.ncpu 2>/dev/null)
  fi
  case $_hc in '' | 0 | *[!0-9]*) _hc= ;; esac
  printf '%s' "$_hc"
}

# lab_docker_resources: "<CPUs> <MiB> <1 if Docker Desktop, else 0>" for what Docker can use
# (Docker Desktop: its Resources settings); nothing when Docker cannot be asked.
lab_docker_resources() {
  docker info --format '{{.NCPU}} {{.MemTotal}} {{.OperatingSystem}}' 2>/dev/null </dev/null | awk '
    NR == 1 && $1 ~ /^[0-9]+$/ && $1 > 0 && $2 ~ /^[0-9]+$/ { printf "%d %d %d", $1, $2 / 1048576, (index($0, "Docker Desktop") > 0) ? 1 : 0 }'
}

# lab_meets KIND CPUS MIB: CPUS and MIB meet the requirement of the standard or complete lab
# (an unknown value does not meet it).
lab_meets() {
  case $2 in '' | *[!0-9]*) return 1 ;; esac
  case $3 in '' | *[!0-9]*) return 1 ;; esac
  if [ "$1" = complete ]; then
    [ "$2" -ge "$LAB_COMPLETE_CPUS" ] && [ "$3" -ge "$LAB_COMPLETE_MIB" ]
  else
    [ "$2" -ge "$LAB_STANDARD_CPUS" ] && [ "$3" -ge "$LAB_STANDARD_MIB" ]
  fi
}

# lab_profiles_has LIST NAME: LIST (COMPOSE_PROFILES, comma-separated) turns on profile NAME.
lab_profiles_has() {
  case ",$(printf '%s' "$1" | tr -d ' ')," in *",$2,"* | *",*,"*) return 0 ;; esac
  return 1
}
# lab_profiles_legacy LIST: LIST still names "full", the former name of the complete profile.
lab_profiles_legacy() {
  case ",$(printf '%s' "$1" | tr -d ' ')," in *",full,"*) return 0 ;; esac
  return 1
}
# lab_setup_kind LIST: "complete" when COMPOSE_PROFILES holds the complete profile (or *), else "standard".
lab_setup_kind() {
  if lab_profiles_has "$1" complete; then printf '%s' complete; else printf '%s' standard; fi
}
# lab_kind_label KIND: "Complete lab" or "Standard lab".
lab_kind_label() {
  if [ "$1" = complete ]; then printf '%s' "Complete lab"; else printf '%s' "Standard lab"; fi
}
# lab_profiles_local_chat LIST: a profile that runs or pulls a local chat model is on.
lab_profiles_local_chat() {
  lab_profiles_has "$1" complete || lab_profiles_has "$1" local-chat || lab_profiles_has "$1" local-models
}

# lab_external_network_missing: print the name of each network the compose file declares as
# external that does not exist in Docker (static read of docker-compose.yml, no interpolation).
lab_external_networks() {
  awk '
    /^networks:[ \t]*$/ { innet = 1; next }
    innet && /^[^ \t#]/ { innet = 0 }
    innet && /^  [A-Za-z0-9_.-]+:[ \t]*$/ { name = $1; sub(/:$/, "", name); next }
    innet && name != "" && /^    external:[ \t]*true/ { print name }
  ' "$LAB_ROOT/docker-compose.yml" 2>/dev/null
}
