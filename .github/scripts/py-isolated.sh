#!/bin/sh
# .github/scripts/py-isolated.sh: run a Python command from this repository in ONE throwaway container:
# the python image docker-compose.yml pins (tag + digest), no network, read-only root file system and
# repository mount, no capabilities, no new privileges, uid 65534, capped CPU / memory / processes,
# a private /tmp. CI runs every Python check and unit test through it; it works the same on a laptop.
#
#   .github/scripts/py-isolated.sh [-C DIR] [-i HOSTDIR] [--] python3 ARGS...
#     -C DIR       working directory, relative to the repository root (default: the root)
#     -i HOSTDIR   also mount HOSTDIR read-only at /input (generated files a check reads)
# Environment: PY_ISOLATED_CPUS (default 1), PY_ISOLATED_MEMORY (default 768m).
set -u

REPO=$(cd "$(dirname "$0")/../.." && pwd -P) || exit 2
workdir=/repo
input=
while [ $# -gt 0 ]; do
  case $1 in
    -C) [ $# -ge 2 ] || { echo "py-isolated: -C needs a directory" >&2; exit 2; }; workdir=/repo/$2; shift ;;
    -i) [ $# -ge 2 ] || { echo "py-isolated: -i needs a directory" >&2; exit 2; }
      input=$(cd "$2" && pwd -P) || exit 2; shift ;;
    --) shift; break ;;
    *) break ;;
  esac
  shift
done
[ $# -gt 0 ] || { echo "usage: py-isolated.sh [-C DIR] [-i HOSTDIR] [--] python3 ARGS..." >&2; exit 2; }

# The image the lab's Python services use: the first python: image in docker-compose.yml (tag@digest).
IMAGE=${PY_ISOLATED_IMAGE:-$(sed -n 's/^ *image: *\(python:[^ ]*@sha256:[0-9a-f]*\).*/\1/p' "$REPO/docker-compose.yml" | head -n 1)}
[ -n "$IMAGE" ] || { echo "py-isolated: no pinned python image found in docker-compose.yml" >&2; exit 2; }

run() {
  exec docker run --rm -i --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
    --user 65534:65534 --cpus "${PY_ISOLATED_CPUS:-1}" -m "${PY_ISOLATED_MEMORY:-768m}" --pids-limit 256 \
    --tmpfs /tmp:rw,nosuid,nodev,size=256m -e HOME=/tmp -e PYTHONDONTWRITEBYTECODE=1 \
    -e PYTHONPYCACHEPREFIX=/tmp/pycache -e PYTHONUNBUFFERED=1 \
    -v "$REPO":/repo:ro -w "$workdir" "$@"
}
if [ -n "$input" ]; then
  run -v "$input":/input:ro "$IMAGE" "$@"
else
  run "$IMAGE" "$@"
fi
