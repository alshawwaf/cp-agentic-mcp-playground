// Smoke test for the cp-agentic-n8n image: start every MCP wrapper that the
// compose sidecars use, then run the MCP handshake (initialize ->
// notifications/initialized -> tools/list) over Streamable HTTP against each.
//
// Proves the image does what the sidecars need: each wrapper resolves its CLI,
// the server boots and listens, and it lists its tools. No Check Point
// backend is contacted: the credentials are dummy values, and listing tools is
// local to each server.
//
// Run inside the image (it ships node):
//   docker run --rm --entrypoint node -v "$PWD/.github/scripts:/smoke:ro" \
//     <image> /smoke/mcp-image-smoke.mjs
// Exit code 0 = every server answered tools/list with at least one tool.
import { spawn } from 'node:child_process';

// Mirrors the sidecar entrypoints and ports in docker-compose.yml.
const SERVERS = [
  ['mcp-documentation', 3000],
  ['mcp-https-inspection', 3001],
  ['mcp-quantum-management', 3002],
  ['mcp-management-logs', 3003],
  ['threat-emulation-mcp', 3004],
  ['threat-prevention-mcp', 3005],
  ['spark-management-mcp', 3006],
  ['reputation-service-mcp', 3007],
  ['harmony-sase-mcp', 3008],
  ['quantum-gw-cli-mcp', 3009],
  ['quantum-gw-connection-analysis-mcp', 3010],
  ['quantum-gaia-mcp', 3011],
  ['cpinfo-analysis-mcp', 3012],
  ['policy-insights-mcp', 3013],
];

// Dummy, obviously-fake values so credential-gated servers boot. 127.0.0.1:9
// (discard) guarantees nothing real is reachable.
const DUMMY_ENV = {
  CLIENT_ID: 'ci-smoke-not-a-real-id',
  SECRET_KEY: 'ci-smoke-not-a-real-secret',
  REGION: 'EU',
  INFINITY_PORTAL_URL: 'https://127.0.0.1:9',
  MANAGEMENT_HOST: '127.0.0.1',
  MANAGEMENT_PORT: '9',
  API_KEY: 'ci-smoke-not-a-real-key',
  GAIA_GATEWAY_IP: '127.0.0.1',
  GAIA_GATEWAY_PORT: '9',
  GAIA_USERNAME: 'ci-smoke',
  GAIA_PASSWORD: 'ci-smoke-not-a-real-password',
  LOG_LEVEL: 'warn',
};

const BOOT_TIMEOUT_MS = Number(process.env.SMOKE_BOOT_TIMEOUT_MS || 90000);
const ACCEPT = 'application/json, text/event-stream';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// A Streamable HTTP response is either plain JSON or an SSE stream whose
// `data:` lines carry the JSON-RPC message.
function parseRpc(contentType, body) {
  if ((contentType || '').includes('text/event-stream')) {
    for (const line of body.split(/\r?\n/)) {
      if (line.startsWith('data:')) {
        const msg = JSON.parse(line.slice(5).trim());
        if (msg.id !== undefined) return msg;
      }
    }
    throw new Error('no JSON-RPC response in the SSE stream');
  }
  return JSON.parse(body);
}

async function rpc(port, body, sessionId) {
  const headers = { 'content-type': 'application/json', accept: ACCEPT };
  if (sessionId) headers['mcp-session-id'] = sessionId;
  const res = await fetch(`http://127.0.0.1:${port}/mcp`, {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(15000),
  });
  const text = await res.text();
  return { res, text };
}

async function probe(name, port, child) {
  const started = Date.now();
  const init = {
    jsonrpc: '2.0',
    id: 1,
    method: 'initialize',
    params: {
      protocolVersion: '2025-06-18',
      capabilities: {},
      clientInfo: { name: 'ci-image-smoke', version: '1' },
    },
  };
  let first;
  for (;;) {
    if (child.exitCode !== null) {
      throw new Error(`exited with code ${child.exitCode} before listening`);
    }
    try {
      first = await rpc(port, init);
      break;
    } catch (err) {
      if (Date.now() - started > BOOT_TIMEOUT_MS) {
        throw new Error(`not listening on :${port} after ${BOOT_TIMEOUT_MS / 1000}s (${err.message})`);
      }
      await sleep(1000);
    }
  }
  if (!first.res.ok) throw new Error(`initialize HTTP ${first.res.status}`);
  const initMsg = parseRpc(first.res.headers.get('content-type'), first.text);
  if (initMsg.error) throw new Error(`initialize error: ${JSON.stringify(initMsg.error)}`);
  const sid = first.res.headers.get('mcp-session-id') || undefined;
  await rpc(port, { jsonrpc: '2.0', method: 'notifications/initialized' }, sid);
  const list = await rpc(port, { jsonrpc: '2.0', id: 2, method: 'tools/list', params: {} }, sid);
  if (!list.res.ok) throw new Error(`tools/list HTTP ${list.res.status}`);
  const msg = parseRpc(list.res.headers.get('content-type'), list.text);
  if (msg.error) throw new Error(`tools/list error: ${JSON.stringify(msg.error)}`);
  const tools = (msg.result && msg.result.tools) || [];
  if (tools.length === 0) throw new Error('tools/list returned 0 tools');
  return { server: (initMsg.result && initMsg.result.serverInfo && initMsg.result.serverInfo.name) || '?', tools: tools.length };
}

const children = [];
const logs = new Map();
for (const [bin, port] of SERVERS) {
  const child = spawn(`/usr/local/bin/${bin}`, ['--transport', 'http', '--transport-port', String(port)], {
    env: { ...process.env, ...DUMMY_ENV },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  const buf = [];
  const keep = (d) => {
    buf.push(String(d));
    if (buf.length > 40) buf.shift();
  };
  child.stdout.on('data', keep);
  child.stderr.on('data', keep);
  child.on('error', (err) => keep(`spawn error: ${err.message}`));
  logs.set(bin, buf);
  children.push([bin, port, child]);
}

const results = await Promise.all(
  children.map(async ([bin, port, child]) => {
    try {
      const r = await probe(bin, port, child);
      return { bin, port, ok: true, detail: `${r.tools} tools (${r.server})` };
    } catch (err) {
      return { bin, port, ok: false, detail: err.message };
    }
  }),
);

for (const [, , child] of children) {
  if (child.exitCode === null) child.kill('SIGTERM');
}

let failed = 0;
console.log('MCP image smoke test (start each wrapper, then initialize + tools/list)');
for (const r of results) {
  console.log(`${r.ok ? 'PASS' : 'FAIL'}  ${r.bin.padEnd(36)} :${r.port}  ${r.detail}`);
  if (!r.ok) {
    failed += 1;
    const tail = logs.get(r.bin).join('').trim().split('\n').slice(-10).join('\n      ');
    if (tail) console.log(`      last output:\n      ${tail}`);
  }
}
console.log(`${results.length - failed}/${results.length} servers passed`);
process.exit(failed ? 1 : 0);
