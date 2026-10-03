#!/bin/sh
# tests/mcp-src/run.sh: security regression tests for the vendored Check Point MCP servers
# (docker/n8n/mcp-src, the LAB PATCH changes listed in docker/n8n/mcp-src/PATCHES.md).
#
#   1. build   copy docker/n8n/mcp-src (sources only) to a work directory outside the repository and
#              build it in ONE throwaway node container (the builder image docker/n8n/Dockerfile pins):
#              npx -y npm@10.9.4 ci + nx build, exactly as the image build does. Network: npm registry only.
#   2. test    run the suites in ONE throwaway container with no network at all (--network none),
#              a read-only root file system, no capabilities, capped CPU and memory. Test certificates
#              are generated inside it for this run only; the fake Check Point APIs listen on loopback.
#
# Suites: tls session delete gaia cpinfo te spark policy-insights static (see tests/mcp-src/*.test.mjs).
# Needs only sh and docker. Nothing is written to the repository (no node_modules, no dist).
#
#   tests/mcp-src/run.sh                            build in a temporary directory, test, remove it
#   tests/mcp-src/run.sh --work DIR --build-only    CI step 1 (keeps DIR)
#   tests/mcp-src/run.sh --work DIR --skip-build    CI step 2: test an existing build
#   tests/mcp-src/run.sh --work DIR --skip-build tls gaia     only these suites
#
# Options: --work DIR, --build-only, --skip-build, --npm-cache DIR (reuse an npm cache), --keep.
# Exit status: 0 = every suite passed, 1 = a check failed, 2 = the tests could not run.

set -u

HERE=$(cd "$(dirname "$0")" && pwd -P) || exit 2
REPO=$(cd "$HERE/../.." && pwd -P) || exit 2
SRC="$REPO/docker/n8n/mcp-src"
DOCKERFILE="$REPO/docker/n8n/Dockerfile"

die() { printf 'mcp-src tests: %s\n' "$1" >&2; exit "${2:-2}"; }
say() { printf 'mcp-src tests: %s\n' "$*"; }

work=
keep=0
build=1
test=1
npm_cache=
while [ $# -gt 0 ]; do
  case $1 in
    --work) [ $# -ge 2 ] || die "--work needs a directory"; work=$2; keep=1; shift ;;
    --build-only) test=0 ;;
    --skip-build) build=0 ;;
    --npm-cache) [ $# -ge 2 ] || die "--npm-cache needs a directory"; npm_cache=$2; shift ;;
    --keep) keep=1 ;;
    -h | --help) sed -n '2,23p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    --) shift; break ;;
    -*) die "unknown option: $1 (see --help)" ;;
    *) break ;;
  esac
  shift
done
suites=$*

command -v docker >/dev/null 2>&1 || die "docker is not installed or not on PATH"
docker info >/dev/null 2>&1 </dev/null || die "Docker is not running"

# The builder image of docker/n8n/Dockerfile (tag and digest), so the tests use the same Node and npm.
NODE_IMAGE=${MCP_TEST_NODE_IMAGE:-$(sed -n 's/^FROM --platform=[^ ]* \(node:[^ ]*\) AS builder.*/\1/p' "$DOCKERFILE" | head -n 1)}
[ -n "$NODE_IMAGE" ] || die "could not read the builder image from $DOCKERFILE"
CPUS=${MCP_TEST_CPUS:-2}
MEM=${MCP_TEST_MEMORY:-2g}
uid=$(id -u)
gid=$(id -g)

created=0
if [ -z "$work" ]; then
  work=$(mktemp -d "${TMPDIR:-/tmp}/cp-mcp-src-tests.XXXXXX") || die "could not create a work directory"
  created=1
fi
mkdir -p "$work" || die "could not create $work"
work=$(cd "$work" && pwd -P) || exit 2
case $work/ in "$REPO"/*) die "the work directory must be outside the repository ($work)" ;; esac
# shellcheck disable=SC2329  # called from the traps below
cleanup() {
  if [ "$created" = 1 ] && [ "$keep" = 0 ]; then rm -rf "$work"; fi
}
trap cleanup EXIT
trap 'cleanup; exit 130' INT TERM HUP

if [ "$build" = 1 ]; then
  say "build: copying docker/n8n/mcp-src (sources only) to $work/src"
  mkdir -p "$work/src" || exit 2
  (cd "$SRC" && tar -cf - --exclude=node_modules --exclude=dist --exclude=.nx --exclude='*.tsbuildinfo' .) |
    (cd "$work/src" && tar -xf -) || die "copying the sources failed"
  cache_args=
  cache_env=npm_config_cache=/tmp/npm-cache
  if [ -n "$npm_cache" ]; then
    mkdir -p "$npm_cache" || exit 2
    npm_cache=$(cd "$npm_cache" && pwd -P) || exit 2
    cache_args="-v $npm_cache:/npm-cache"
    cache_env=npm_config_cache=/npm-cache
  fi
  say "build: npm ci (npm 10.9.4) + nx build in $NODE_IMAGE (--cpus $CPUS, --memory $MEM)"
  # shellcheck disable=SC2086  # cache_args is empty or one -v option without spaces in its parts
  docker run --rm --name "cp-mcp-src-build-$$" --cpus "$CPUS" -m "$MEM" --pids-limit 1024 \
    --cap-drop ALL --security-opt no-new-privileges --user "$uid:$gid" \
    -e HOME=/tmp/home -e "$cache_env" -e NODE_ENV=development -e npm_config_production=false \
    -e NX_DAEMON=false -e NX_NO_CLOUD=true -e NX_SKIP_NX_CACHE=true -e CI=true \
    -v "$work/src":/src -w /src $cache_args "$NODE_IMAGE" sh -c '
      set -e; mkdir -p "$HOME"
      npx -y npm@10.9.4 ci --no-audit --no-fund > /tmp/ci.log 2>&1 || { tail -40 /tmp/ci.log; exit 1; }
      tail -n 2 /tmp/ci.log
      npx nx run-many --target=build --all --parallel=1 --skip-nx-cache > /tmp/build.log 2>&1 ||
        { grep -iE "error|failed" /tmp/build.log | head -40; tail -20 /tmp/build.log; exit 1; }
      grep -E "Successfully ran" /tmp/build.log | tail -n 1' </dev/null || die "the build failed" 1
fi

if [ "$test" = 1 ]; then
  [ -f "$work/src/packages/management/dist/index.js" ] || die "no build in $work/src (run without --skip-build first)"
  say "test: suites in $NODE_IMAGE with --network none, read-only, no capabilities (--cpus $CPUS, --memory $MEM)"
  # te-api.checkpoint.com -> 127.0.0.1: the fake Threat Emulation cloud of te.test.mjs listens there (port 443,
  # allowed without capabilities by the per-container sysctl). No network, so nothing can leave the container.
  # shellcheck disable=SC2086  # suites: plain words
  docker run --rm --name "cp-mcp-src-test-$$" --network none --read-only --cap-drop ALL \
    --security-opt no-new-privileges --user "$uid:$gid" --cpus "$CPUS" -m "$MEM" --pids-limit 512 \
    --add-host te-api.checkpoint.com:127.0.0.1 --sysctl net.ipv4.ip_unprivileged_port_start=0 \
    --tmpfs /tmp --tmpfs /data:mode=1777 --tmpfs /secret:mode=1777 --tmpfs /outside:mode=1777 \
    -e HOME=/tmp -v "$work/src":/src/work:ro -v "$HERE":/src/tests:ro \
    "$NODE_IMAGE" sh /src/tests/run-suites.sh $suites </dev/null
  exit $?
fi
exit 0
