#!/bin/bash
###############################################################################
# Health Check — Check Point Agentic MCP Playground
#
# Validates the running Docker Compose stack. No host ports are published, so
# every service is probed from INSIDE the `demo` network via `docker exec`
# (container running + its listening port answers). The MCP Gateway is also
# checked for aggregated tools.
#
# Usage: ./scripts/health-check.sh [--profile cpu|gpu-nvidia] [--verbose]
# Exit:  0 all healthy | 1 one or more failed | 2 script error
###############################################################################

set -uo pipefail

PROFILE="${PROFILE:-cpu}"
VERBOSE=0
GATEWAY_TOKEN="${MCP_GATEWAY_TOKEN:-cp-mcp-gateway-training-token}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'

while [[ $# -gt 0 ]]; do
  case $1 in
    --profile) PROFILE="$2"; shift 2 ;;
    --timeout) shift 2 ;;                 # accepted for back-compat, unused
    --verbose|-v) VERBOSE=1; shift ;;
    --help|-h) echo "Usage: $0 [--profile cpu|gpu-nvidia] [--verbose]"; exit 0 ;;
    *) echo "Unknown argument: $1"; exit 2 ;;
  esac
done

log_info()    { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[✓]${NC} $1"; }
log_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
log_error()   { echo -e "${RED}[✗]${NC} $1"; }
log_verbose() { [[ $VERBOSE -eq 1 ]] && echo -e "${BLUE}[DEBUG]${NC} $1" || true; }

failed=0

check_running() {   # container
  if docker ps --format '{{.Names}}' | grep -q "^${1}$"; then
    log_success "$1 is running"
  else
    log_error "$1 is NOT running"; failed=$((failed+1)); return 1
  fi
}

# TCP-probe a container's own listening port from inside it. Tries nc, then a
# /dev/tcp bash test, then a node fallback — one of these exists in every image
# used here (busybox nc in the gateway; node in the MCP/n8n image).
check_port() {   # container port label
  local c=$1 p=$2 label=${3:-"$1:$2"}
  if docker exec "$c" sh -c "nc -z 127.0.0.1 $p" >/dev/null 2>&1 \
     || docker exec "$c" sh -c "timeout 3 bash -c '</dev/tcp/127.0.0.1/$p'" >/dev/null 2>&1 \
     || docker exec "$c" node -e "require('net').connect($p,'127.0.0.1').on('connect',()=>process.exit(0)).on('error',()=>process.exit(1))" >/dev/null 2>&1; then
    log_success "$label listening on :$p"
  else
    log_error "$label NOT listening on :$p"; failed=$((failed+1)); return 1
  fi
}

log_info "Health check — profile: $PROFILE"; echo ""

log_info "=== Core ==="
if check_running postgres; then
  if docker exec postgres pg_isready -U "${POSTGRES_USER:-admin}" >/dev/null 2>&1; then
    log_success "PostgreSQL accepts connections"
  else
    log_error "PostgreSQL not ready"; failed=$((failed+1))
  fi
fi
check_running n8n && check_port n8n 5678 "n8n"

OLLAMA_C="ollama-${PROFILE}"; [[ "$PROFILE" == "gpu-nvidia" ]] && OLLAMA_C="ollama-gpu"
check_running "$OLLAMA_C" && check_port "$OLLAMA_C" 11434 "Ollama"

echo ""; log_info "=== AI UIs ==="
check_running open-webui && check_port open-webui 8080 "Open WebUI"
check_running langflow   && check_port langflow 7860 "Langflow"
# Flowise's internal port is env-driven (PORT / FLOWISE_PORT), so resolve it
# from inside the container rather than hard-coding.
if check_running flowise; then
  if docker exec flowise sh -c 'P=${PORT:-${FLOWISE_PORT:-3000}}; nc -z 127.0.0.1 "$P" 2>/dev/null || node -e "require(\"net\").connect(process.env.PORT||process.env.FLOWISE_PORT||3000,\"127.0.0.1\").on(\"connect\",()=>process.exit(0)).on(\"error\",()=>process.exit(1))"' >/dev/null 2>&1; then
    log_success "Flowise listening (container PORT)"
  else
    log_error "Flowise NOT listening"; failed=$((failed+1))
  fi
fi

echo ""; log_info "=== MCP sidecars (internal ports) ==="
for entry in \
  "mcp-documentation:3000" "mcp-https-inspection:3001" "mcp-quantum-management:3002" \
  "mcp-management-logs:3003" "threat-emulation-mcp:3004" "threat-prevention-mcp:3005" \
  "spark-management-mcp:3006" "reputation-service-mcp:3007" "harmony-sase-mcp:3008" \
  "quantum-gw-cli-mcp:3009" "quantum-gw-connection-analysis-mcp:3010" \
  "quantum-gaia-mcp:3011" "cpinfo-analysis-mcp:3012"; do
  c="${entry%%:*}"; p="${entry##*:}"
  check_running "$c" && check_port "$c" "$p" "$c"
done

echo ""; log_info "=== MCP Gateway ==="
if check_running mcp-gateway && check_port mcp-gateway 8080 "mcp-gateway"; then
  # Aggregated tools/list through the gateway with the Bearer token (proves the
  # gateway enumerated its sidecars, not just that the port is open).
  tools=$(T="$GATEWAY_TOKEN" docker exec -e T="$GATEWAY_TOKEN" n8n node -e '
    const http=require("http");
    const post=(b,s)=>new Promise((res,rej)=>{const d=JSON.stringify(b);const h={"Content-Type":"application/json","Accept":"application/json, text/event-stream","Content-Length":Buffer.byteLength(d),"Authorization":"Bearer "+process.env.T};if(s)h["mcp-session-id"]=s;const r=http.request({host:"mcp-gateway",port:8080,path:"/mcp",method:"POST",headers:h},x=>{let f="";x.on("data",c=>f+=c);x.on("end",()=>res({h:x.headers,b:f}))});r.on("error",rej);r.write(d);r.end()});
    (async()=>{const i=await post({jsonrpc:"2.0",id:1,method:"initialize",params:{protocolVersion:"2025-03-26",capabilities:{},clientInfo:{name:"hc",version:"1"}}});const s=i.h["mcp-session-id"];await post({jsonrpc:"2.0",method:"notifications/initialized"},s);const l=await post({jsonrpc:"2.0",id:2,method:"tools/list"},s);const m=(l.b.includes("data:")?l.b.split("\n").filter(x=>x.startsWith("data:")).map(x=>JSON.parse(x.slice(5))):[JSON.parse(l.b)]).find(x=>x.id===2);console.log((m&&m.result&&m.result.tools||[]).length)})().catch(()=>{console.log("0");process.exit(0)})' 2>/dev/null)
  if [[ "${tools:-0}" -gt 0 ]]; then
    log_success "Gateway serving $tools aggregated tools"
  else
    log_warning "Gateway up but returned 0 tools (check sidecar startup order / token)"
  fi
fi

echo ""
if [[ $failed -eq 0 ]]; then
  log_success "All checks passed"; exit 0
else
  log_error "$failed check(s) failed"; exit 1
fi
