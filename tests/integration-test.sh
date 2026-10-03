#!/bin/sh
# tests/integration-test.sh: integration test of the Check Point AI agent lab.
#
#   1. docker compose config --quiet         the compose file and .env are valid
#   2. docker compose up -d                  only with --up: starts or updates the lab
#   3. wait until no service is starting     (--wait SECONDS, default 900)
#   4. tests/acceptance/run.sh [options]     the acceptance tests; every option this script does
#                                            not know (and everything after --) is passed on
#
# Safe on a lab in use: it never stops the lab and never removes containers, volumes or images.
# Needs only sh and docker. Exit status: 0 = every step passed, 1 = a step or check failed,
# 2 = the test could not run.
#
#   tests/integration-test.sh                         check the running lab
#   tests/integration-test.sh --up --no-model         start it (CI), then check it without model calls
#   tests/integration-test.sh --up -- --json result.json --mock-provider

set -u

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd -P) || exit 2
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd -P) || exit 2
LAB_ROOT=$REPO_ROOT
# shellcheck source=SCRIPTDIR/test-helpers.sh
. "$SCRIPT_DIR/test-helpers.sh"
# shellcheck source=SCRIPTDIR/../scripts/lib/labenv.sh
. "$REPO_ROOT/scripts/lib/labenv.sh"

usage() {
  cat <<'EOF'
Usage: tests/integration-test.sh [--up] [--wait SECONDS] [--project-dir DIR] [--] [acceptance options]

  --up               Start or update the lab first (docker compose up -d). Nothing is removed.
  --wait SECONDS     How long to wait for services that are still starting (default 900).
  --project-dir DIR  Lab directory holding docker-compose.yml and .env (default: this repository).
                     Passed on to the acceptance tests.
  -h, --help

Other options go to tests/acceptance/run.sh (see tests/acceptance/run.sh --help), for example
--no-model, --only IDS, --json FILE, --mock-provider. This script never stops the lab.
EOF
}

up=0
wait_s=900
project_args=
while [ $# -gt 0 ]; do
  case $1 in
    --up) up=1 ;;
    --wait) [ $# -ge 2 ] || lab_die "--wait needs a number of seconds" 2; wait_s=$2; shift ;;
    --project-dir) [ $# -ge 2 ] || lab_die "--project-dir needs a directory" 2
      LAB_ROOT=$(cd "$2" && pwd -P) || lab_die "no such directory: $2" 2
      project_args=$LAB_ROOT; shift ;;
    -h | --help) usage; exit 0 ;;
    --) shift; break ;;
    *) break ;;
  esac
  shift
done
case $wait_s in '' | *[!0-9]*) lab_die "--wait needs a number of seconds" 2 ;; esac
lab_have_docker || lab_die "docker is not installed or not on PATH" 2
lab_docker_up || lab_die "Docker is not running" 2

# pending_services: running services that are still starting (health "starting", or a one-shot job
# that has not finished yet).
pending_services() {
  _pp=$(lab_project_name)
  _ids=$(docker ps -q --no-trunc --filter "label=com.docker.compose.project=$_pp" \
    --filter "label=com.docker.compose.oneoff=False" 2>/dev/null </dev/null | tr '\n' ' ')
  [ -n "$(printf '%s' "$_ids" | tr -d ' ')" ] || return 0
  # shellcheck disable=SC2086
  docker inspect -f '{{index .Config.Labels "com.docker.compose.service"}}|{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}|{{.HostConfig.RestartPolicy.Name}}' \
    $_ids 2>/dev/null </dev/null |
    awk -F'|' '$3 == "starting" || ($2 == "running" && $3 == "" && ($4 == "no" || $4 == "")) { printf "%s ", $1 }'
}

log_info "Check Point AI agent lab: integration test (lab directory $LAB_ROOT)"

log_info "Step 1: docker compose config"
lab_compose config --quiet </dev/null
assert_true $? "docker-compose.yml and .env are valid (docker compose config --quiet)"

if [ "$up" = 1 ]; then
  log_info "Step 2: docker compose up -d (starts or updates the lab; nothing is removed)"
  lab_compose up -d </dev/null
  assert_true $? "the lab starts (docker compose up -d)"
else
  log_info "Step 2: skipped (the lab is already running; --up starts it)"
fi

log_info "Step 3: waiting up to ${wait_s}s for services that are still starting"
_tries=$(( (wait_s + 9) / 10 ))
_pending=$(pending_services)
while [ -n "$_pending" ] && [ "$_tries" -gt 0 ]; do
  log_info "  still starting: $_pending"
  sleep 10
  _tries=$((_tries - 1))
  _pending=$(pending_services)
done
assert_equals "" "$_pending" "no service is still starting"

log_info "Step 4: acceptance tests (tests/acceptance/run.sh)"
if [ -n "$project_args" ]; then
  "$SCRIPT_DIR/acceptance/run.sh" --project-dir "$project_args" "$@"
else
  "$SCRIPT_DIR/acceptance/run.sh" "$@"
fi
acc_rc=$?
assert_true "$acc_rc" "acceptance tests"

if print_test_summary; then exit 0; fi
[ "$acc_rc" -eq 2 ] && exit 2
exit 1
