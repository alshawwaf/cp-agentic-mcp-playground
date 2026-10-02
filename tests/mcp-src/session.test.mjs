// Credential separation and session lifecycle of the patched MCP launcher (packages/mcp-utils), exercised
// through the real Management MCP server against fake management APIs on loopback:
//   D044  a session's credential source is fixed at initialize: header credentials XOR env credentials
//   D128  later requests cannot re-bind a session; /health shows counts only
//   D127  idle sessions are closed (MCP_SESSION_IDLE_TIMEOUT_SECONDS); at most MCP_MAX_SESSIONS stay open
// plus: unknown session -> 404, DELETE closes cleanly, no secret in the server log.
import crypto from 'node:crypto';
import { startFakeApi, sawSecret } from './lib/fake-api.mjs';
import { initialize, callTool, listTools, del, health, startServer, rssMiB, sleep } from './lib/mcp-client.mjs';
import { CERTS, entry, suite } from './lib/env.mjs';

const t = suite('Sessions and credential separation (Management MCP server)');
const SECRET = 'envkey-' + crypto.randomBytes(8).toString('hex');      // the operator's (test) API key
const HEADER_KEY = 'hdrkey-' + crypto.randomBytes(8).toString('hex');  // a caller's own (test) key
const MGMT = entry('management');

const lab = await startFakeApi({ host: '127.0.0.1', name: 'lab-sms' });        // the configured SMS
const other = await startFakeApi({ host: '127.0.0.2', name: 'other-host' });   // any other host
const envFor = extra => ({
  MANAGEMENT_HOST: '127.0.0.1', MANAGEMENT_PORT: String(lab.port), API_KEY: SECRET,
  NODE_EXTRA_CA_CERTS: CERTS + 'ca.pem', ...extra,
});
const reset = () => { lab.received.length = 0; other.received.length = 0; };
const toOther = { 'management-host': '127.0.0.2', 'management-port': String(other.port) };

{
  const s = await startServer(MGMT, 4102, envFor({}));
  const start = (await health(s.base)).activeSessions;

  let init = await initialize(s.url, toOther);
  t.check('D044: management-host header without credentials -> initialize rejected (400)',
    init.status === 400 && /Invalid session configuration/.test(JSON.stringify(init.body)), init);
  t.check('D044: ... and no zombie session', (await health(s.base)).activeSessions === start, null);
  t.check('D044: env API key never sent to the header host', !sawSecret(other, SECRET), other.received.length);

  init = await initialize(s.url, { ...toOther, 'api-key': HEADER_KEY });
  let call = await callTool(s.url, init.sessionId, 'show_hosts', {});
  t.check('D044: header mode uses the header key at the header host only',
    init.status === 200 && sawSecret(other, HEADER_KEY) && !sawSecret(other, SECRET) && lab.received.length === 0,
    { status: init.status, call: call.text.slice(0, 120) });
  reset();

  const envSession = await initialize(s.url, {});
  call = await callTool(s.url, envSession.sessionId, 'show_hosts', {});
  t.check('D044: env mode (no headers) uses the env key at MANAGEMENT_HOST with TLS verified',
    sawSecret(lab, SECRET) && other.received.length === 0 && /served_by|objects|total/.test(call.text), call.text.slice(0, 160));
  reset();

  const fresh = await initialize(s.url, {});
  call = await callTool(s.url, fresh.sessionId, 'show_hosts', {}, toOther);
  t.check('D128: headers on a later request do not change the session (stays on MANAGEMENT_HOST)',
    sawSecret(lab, SECRET) && other.received.length === 0, { lab: lab.received.length, other: other.received.length });
  reset();

  const h = await health(s.base);
  t.check('D128: /health reports counts only, no session IDs', h.sessions === undefined && typeof h.activeSessions === 'number', h);

  const dbg = await initialize(s.url, { debug: 'true' });
  await callTool(s.url, dbg.sessionId, 'show_hosts', {});
  t.check('D044: a "debug: true" header is ignored; the API key is not logged', !s.logs.text.includes(SECRET) && !/Debug enabled/.test(s.logs.text), null);

  t.check('unknown session ID -> 404 (the client re-initializes)', (await callTool(s.url, crypto.randomUUID(), 'show_hosts')).status === 404, null);
  const n = (await health(s.base)).activeSessions;
  t.check('DELETE closes the session cleanly', (await del(s.url, fresh.sessionId)) === 200 && (await health(s.base)).activeSessions === n - 1, null);
  t.check('no secret anywhere in the server log', !s.logs.text.includes(SECRET) && !s.logs.text.includes(HEADER_KEY), null);
  s.stop();
}

{
  // A sidecar without env credentials (the default lab when no Management key is set)
  const s = await startServer(MGMT, 4108, { NODE_EXTRA_CA_CERTS: CERTS + 'ca.pem' });
  const before = (await health(s.base)).activeSessions;
  const z = await initialize(s.url, { 'management-host': 'zz-review.invalid' });
  const after = (await health(s.base)).activeSessions;
  t.check('D044: failed initialize -> clear 400 error, no zombie session',
    z.status === 400 && z.body?.error?.code === -32000 && after === before, { status: z.status, body: z.body, before, after });
  s.stop();
}

{
  const s = await startServer(MGMT, 4103, envFor({ MCP_SESSION_IDLE_TIMEOUT_SECONDS: '5', MCP_MAX_SESSIONS: '0' }));
  const busy = await initialize(s.url, {});
  const idle = [await initialize(s.url, {}), await initialize(s.url, {}), await initialize(s.url, {})];
  t.check('D127 setup: 4 sessions open', (await health(s.base)).activeSessions === 4, null);
  // keep one session busy for more than two timeouts
  const until = Date.now() + 11000;
  while (Date.now() < until) { await listTools(s.url, busy.sessionId); await sleep(500); }
  const h = await health(s.base);
  t.check('D127: idle sessions closed after MCP_SESSION_IDLE_TIMEOUT_SECONDS, the busy one kept', h.activeSessions === 1, h);
  t.check('D127: a closed session answers 404', (await listTools(s.url, idle[0].sessionId)).status === 404, null);
  t.check('D127: the busy session still works', (await listTools(s.url, busy.sessionId)).status === 200, null);
  t.check('D127: one quiet log line for the cleanup', /Closed 3 idle MCP session\(s\)/.test(s.logs.text), s.logs.text.split('\n').filter(l => /idle/.test(l)));
  s.stop();
}

{
  const s = await startServer(MGMT, 4104, envFor({ MCP_SESSION_IDLE_TIMEOUT_SECONDS: '0', MCP_MAX_SESSIONS: '3' }));
  const ids = [];
  for (let i = 0; i < 5; i++) { ids.push((await initialize(s.url, {})).sessionId); await sleep(20); }
  const h = await health(s.base);
  t.check('D127: at most MCP_MAX_SESSIONS open (least recently used idle session evicted)', h.activeSessions === 3, h);
  t.check('D127: the oldest sessions are evicted (404), the newest works',
    (await listTools(s.url, ids[0])).status === 404 && (await listTools(s.url, ids[1])).status === 404 && (await listTools(s.url, ids[4])).status === 200, null);
  s.stop();
}

{
  // Memory per session, for sizing MCP_MAX_SESSIONS against the sidecar mem_limit
  const s = await startServer(MGMT, 4106, envFor({ MCP_SESSION_IDLE_TIMEOUT_SECONDS: '0', MCP_MAX_SESSIONS: '0' }));
  await sleep(500);
  const r0 = rssMiB(s.child);
  for (let i = 0; i < 30; i++) await initialize(s.url, {});
  await sleep(500);
  const r1 = rssMiB(s.child);
  t.info(`RSS idle ${r0.toFixed(1)} MiB -> ${r1.toFixed(1)} MiB with 30 sessions (${((r1 - r0) / 30).toFixed(2)} MiB per session)`);
  s.stop();
}

lab.close();
other.close();
t.done();
