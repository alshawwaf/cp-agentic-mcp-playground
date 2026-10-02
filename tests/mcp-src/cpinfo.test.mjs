// CPInfo file access (LAB PATCH D097, packages/cpinfo-analysis/src/file-access.ts): file_path is limited to
// regular files under CPINFO_ALLOWED_DIRS (default /data/cpinfo, the lab's n8n/shared folder); symlinks and
// '..' are resolved first; the answer for a path outside is the same whether it exists or not (no existence
// oracle); FIFOs and directories are refused without blocking. Needs writable /data and /outside (tmpfs).
import crypto from 'node:crypto';
import fs from 'node:fs';
import { execFileSync } from 'node:child_process';
import { initialize, callTool, startServer } from './lib/mcp-client.mjs';
import { entry, suite } from './lib/env.mjs';

const t = suite('CPInfo file_path allow-list');
const MARK = 'envmark-' + crypto.randomBytes(6).toString('hex'); // planted in the server env (test value)
const CPINFO = entry('cpinfo-analysis');
const kind = x => (/Access denied/.test(x) ? 'denied' : /File not found/.test(x) ? 'notfound'
  : /A cpinfo file path is required/.test(x) ? 'required' : /Error|error/.test(x) ? 'other-error' : 'ok');

// Lab layout: ./n8n/shared mounted at /data/cpinfo. /outside is what an attacker probes.
for (const p of ['/data/cpinfo', '/data/cpinfo-link']) fs.rmSync(p, { recursive: true, force: true });
for (const p of fs.existsSync('/outside') ? fs.readdirSync('/outside') : []) fs.rmSync(`/outside/${p}`, { recursive: true, force: true });
fs.mkdirSync('/data/cpinfo/subdir', { recursive: true });
fs.mkdirSync('/outside/sub', { recursive: true });
fs.writeFileSync('/data/cpinfo/sample.cpinfo', '=====\nSection: hostname\n=====\nlab-gw-1\n');
fs.writeFileSync('/data/cpinfo/subdir/inner.cpinfo', '=====\nSection: hostname\n=====\nlab-gw-2\n');
fs.writeFileSync('/outside/secret.txt', 'outside the allowed directory\n');
fs.symlinkSync('/proc/self/environ', '/data/cpinfo/escape-env.cpinfo'); // planted symlink to the server env
fs.symlinkSync('/outside/sub', '/data/cpinfo/linkdir');                 // planted symlinked directory
fs.symlinkSync('/outside/secret.txt', '/data/cpinfo/escape.cpinfo');    // planted symlinked file
fs.symlinkSync('/data/cpinfo/subdir', '/data/cpinfo/inner-link');       // a symlink that stays inside
fs.symlinkSync('/data/cpinfo', '/data/cpinfo-link');                    // the allowed dir given as a symlink
execFileSync('mkfifo', ['/data/cpinfo/fifo.cpinfo']);

async function server(port, env = {}) {
  const s = await startServer(CPINFO, port, { MARKER_FOR_TEST: MARK, ...env });
  const { sessionId } = await initialize(s.url);
  const ask = async (p, tool = 'check_initialization_status', extra = {}) => {
    const started = Date.now();
    const r = await callTool(s.url, sessionId, tool, { file_path: p, ...extra });
    return { text: r.text, kind: kind(r.text), ms: Date.now() - started };
  };
  return { s, ask };
}

{
  // Default allow-list (CPINFO_ALLOWED_DIRS unset = /data/cpinfo)
  const { s, ask } = await server(4402);
  const leak = await ask('/proc/self/environ', 'smart_content_search', { keyword: 'MARKER_FOR_TEST' });
  t.check('/proc/self/environ refused (search tool)', leak.kind === 'denied' && !leak.text.includes(MARK), leak.text.slice(0, 200));
  const viaLink = await ask('/data/cpinfo/escape-env.cpinfo', 'smart_content_search', { keyword: 'MARKER_FOR_TEST' });
  t.check('symlink inside /data/cpinfo pointing at /proc/self/environ refused', viaLink.kind === 'denied' && !viaLink.text.includes(MARK), viaLink.text.slice(0, 200));

  const PAIRS = [ // [label, existing target, missing target]
    ['plain outside path', '/etc/passwd', '/etc/no-such-file'],
    ['lexical .. outside', '/data/cpinfo/../../etc/passwd', '/data/cpinfo/../../etc/no-such-file'],
    ['linkdir/.. + file (absolute)', '/data/cpinfo/linkdir/../secret.txt', '/data/cpinfo/linkdir/../missing.txt'],
    ['linkdir/.. + file (relative)', 'linkdir/../secret.txt', 'linkdir/../missing.txt'],
    ['linkdir/../.. + /etc file', '/data/cpinfo/linkdir/../../etc/passwd', '/data/cpinfo/linkdir/../../etc/no-such-file'],
    ['linkdir/.. + directory', '/data/cpinfo/linkdir/../sub', '/data/cpinfo/linkdir/../no-such-dir'],
    ['linkdir/./.. + file', '/data/cpinfo/linkdir/./../secret.txt', '/data/cpinfo/linkdir/./../missing.txt'],
  ];
  for (const [label, existing, missing] of PAIRS) {
    const a = await ask(existing), b = await ask(missing);
    t.check(`${label}: the same "Access denied" for an existing and a missing target (no existence oracle)`,
      a.kind === 'denied' && b.kind === 'denied' && !a.text.includes('/outside') && !b.text.includes('/outside'),
      [a.text.slice(0, 140), b.text.slice(0, 140)]);
  }
  const escape = await ask('/data/cpinfo/escape.cpinfo');
  t.check('symlinked file pointing outside refused', escape.kind === 'denied', escape.text.slice(0, 140));
  const dir = await ask('/data/cpinfo/subdir');
  t.check('a directory inside refused', dir.kind === 'denied', dir.text.slice(0, 140));
  const fifo = await ask('/data/cpinfo/fifo.cpinfo');
  t.check('a FIFO inside refused without blocking', fifo.kind === 'denied' && fifo.ms < 5000, { kind: fifo.kind, ms: fifo.ms });
  for (const p of ['sample.cpinfo', '/data/cpinfo/sample.cpinfo', 'subdir/../sample.cpinfo', 'inner-link/inner.cpinfo',
    '/data/cpinfo/inner-link/inner.cpinfo']) {
    const r = await ask(p);
    t.check(`allowed file works: ${p}`, r.kind === 'ok', r.text.slice(0, 160));
  }
  for (const p of ['/data/cpinfo/not-there.cpinfo', 'not-there.cpinfo', '/data/cpinfo/subdir/missing/x.cpinfo']) {
    const r = await ask(p);
    t.check(`missing file inside says "File not found": ${p}`, r.kind === 'notfound', r.text.slice(0, 140));
  }
  for (const p of ['', '   ', 'a\u0000b']) {
    const r = await ask(p);
    t.check(`empty or NUL path refused (${JSON.stringify(p)})`, r.kind === 'required' || r.kind === 'denied', r.text.slice(0, 140));
  }
  t.check('the planted env value never appears in the server log', !s.logs.text.includes(MARK), null);
  s.stop();
}

{
  // Allowed directory configured through a symlink (CPINFO_ALLOWED_DIRS=/data/cpinfo-link)
  const { s, ask } = await server(4403, { CPINFO_ALLOWED_DIRS: '/data/cpinfo-link' });
  const rel = await ask('sample.cpinfo');
  const abs = await ask('/data/cpinfo-link/sample.cpinfo');
  const a = await ask('/data/cpinfo-link/linkdir/../secret.txt'), b = await ask('/data/cpinfo-link/linkdir/../missing.txt');
  t.check('allowed dir given as a symlink works (relative and absolute)', rel.kind === 'ok' && abs.kind === 'ok', [rel.text.slice(0, 120), abs.text.slice(0, 120)]);
  t.check('allowed dir given as a symlink: no oracle through linkdir/..', a.kind === 'denied' && b.kind === 'denied', [a.text.slice(0, 120), b.text.slice(0, 120)]);
  s.stop();
}

t.done();
