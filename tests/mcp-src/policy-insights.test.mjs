// Policy Insights (vendored from upstream 0.3.5 into packages/policy-insights, PATCHES.md §11): the same
// tools as the published npm @chkp/policy-insights-mcp@0.3.5 (fixtures/policy-insights-0.3.5-tools.json),
// plus the lab patches: no telemetry, TLS verified, header XOR env credentials, no secrets in logs, session
// cleanup. Loopback only.
//
// Regenerate the fixture (only when the vendored copy is deliberately moved to a new upstream release):
//   node policy-insights.test.mjs --write-fixture <npm package dist/index.js> <version> <npm integrity> <gitHead>
// (run it where the package's dependencies resolve, e.g. from a pibase/ folder inside a built mcp-src tree)
import crypto from 'node:crypto';
import fs from 'node:fs';
import { startFakeApi, sawSecret } from './lib/fake-api.mjs';
import { initialize, callTool, listTools, del, health, startServer, sleep } from './lib/mcp-client.mjs';
import { CERTS, entry, suite } from './lib/env.mjs';

const FIXTURE = new URL('./fixtures/policy-insights-0.3.5-tools.json', import.meta.url).pathname;
const PI = process.env.PI_WORK_ENTRY || entry('policy-insights');
let port = 4700;

// Fields the MCP SDK itself adds (they differ between SDK releases, not between the two builds).
const normalize = tools => tools.map(({ execution, inputSchema: { additionalProperties, $schema, ...schema } = {}, ...rest }) => ({ ...rest, inputSchema: schema }));

async function toolList(serverEntry) {
  const s = await startServer(serverEntry, port++, { MANAGEMENT_HOST: '127.0.0.1', API_KEY: 'x', TELEMETRY_DISABLED: 'true' });
  const init = await initialize(s.url);
  const tl = await listTools(s.url, init.sessionId);
  s.stop();
  return { serverInfo: init.body?.result?.serverInfo, tools: tl.body?.result?.tools ?? [] };
}

if (process.argv[2] === '--write-fixture') {
  const [, , , npmEntry, version, integrity = '', gitHead = ''] = process.argv;
  const base = await toolList(npmEntry);
  fs.writeFileSync(FIXTURE, JSON.stringify({
    source: `npm @chkp/policy-insights-mcp@${version} (gitHead ${gitHead}), tools/list normalized by tests/mcp-src/policy-insights.test.mjs`,
    integrity,
    serverInfo: { name: base.serverInfo?.name, version: base.serverInfo?.version },
    tools: normalize(base.tools),
  }, null, 2) + '\n');
  console.log(`wrote ${FIXTURE}: ${base.tools.length} tools`);
  process.exit(0);
}

const t = suite('Policy Insights (vendored) parity and lab patches');
const SECRET = 'envkey-' + crypto.randomBytes(8).toString('hex');      // the operator's (test) API key
const HEADER_KEY = 'hdrkey-' + crypto.randomBytes(8).toString('hex');  // a caller's own (test) key

const selfSigned = await startFakeApi({ host: '127.0.0.1', certName: 'self', name: 'self-signed' });
const lab = await startFakeApi({ host: '127.0.0.1', certName: 'server', name: 'lab-sms' });      // CA-signed, the configured SMS
const other = await startFakeApi({ host: '127.0.0.2', certName: 'server', name: 'other-host' });  // any other host
const reset = () => { for (const f of [selfSigned, lab, other]) f.received.length = 0; };
const envFor = (fake, extra = {}) => ({
  MANAGEMENT_HOST: '127.0.0.1', MANAGEMENT_PORT: String(fake.port), API_KEY: SECRET, TELEMETRY_DISABLED: 'true', ...extra,
});
const toOther = { 'management-host': '127.0.0.2', 'management-port': String(other.port) };
const start = env => startServer(PI, port++, env);

// ---------------- static checks on the bundle ----------------
{
  const work = fs.readFileSync(PI, 'utf8');
  t.check('bundle has no rejectUnauthorized: false and uses the patched client (MANAGEMENT_CA_CERT)', !/rejectUnauthorized:\s*(false|!1)/.test(work) && work.includes('MANAGEMENT_CA_CERT'), null);
  t.check('bundle contains no telemetry code', !work.includes('metrics.security.ai.checkpoint.com') && !work.includes('machineIdSync'), null);
}

// ---------------- tool parity with npm 0.3.5 ----------------
{
  const fixture = JSON.parse(fs.readFileSync(FIXTURE, 'utf8'));
  const work = await toolList(PI);
  const names = list => list.map(x => x.name).sort();
  t.check(`same ${fixture.tools.length} tools as npm 0.3.5 (names, descriptions, input schemas)`,
    work.tools.length === fixture.tools.length && JSON.stringify(normalize(work.tools)) === JSON.stringify(fixture.tools),
    { fixture: names(fixture.tools), work: names(work.tools) });
  t.check('same serverInfo name and version as npm 0.3.5',
    work.serverInfo?.name === fixture.serverInfo.name && work.serverInfo?.version === fixture.serverInfo.version,
    { fixture: fixture.serverInfo, work: work.serverInfo });

  const s = await start(envFor(lab, { NODE_EXTRA_CA_CERTS: CERTS + 'ca.pem' }));
  const a = await initialize(s.url), b = await initialize(s.url);
  const la = await listTools(s.url, a.sessionId), lb = await listTools(s.url, b.sessionId);
  t.check('two concurrent sessions each get their own server with all tools',
    a.sessionId !== b.sessionId && (la.body?.result?.tools ?? []).length === fixture.tools.length && (lb.body?.result?.tools ?? []).length === fixture.tools.length, null);
  s.stop();
}

// ---------------- TLS ----------------
{
  let s = await start(envFor(selfSigned));
  let { sessionId } = await initialize(s.url);
  let r = await callTool(s.url, sessionId, 'ShowConfig', {});
  t.check('self-signed SMS rejected (TLS certificate verification failed)', /TLS certificate verification failed/.test(r.text), r.text.slice(0, 200));
  t.check('the API key never reached the unverified server', !sawSecret(selfSigned, SECRET), selfSigned.received.length);
  t.check('no secret in the server log', !s.logs.text.includes(SECRET), null);
  s.stop(); reset();

  s = await start(envFor(selfSigned, { NODE_TLS_REJECT_UNAUTHORIZED: '0' }));
  ({ sessionId } = await initialize(s.url));
  r = await callTool(s.url, sessionId, 'ShowConfig', {});
  t.check('NODE_TLS_REJECT_UNAUTHORIZED=0 cannot switch verification off', /TLS certificate verification failed/.test(r.text) && !sawSecret(selfSigned, SECRET), r.text.slice(0, 160));
  s.stop(); reset();

  s = await start(envFor(lab));
  ({ sessionId } = await initialize(s.url));
  r = await callTool(s.url, sessionId, 'ShowConfig', {});
  t.check('private-CA SMS rejected without the CA', /TLS certificate verification failed/.test(r.text) && !sawSecret(lab, SECRET), r.text.slice(0, 160));
  s.stop(); reset();

  for (const v of ['MANAGEMENT_CA_CERT', 'NODE_EXTRA_CA_CERTS']) {
    s = await start(envFor(lab, { [v]: CERTS + 'ca.pem' }));
    ({ sessionId } = await initialize(s.url));
    r = await callTool(s.url, sessionId, 'ShowConfig', {});
    t.check(`${v}=ca.pem: verified and works`, /served_by.*lab-sms/s.test(r.text) && sawSecret(lab, SECRET), r.text.slice(0, 160));
    s.stop(); reset();
  }

  s = await start(envFor(selfSigned, { MANAGEMENT_CA_CERT: CERTS + 'self.pem', MANAGEMENT_TLS_SERVERNAME: 'sms.lab.test' }));
  ({ sessionId } = await initialize(s.url));
  r = await callTool(s.url, sessionId, 'ShowConfig', {});
  t.check('self-signed certificate trusted via MANAGEMENT_CA_CERT + MANAGEMENT_TLS_SERVERNAME: works', /served_by.*self-signed/s.test(r.text), r.text.slice(0, 160));
  s.stop(); reset();
}

// ---------------- credential separation (D044 / D128) and redaction ----------------
{
  const s = await start(envFor(lab, { NODE_EXTRA_CA_CERTS: CERTS + 'ca.pem' }));
  const n0 = (await health(s.base)).activeSessions;
  let init = await initialize(s.url, toOther);
  t.check('D044: management-host header without credentials -> 400 Invalid session configuration',
    init.status === 400 && /Invalid session configuration/.test(JSON.stringify(init.body)), init.status);
  t.check('D044: ... no zombie session, the env key never sent to the header host', (await health(s.base)).activeSessions === n0 && !sawSecret(other, SECRET), null);

  init = await initialize(s.url, { ...toOther, 'api-key': HEADER_KEY });
  let r = await callTool(s.url, init.sessionId, 'ShowConfig', {});
  t.check('D044: header mode uses the header key at the header host only',
    init.status === 200 && sawSecret(other, HEADER_KEY) && !sawSecret(other, SECRET) && lab.received.length === 0, r.text.slice(0, 120));
  reset();

  const envSession = await initialize(s.url, {});
  r = await callTool(s.url, envSession.sessionId, 'ShowConfig', {});
  t.check('D044: env mode uses the env key at MANAGEMENT_HOST only', sawSecret(lab, SECRET) && other.received.length === 0, r.text.slice(0, 120));
  reset();

  const fresh = await initialize(s.url, {});
  r = await callTool(s.url, fresh.sessionId, 'ShowConfig', {}, { ...toOther, 'api-key': HEADER_KEY });
  t.check('D128: headers on a later request do not change the session', sawSecret(lab, SECRET) && other.received.length === 0,
    { lab: lab.received.length, other: other.received.length, text: r.text.slice(0, 100) });
  reset();

  r = await callTool(s.url, envSession.sessionId, 'ShowConfig', { domain: 'bad\ndomain' });
  t.check('a domain with control characters is refused before any request', /invalid domain name/.test(r.text) && lab.received.length === 0, r.text.slice(0, 120));
  reset();
  t.check('no API key (env or header) anywhere in the server log', !s.logs.text.includes(SECRET) && !s.logs.text.includes(HEADER_KEY), null);
  s.stop();
}
{
  const s = await start(envFor(lab, { NODE_EXTRA_CA_CERTS: CERTS + 'ca.pem', DEBUG: 'true' }));
  const a = await initialize(s.url, { ...toOther, 'api-key': HEADER_KEY });
  await callTool(s.url, a.sessionId, 'ShowSuggestionsSummary', { requestBody: {} });
  const b = await initialize(s.url, {});
  await callTool(s.url, b.sessionId, 'ShowConfig', {});
  t.check('DEBUG=true logs the tool call but no API key (env or header)',
    /Executing tool "ShowConfig"/.test(s.logs.text) && !s.logs.text.includes(SECRET) && !s.logs.text.includes(HEADER_KEY), null);
  s.stop(); reset();
}

// ---------------- session lifecycle (D127) ----------------
{
  let s = await start(envFor(lab, { MCP_SESSION_IDLE_TIMEOUT_SECONDS: '3', MCP_MAX_SESSIONS: '0' }));
  const busy = await initialize(s.url);
  const idle = [await initialize(s.url), await initialize(s.url), await initialize(s.url)];
  const until = Date.now() + 8000;
  while (Date.now() < until) { await listTools(s.url, busy.sessionId); await sleep(500); }
  const h = await health(s.base);
  t.check('D127: idle sessions closed after MCP_SESSION_IDLE_TIMEOUT_SECONDS, the busy one kept', h.activeSessions === 1, h);
  t.check('D127: a closed session -> 404; the busy session works',
    (await listTools(s.url, idle[0].sessionId)).status === 404 && (await listTools(s.url, busy.sessionId)).status === 200, null);
  t.check('D128: /health reports counts only', h.sessions === undefined && h.sessionIdleTimeoutSeconds === 3, h);
  const n = (await health(s.base)).activeSessions;
  t.check('DELETE closes the session cleanly and the server stays up',
    (await del(s.url, busy.sessionId)) === 200 && (await health(s.base)).activeSessions === n - 1 && (await health(s.base)).status === 'ok', null);
  s.stop();

  s = await start(envFor(lab, { MCP_SESSION_IDLE_TIMEOUT_SECONDS: '0', MCP_MAX_SESSIONS: '3' }));
  const ids = [];
  for (let i = 0; i < 5; i++) { ids.push((await initialize(s.url)).sessionId); await sleep(20); }
  t.check('D127: at most MCP_MAX_SESSIONS open, the oldest idle session evicted',
    (await health(s.base)).activeSessions === 3 && (await listTools(s.url, ids[0])).status === 404 && (await listTools(s.url, ids[4])).status === 200, null);
  s.stop();
}

selfSigned.close();
lab.close();
other.close();
t.done();
