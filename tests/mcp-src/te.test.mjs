// Threat Emulation file access (LAB PATCH, packages/threat-emulation/src/lib/file-access.ts): upload_file,
// scan_file and query_file read only regular files under TE_ALLOWED_DIRS (default /data/shared, the lab's
// n8n/shared folder); symlinks and '..' resolved; no existence oracle; FIFOs refused without blocking.
//
// The container maps te-api.checkpoint.com to 127.0.0.1 (--add-host) and has no network, so the fake TE
// cloud below (127.0.0.1:443, certificate from the run's test CA) records every upload and nothing can
// reach the real Threat Emulation service. Needs writable /data and /secret (tmpfs).
import crypto from 'node:crypto';
import dns from 'node:dns/promises';
import fs from 'node:fs';
import https from 'node:https';
import { execFileSync } from 'node:child_process';
import { initialize, callTool, startServer } from './lib/mcp-client.mjs';
import { CERTS, entry, suite } from './lib/env.mjs';

const t = suite('Threat Emulation file_path allow-list');
const API_KEY = 'tekey-' + crypto.randomBytes(8).toString('hex');      // generated test value
const MARK = 'envmark-' + crypto.randomBytes(6).toString('hex');       // planted in the server env
const OUTSIDE = 'outside-secret-' + crypto.randomBytes(6).toString('hex');
const SAMPLE = 'sample-content-' + crypto.randomBytes(6).toString('hex');
const TE = entry('threat-emulation');

const { address } = await dns.lookup('te-api.checkpoint.com');
if (address !== '127.0.0.1') {
  console.error(`te-api.checkpoint.com resolves to ${address}, not 127.0.0.1: run this suite through run-suites.sh (--add-host)`);
  process.exit(2);
}

// --- fake TE cloud -----------------------------------------------------------------------------
const received = [];
const fake = https.createServer({ key: fs.readFileSync(CERTS + 'te-fake.key'), cert: fs.readFileSync(CERTS + 'te-fake.pem') }, (req, res) => {
  const chunks = [];
  req.on('data', c => chunks.push(c));
  req.on('end', () => {
    received.push({ path: req.url, body: Buffer.concat(chunks).toString('latin1') });
    res.setHeader('Content-Type', 'application/json');
    if (req.url.includes('/upload')) res.end(JSON.stringify({ response: { status: { code: 1002, label: 'UPLOAD_SUCCESS', message: 'ok' } } }));
    else if (req.url.includes('/query')) res.end(JSON.stringify({ response: { status: { code: 1001, label: 'FOUND' }, te: { combined_verdict: 'benign' } } }));
    else res.end('{}');
  });
});
await new Promise(r => fake.listen(443, '127.0.0.1', r));
const sent = needle => received.some(x => x.body.includes(needle));
const reset = () => { received.length = 0; };

// --- files ------------------------------------------------------------------------------------
fs.mkdirSync('/data/shared/subdir', { recursive: true });
fs.mkdirSync('/data/other', { recursive: true });
fs.mkdirSync('/secret', { recursive: true });
fs.writeFileSync('/data/shared/sample.bin', SAMPLE);
fs.writeFileSync('/data/other/other.bin', SAMPLE + '-other');
fs.writeFileSync('/secret/creds.txt', OUTSIDE);
const link = (target, at) => { try { fs.symlinkSync(target, at); } catch { /* exists */ } };
link('/proc/self/environ', '/data/shared/escape.bin');
link('/secret', '/data/shared/linkdir');
link('/secret/creds.txt', '/data/shared/creds-link.bin');
link('/data/shared/sample.bin', '/data/shared/inner-link.bin');
try { execFileSync('mkfifo', ['/data/shared/pipe.bin']); } catch { /* exists */ }

const serverEnv = extra => ({ API_KEY, MARKER_FOR_TEST: MARK, NODE_EXTRA_CA_CERTS: CERTS + 'ca.pem', ...extra });
const errText = r => { try { return JSON.parse(r.text).error ?? ''; } catch { return r.text; } };
const withTimeout = (p, ms) => Promise.race([p, new Promise(r => setTimeout(() => r({ text: 'TIMEOUT', timeout: true }), ms))]);

{
  const s = await startServer(TE, 4802, serverEnv({}));
  const { sessionId } = await initialize(s.url);
  const denied = async (label, args, tool = 'upload_file') => {
    const r = await withTimeout(callTool(s.url, sessionId, tool, args), 8000);
    const ok = /Access denied/.test(errText(r)) && received.length === 0 && !r.text.includes(MARK) && !r.text.includes(OUTSIDE);
    t.check(`${label} -> Access denied, nothing sent`, ok, { text: r.text.slice(0, 160), sent: received.length });
    reset();
    return r;
  };
  await denied('upload_file /secret/creds.txt (outside)', { file_path: '/secret/creds.txt' });
  await denied('upload_file /proc/self/environ', { file_path: '/proc/self/environ' });
  await denied('symlink in /data/shared -> /proc/self/environ', { file_path: '/data/shared/escape.bin' });
  await denied('symlink in /data/shared -> outside file', { file_path: 'creds-link.bin' });
  await denied('path through a symlinked directory to outside', { file_path: '/data/shared/linkdir/creds.txt' });
  await denied('/data/shared/../../secret/creds.txt', { file_path: '/data/shared/../../secret/creds.txt' });
  await denied('relative subdir/../../../secret/creds.txt', { file_path: 'subdir/../../../secret/creds.txt' });
  await denied('the allowed directory itself', { file_path: '/data/shared' });
  await denied('a sub-directory', { file_path: 'subdir' });
  const fifo = await denied('a FIFO in /data/shared (no blocking read)', { file_path: 'pipe.bin' });
  t.check('the FIFO is refused promptly', !fifo.timeout, null);
  await denied('scan_file /proc/self/environ', { file_path: '/proc/self/environ' }, 'scan_file');
  await denied('scan_file outside file', { file_path: '/secret/creds.txt' }, 'scan_file');
  await denied('query_file with file_path outside (MD5 not computed)', { sha1: 'a'.repeat(40), file_path: '/secret/creds.txt' }, 'query_file');

  const strip = (r, p) => errText(r).split(p).join('<path>');
  for (const [miss, exist] of [
    ['/etc/does-not-exist', '/etc/hostname'],
    ['/data/shared/linkdir/missing.txt', '/data/shared/linkdir/creds.txt'],
    ['/secret/missing/../creds.txt', '/secret/creds.txt'],
  ]) {
    const a = await callTool(s.url, sessionId, 'upload_file', { file_path: miss });
    const b = await callTool(s.url, sessionId, 'upload_file', { file_path: exist });
    t.check(`the same answer for missing ${miss} and existing ${exist} (no existence oracle)`,
      /Access denied/.test(errText(a)) && strip(a, miss) === strip(b, exist), [strip(a, miss).slice(0, 60), strip(b, exist).slice(0, 60)]);
  }
  reset();

  const nf = await callTool(s.url, sessionId, 'upload_file', { file_path: 'missing.bin' });
  t.check('a missing file inside /data/shared -> File not found', /File not found/.test(errText(nf)) && received.length === 0, errText(nf));

  let r = await callTool(s.url, sessionId, 'upload_file', { file_path: 'sample.bin' });
  t.check('a relative file name in /data/shared uploads', /File uploaded successfully/.test(r.text) && sent(SAMPLE) && sent('"file_name":"sample.bin"'), r.text.slice(0, 120));
  reset();
  r = await callTool(s.url, sessionId, 'upload_file', { file_path: '/data/shared/sample.bin' });
  t.check('an absolute path under /data/shared uploads', /File uploaded successfully/.test(r.text) && sent(SAMPLE), r.text.slice(0, 120));
  reset();
  r = await callTool(s.url, sessionId, 'upload_file', { file_path: 'inner-link.bin' });
  t.check('a symlink that stays inside /data/shared is allowed', /File uploaded successfully/.test(r.text) && sent(SAMPLE), r.text.slice(0, 120));
  reset();
  r = await callTool(s.url, sessionId, 'scan_file', { file_path: 'sample.bin' });
  t.check('scan_file of a shared file works', /already analyzed|Scan completed/.test(r.text), r.text.slice(0, 120));
  reset();
  const md5 = crypto.createHash('md5').update(SAMPLE).digest('hex');
  r = await callTool(s.url, sessionId, 'query_file', { sha1: 'b'.repeat(40), file_path: 'sample.bin' });
  t.check('query_file computes the MD5 of a shared file', /Query completed/.test(r.text) && sent(md5), r.text.slice(0, 120));
  reset();
  r = await callTool(s.url, sessionId, 'query_file', { sha1: 'b'.repeat(40), file_path: 'missing.bin' });
  t.check('query_file with a missing shared file proceeds without MD5 (upstream behaviour)', /Query completed/.test(r.text) && received.length > 0, r.text.slice(0, 120));
  reset();
  r = await callTool(s.url, sessionId, 'query_file', { md5 });
  t.check('query_file by hash only still works', /Query completed/.test(r.text), r.text.slice(0, 120));
  reset();
  t.check('the TE API key and the planted env value never appear in the server log', !s.logs.text.includes(API_KEY) && !s.logs.text.includes(MARK), null);
  s.stop();
}
{
  const s = await startServer(TE, 4803, serverEnv({ TE_ALLOWED_DIRS: '/data/other' }));
  const { sessionId } = await initialize(s.url);
  const a = await callTool(s.url, sessionId, 'upload_file', { file_path: '/data/shared/sample.bin' });
  const b = await callTool(s.url, sessionId, 'upload_file', { file_path: 'other.bin' });
  t.check('TE_ALLOWED_DIRS=/data/other replaces the default', /Access denied/.test(errText(a)) && /File uploaded successfully/.test(b.text) && sent(SAMPLE + '-other'),
    [errText(a).slice(0, 80), b.text.slice(0, 80)]);
  s.stop();
  reset();
}

fake.close();
t.done();
