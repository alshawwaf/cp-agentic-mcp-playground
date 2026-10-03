// Minimal Streamable HTTP MCP client + server launcher for the vendored-server tests (loopback only).
import { spawn } from 'node:child_process';
import fs from 'node:fs';

const PROTOCOL = '2025-06-18';
// A request that hangs (a regression, e.g. a blocking read) fails the check instead of stalling the run.
const REQUEST_TIMEOUT_MS = Number(process.env.MCP_TEST_REQUEST_TIMEOUT_MS || 30000);

function parseBody(text) {
  // The response is either JSON or SSE ("event: message\ndata: {...}")
  const dataLines = text.split('\n').filter(l => l.startsWith('data:'));
  const raw = dataLines.length ? dataLines[dataLines.length - 1].slice(5).trim() : text;
  try { return JSON.parse(raw); } catch { return { raw: text }; }
}

export async function post(url, payload, headers = {}) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json, text/event-stream', ...headers },
    body: JSON.stringify(payload),
    signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
  });
  const text = await res.text();
  return { status: res.status, sessionId: res.headers.get('mcp-session-id'), body: parseBody(text) };
}

export async function initialize(url, headers = {}) {
  const r = await post(url, {
    jsonrpc: '2.0', id: 1, method: 'initialize',
    params: { protocolVersion: PROTOCOL, capabilities: {}, clientInfo: { name: 'lab-ci-test', version: '1' } },
  }, headers);
  if (r.sessionId) {
    await post(url, { jsonrpc: '2.0', method: 'notifications/initialized' },
      { 'mcp-session-id': r.sessionId, 'mcp-protocol-version': PROTOCOL, ...headers });
  }
  return r;
}

export async function callTool(url, sessionId, name, args = {}, headers = {}) {
  const r = await post(url, { jsonrpc: '2.0', id: 2, method: 'tools/call', params: { name, arguments: args } },
    { 'mcp-session-id': sessionId, 'mcp-protocol-version': PROTOCOL, ...headers });
  const text = r.body?.result?.content?.map(c => c.text).join('\n') ?? JSON.stringify(r.body);
  return { status: r.status, text, body: r.body };
}

export async function listTools(url, sessionId) {
  return post(url, { jsonrpc: '2.0', id: 3, method: 'tools/list' },
    { 'mcp-session-id': sessionId, 'mcp-protocol-version': PROTOCOL });
}

export async function del(url, sessionId) {
  const res = await fetch(url, {
    method: 'DELETE', headers: { 'mcp-session-id': sessionId, 'mcp-protocol-version': PROTOCOL }, signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
  });
  return res.status;
}

export async function health(base) {
  const res = await fetch(base + '/health', { signal: AbortSignal.timeout(5000) });
  return res.json();
}

/** Start `node <entry> --transport http --transport-port <port>`; resolves when /health answers. */
export async function startServer(entry, port, env) {
  if (!fs.existsSync(entry)) throw new Error(`server build not found: ${entry} (build mcp-src first)`);
  const child = spawn(process.execPath, [entry, '--transport', 'http', '--transport-port', String(port)], {
    env: { PATH: process.env.PATH, ...env }, stdio: ['ignore', 'pipe', 'pipe'],
  });
  const logs = { text: '' };
  child.stdout.on('data', d => { logs.text += d; });
  child.stderr.on('data', d => { logs.text += d; });
  const base = `http://127.0.0.1:${port}`;
  for (let i = 0; i < 150; i++) {
    try { await health(base); return { child, base, url: base + '/mcp', logs, stop: () => child.kill('SIGKILL') }; } catch { /* not up yet */ }
    if (child.exitCode !== null) break;
    await new Promise(r => setTimeout(r, 100));
  }
  child.kill('SIGKILL');
  throw new Error('server did not start: ' + logs.text.slice(0, 800));
}

/** Resident set size of a child process in MiB (Linux /proc). */
export function rssMiB(child) {
  const status = fs.readFileSync(`/proc/${child.pid}/status`, 'utf8');
  return Number(/VmRSS:\s+(\d+)/.exec(status)[1]) / 1024;
}

export const sleep = ms => new Promise(r => setTimeout(r, ms));
