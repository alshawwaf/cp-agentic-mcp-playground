// Throwaway fake Check Point API (management web_api / Gaia gaia_api) on loopback, for these tests only.
// It records what it receives so a test can assert where credentials went.
import https from 'node:https';
import fs from 'node:fs';
import { CERTS } from './env.mjs';

export function startFakeApi({ host = '127.0.0.1', certName = 'server', name = 'fake' } = {}) {
  const received = [];
  const server = https.createServer({
    key: fs.readFileSync(CERTS + certName + '.key'),
    cert: fs.readFileSync(CERTS + certName + '.pem'),
  }, (req, res) => {
    let body = '';
    req.on('data', c => { body += c; });
    req.on('end', () => {
      received.push({ path: req.url, body, sid: req.headers['x-chkp-sid'] });
      res.setHeader('Content-Type', 'application/json');
      if (req.url.endsWith('/login')) {
        res.end(JSON.stringify({ sid: `${name}-sid`, 'session-timeout': 600, uid: 'u-1' }));
      } else if (req.url.endsWith('/show-session')) {
        res.end(JSON.stringify({ domain: { 'domain-type': 'domain' } }));
      } else {
        res.end(JSON.stringify({ objects: [], total: 0, served_by: name }));
      }
    });
  });
  return new Promise(resolve => server.listen(0, host, () => {
    resolve({ server, port: server.address().port, host, received, close: () => server.close() });
  }));
}

/** True if any recorded request body contains `needle`. */
export function sawSecret(fake, needle) {
  return fake.received.some(r => (r.body || '').includes(needle));
}
