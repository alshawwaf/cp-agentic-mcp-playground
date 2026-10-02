// TLS verification of the on-premises management client (LAB PATCH D053/D066, packages/infra):
// verification is always on, trust comes only from NODE_EXTRA_CA_CERTS or MANAGEMENT_CA_CERT,
// the host name is always checked, and no API key is ever logged. Loopback only.
import { spawn } from 'node:child_process';
import crypto from 'node:crypto';
import { startFakeApi, sawSecret } from './lib/fake-api.mjs';
import { CERTS, suite } from './lib/env.mjs';

const t = suite('TLS verification (management API client)');
const SECRET = 'testkey-' + crypto.randomBytes(8).toString('hex'); // generated test value, not a real key

function runClient(host, port, env = {}) {
  // async spawn: the fake servers live in this process and must keep serving meanwhile
  return new Promise(resolve => {
    const child = spawn(process.execPath, [new URL('./lib/tls-client.mjs', import.meta.url).pathname, host, String(port)], {
      env: { PATH: process.env.PATH, MCP_SRC_DIR: process.env.MCP_SRC_DIR || '', TEST_SECRET: SECRET, ...env },
    });
    let stdout = '', stderr = '';
    child.stdout.on('data', d => { stdout += d; });
    child.stderr.on('data', d => { stderr += d; });
    const timer = setTimeout(() => child.kill('SIGKILL'), 20000);
    child.on('close', () => {
      clearTimeout(timer);
      let out;
      try { out = JSON.parse(stdout.trim().split('\n').pop()); } catch { out = { ok: false, error: 'no output: ' + stderr.slice(0, 300) }; }
      out.logHasSecret = (stderr + stdout).includes(SECRET);
      resolve(out);
    });
  });
}

const selfSigned = await startFakeApi({ certName: 'self', name: 'self-signed' }); // names sms.lab.test only
const caSigned = await startFakeApi({ certName: 'server', name: 'ca-signed' });   // lab-CA-signed, valid for 127.0.0.1

let r = await runClient('127.0.0.1', selfSigned.port);
t.check('self-signed certificate rejected when no CA is configured', r.ok === false && /TLS certificate verification failed/.test(r.error || ''), r);
t.check('API key never reached the untrusted server', !sawSecret(selfSigned, SECRET), selfSigned.received.length);
t.check('API key not in any log output', r.logHasSecret === false, null);

r = await runClient('127.0.0.1', caSigned.port);
t.check('private-CA certificate rejected without the CA', r.ok === false, r);

r = await runClient('127.0.0.1', caSigned.port, { NODE_EXTRA_CA_CERTS: CERTS + 'ca.pem' });
t.check('NODE_EXTRA_CA_CERTS=ca.pem: verified and works', r.ok === true && sawSecret(caSigned, SECRET), r);
caSigned.received.length = 0;

r = await runClient('127.0.0.1', caSigned.port, { MANAGEMENT_CA_CERT: CERTS + 'ca.pem' });
t.check('MANAGEMENT_CA_CERT=ca.pem: verified and works', r.ok === true, r);

r = await runClient('127.0.0.1', caSigned.port, { MANAGEMENT_CA_CERT: CERTS + 'evil-ca.pem' });
t.check('MANAGEMENT_CA_CERT with the wrong CA: rejected', r.ok === false, r);

r = await runClient('127.0.0.1', caSigned.port, { MANAGEMENT_CA_CERT: CERTS + 'missing.pem' });
t.check('unreadable MANAGEMENT_CA_CERT: clear error, no request', r.ok === false && /CA certificate file/.test(r.error || ''), r);

selfSigned.received.length = 0;
r = await runClient('127.0.0.1', selfSigned.port, { NODE_TLS_REJECT_UNAUTHORIZED: '0' });
t.check('NODE_TLS_REJECT_UNAUTHORIZED=0 cannot switch verification off', r.ok === false && !sawSecret(selfSigned, SECRET), r);

// Self-signed SMS certificate trusted explicitly, but it names sms.lab.test, not the IP address dialed
r = await runClient('127.0.0.1', selfSigned.port, { MANAGEMENT_CA_CERT: CERTS + 'self.pem' });
t.check('trusted certificate that does not name the host: host name check still enforced', r.ok === false && /ALTNAME|altnames|not in the cert/i.test(r.error || ''), r);

r = await runClient('127.0.0.1', selfSigned.port, {
  MANAGEMENT_CA_CERT: CERTS + 'self.pem', MANAGEMENT_HOST: '127.0.0.1', MANAGEMENT_TLS_SERVERNAME: 'sms.lab.test' });
t.check('+ MANAGEMENT_TLS_SERVERNAME=sms.lab.test for the configured host: works', r.ok === true, r);

r = await runClient('127.0.0.1', selfSigned.port, {
  MANAGEMENT_CA_CERT: CERTS + 'self.pem', MANAGEMENT_HOST: '10.9.9.9', MANAGEMENT_TLS_SERVERNAME: 'sms.lab.test' });
t.check('servername override ignored for a host other than MANAGEMENT_HOST', r.ok === false, r);

// Real SMS / Gaia certificates are often self-signed END-ENTITY certificates (CA:FALSE)
const leafOnly = await startFakeApi({ certName: 'leaf', name: 'leaf' });
r = await runClient('127.0.0.1', leafOnly.port, {
  MANAGEMENT_CA_CERT: CERTS + 'leaf.pem', MANAGEMENT_HOST: '127.0.0.1', MANAGEMENT_TLS_SERVERNAME: 'sms2.lab.test' });
t.check('self-signed CA:FALSE server certificate trusted via MANAGEMENT_CA_CERT + servername: works', r.ok === true, r);
r = await runClient('127.0.0.1', leafOnly.port, {
  NODE_EXTRA_CA_CERTS: CERTS + 'leaf.pem', MANAGEMENT_HOST: '127.0.0.1', MANAGEMENT_TLS_SERVERNAME: 'sms2.lab.test' });
t.check('same certificate via NODE_EXTRA_CA_CERTS + servername: works', r.ok === true, r);
leafOnly.close();

// Redaction: the raw axios error used to carry the login body (with the API key) into the log
r = await runClient('127.0.0.1', 1, { CLIENT_DEBUG: '1' });
t.check('connection error with debug on does not log the API key', r.logHasSecret === false && r.ok === false, r.error);

selfSigned.close();
caSigned.close();
t.done();
