#!/usr/bin/env python3
"""Policy check of the resolved Compose configuration (CI gate, standard library only).

    docker compose config --format json > default.json
    docker compose --profile '*' config --format json > all.json
    python3 check_compose_policy.py default.json all.json docker-compose.yml

Enforces the lab's containment and laptop-safety promises (lab-test DESIGN sections 3, 4, 7, 10):
  - no service publishes a host port, runs privileged, or shares the host network / PID namespace
  - only docker-socket-proxy mounts the Docker socket, read-only, with POST=0; the MCP gateway
    reaches Docker only through it
  - every service has a CPU and a memory limit
  - every third-party image is pinned by tag and digest (the lab's own GHCR images and the local
    PolicyPilot image are exempt: their tag is chosen in .env)
  - the security-lab and ai-red-team services are opt-in (not in the default stack), sit only on
    internal networks, and keep their hardening (vuln-mcp: read-only, uid 65534, no capabilities,
    read-only mounts; AI-Infra-Guard: no SYS_ADMIN, no seccomp=unconfined, no-new-privileges)
  - aig-agent drops all capabilities and adds back only what its root entrypoint needs (KILL, SETGID,
    SETUID: it starts the scanners as uid 1000 with gosu and restarts them), has a read-only root
    file system and a positive pids_limit, and mounts nothing writable from the host
  - aig-ui, the opt-in proxy to the AI-Infra-Guard UI, is opt-in, joins exactly aig-ui-access and ai-red-team,
    and is read-only with no capabilities, no-new-privileges, read-only mounts and IP forwarding off
  - no service passes .env through with env_file (unresolved op:// references would reach containers)
Prints one line per finding (service and rule, never a value). Exit 1 on any finding.
"""
import json
import re
import sys

OWN_IMAGE = re.compile(r"^ghcr\.io/[a-z0-9_.-]+/cp-agentic-[a-z0-9-]+:[A-Za-z0-9_.-]+$")
LOCAL_IMAGES = {"policypilot-mcp"}            # built locally from the PolicyPilot repo (POLICYPILOT_IMAGE)
PINNED = re.compile(r"^[^@\s]+:[^@\s]+@sha256:[0-9a-f]{64}$")
OPT_IN = {"vuln-mcp": "security-lab", "aig-webserver": "ai-red-team", "aig-agent": "ai-red-team"}
UI_PROXY, UI_PROXY_NETWORKS = "aig-ui", {"aig-ui-access", "ai-red-team"}
SCANNER, SCANNER_CAPS = "aig-agent", {"KILL", "SETGID", "SETUID"}
SOCKET = "/var/run/docker.sock"


def load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def cap_names(svc, key):
    """Capability names of cap_add / cap_drop, upper case, without the CAP_ prefix."""
    return {str(c).upper().removeprefix("CAP_") for c in svc.get(key) or []}


def main(argv):
    if len(argv) != 4:
        print(__doc__.strip().splitlines()[0])
        print("usage: check_compose_policy.py DEFAULT.json ALL_PROFILES.json docker-compose.yml")
        return 2
    default, full, compose_path = load(argv[1]), load(argv[2]), argv[3]
    services = full.get("services", {})
    networks = full.get("networks", {})
    findings = []

    def bad(svc, rule):
        findings.append(f"{svc}: {rule}")

    for name in OPT_IN:
        if name not in services:
            bad(name, "expected service is missing from docker-compose.yml")
        if name in default.get("services", {}):
            bad(name, f"must be opt-in (profile {OPT_IN[name]}), but it is in the default stack")

    for name, svc in sorted(services.items()):
        if svc.get("ports"):
            bad(name, "publishes a host port (publish ports in a local docker-compose.override.yml instead)")
        if svc.get("privileged"):
            bad(name, "runs privileged")
        if svc.get("network_mode") == "host" or svc.get("pid") == "host" or svc.get("ipc") == "host":
            bad(name, "shares a host namespace (network_mode/pid/ipc: host)")
        limits = (svc.get("deploy") or {}).get("resources", {}).get("limits", {})
        if not (svc.get("cpus") or limits.get("cpus")):
            bad(name, "has no CPU limit (cpus)")
        if not (svc.get("mem_limit") or limits.get("memory")):
            bad(name, "has no memory limit (mem_limit)")

        image = svc.get("image") or ""
        if name not in LOCAL_IMAGES and not OWN_IMAGE.match(image) and not PINNED.match(image):
            bad(name, "third-party image is not pinned by tag and digest (image: name:tag@sha256:...)")

        for vol in svc.get("volumes") or []:
            if SOCKET in (vol.get("source") or ""):
                if name != "docker-socket-proxy":
                    bad(name, "mounts the Docker socket (only docker-socket-proxy may)")
                elif not vol.get("read_only"):
                    bad(name, "mounts the Docker socket read-write")

        if name in OPT_IN:
            nets = list((svc.get("networks") or {}).keys())
            for net in nets:
                if not (networks.get(net) or {}).get("internal"):
                    bad(name, f"is on network {net}, which is not internal (attack containment)")
            if not nets:
                bad(name, "has no network: it would join the default network")
            secopt = [str(x) for x in svc.get("security_opt") or []]
            if not any(x.startswith("no-new-privileges") and not x.endswith("false") for x in secopt):
                bad(name, "lacks security_opt no-new-privileges")
            if any("unconfined" in x for x in secopt):
                bad(name, "disables seccomp or AppArmor (unconfined)")
            if cap_names(svc, "cap_add") & {"SYS_ADMIN", "ALL"}:
                bad(name, "adds SYS_ADMIN / ALL capabilities")

    vuln = services.get("vuln-mcp") or {}
    if vuln:
        if not vuln.get("read_only"):
            bad("vuln-mcp", "root file system is not read-only")
        if "ALL" not in (vuln.get("cap_drop") or []):
            bad("vuln-mcp", "does not drop all capabilities")
        if str(vuln.get("user", "")).split(":")[0] != "65534":
            bad("vuln-mcp", "does not run as uid 65534")
        for vol in vuln.get("volumes") or []:
            if vol.get("type") == "bind" and not vol.get("read_only"):
                bad("vuln-mcp", "has a writable bind mount")

    scanner = services.get(SCANNER) or {}
    if scanner:
        if "ALL" not in cap_names(scanner, "cap_drop"):
            bad(SCANNER, "does not drop all capabilities")
        if cap_names(scanner, "cap_add") - SCANNER_CAPS:
            bad(SCANNER, "adds capabilities beyond KILL, SETGID and SETUID")
        if not scanner.get("read_only"):
            bad(SCANNER, "root file system is not read-only")
        limits = (scanner.get("deploy") or {}).get("resources", {}).get("limits", {})
        pids = scanner.get("pids_limit") or limits.get("pids") or 0
        if not (isinstance(pids, int) and pids > 0):  # 0 or -1 means unlimited
            bad(SCANNER, "has no pids_limit")
        for vol in scanner.get("volumes") or []:
            if vol.get("type") == "bind" and not vol.get("read_only"):
                bad(SCANNER, "has a writable bind mount")

    ui = services.get(UI_PROXY) or {}
    if ui:
        if UI_PROXY in default.get("services", {}):
            bad(UI_PROXY, "must be opt-in (profile ai-red-team), but it is in the default stack")
        if set((ui.get("networks") or {}).keys()) != UI_PROXY_NETWORKS:
            bad(UI_PROXY, "must join exactly the aig-ui-access and ai-red-team networks (attack containment)")
        if not ui.get("read_only"):
            bad(UI_PROXY, "root file system is not read-only")
        if "ALL" not in (ui.get("cap_drop") or []) or ui.get("cap_add"):
            bad(UI_PROXY, "must drop all capabilities and add none")
        if not any(str(x).startswith("no-new-privileges") and not str(x).endswith("false")
                   for x in ui.get("security_opt") or []):
            bad(UI_PROXY, "lacks security_opt no-new-privileges")
        for vol in ui.get("volumes") or []:
            if vol.get("type") == "bind" and not vol.get("read_only"):
                bad(UI_PROXY, "has a writable bind mount")
        sysctls = ui.get("sysctls") or {}
        for key in ("net.ipv4.ip_forward", "net.ipv6.conf.all.forwarding"):
            if str(sysctls.get(key, "")) != "0":
                bad(UI_PROXY, f"must set sysctl {key}=0 (no packet forwarding between its networks)")

    proxy = services.get("docker-socket-proxy") or {}
    if str((proxy.get("environment") or {}).get("POST", "")) != "0":
        bad("docker-socket-proxy", "must set POST=0 (read-only Docker API)")
    gateway = services.get("mcp-gateway") or {}
    if (gateway.get("environment") or {}).get("DOCKER_HOST") != "tcp://docker-socket-proxy:2375":
        bad("mcp-gateway", "must reach Docker only through DOCKER_HOST=tcp://docker-socket-proxy:2375")

    with open(compose_path, encoding="utf-8") as fh:
        for no, line in enumerate(fh, 1):
            if re.match(r"^\s*env_file\s*:", line):
                bad(f"{compose_path}:{no}", "env_file passes .env through to a container (use explicit environment entries)")

    for f in findings:
        print(f"FAIL  {f}")
    print(f"compose policy: {len(services)} services checked (all profiles), {len(findings)} finding(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
