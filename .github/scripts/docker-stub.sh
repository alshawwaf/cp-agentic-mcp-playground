#!/bin/sh
# Stand-in for the docker CLI on CI runners without Docker (the macOS job): lets ./setup.sh and
# ./scripts/doctor.sh --preflight run their real code paths (BSD sed, stat, /bin/sh) with a Docker that
# "is running" and can use 4 CPUs and 8 GiB. It starts nothing and pulls nothing; images are never
# present, so doctor skips the LiteLLM image check. Installed as "docker" on PATH by the workflow.
# DOCKER_STUB_LOG=FILE records the first two arguments of every call (never the values passed in).
[ -n "${DOCKER_STUB_LOG:-}" ] && printf '%s %s\n' "${1-}" "${2-}" >> "$DOCKER_STUB_LOG"

case ${1-} in
  info)
    case "$*" in
      *--format*) printf '4 8589934592 Docker Desktop\n' ;;
      *) printf 'Server Version: stub\n' ;;
    esac
    exit 0 ;;
  version) printf 'Docker version 0.0.0-ci-stub\n'; exit 0 ;;
  compose)
    case ${2-} in
      version) printf 'Docker Compose version v2.0.0-ci-stub\n'; exit 0 ;;
      config) exit 0 ;;         # empty output: the scripts fall back to reading docker-compose.yml
      ps) exit 0 ;;
      *) printf 'docker stub: docker compose %s is not supported in this CI job\n' "${2-}" >&2; exit 1 ;;
    esac ;;
  network)
    case ${2-} in
      inspect | create) exit 0 ;;
      ls) exit 0 ;;
    esac ;;
  volume | ps | images) exit 0 ;;
  image)
    [ "${2-}" = inspect ] && exit 1   # no image is ever present
    exit 0 ;;
esac
printf 'docker stub: docker %s is not supported in this CI job\n' "${1-}" >&2
exit 1
