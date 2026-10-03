// Gaia allow-list (LAB PATCH D043/D096, packages/gaia): GAIA_USERNAME / GAIA_PASSWORD go only to
// GAIA_GATEWAY_IP (plus GAIA_ALLOWED_GATEWAYS), never to a gateway named in a prompt; host and port are
// validated; TLS is verified (GAIA_CA_CERT); blank settings answer "not configured" at once.
// Real Gaia MCP server against fake Gaia APIs on loopback.
import crypto from 'node:crypto';
import { startFakeApi, sawSecret } from './lib/fake-api.mjs';
import { initialize, callTool, startServer } from './lib/mcp-client.mjs';
import { CERTS, entry, suite } from './lib/env.mjs';

const t = suite('Gaia gateway allow-list');
const SECRET = 'gaiapw-' + crypto.randomBytes(8).toString('hex'); // generated test password
const GAIA = entry('gaia');

const gw = await startFakeApi({ host: '127.0.0.1', name: 'lab-gateway' });   // GAIA_GATEWAY_IP
const evil = await startFakeApi({ host: '127.0.0.2', name: 'other-host' });   // a host named in a prompt
const reset = () => { gw.received.length = 0; evil.received.length = 0; };
const env = extra => ({
  GAIA_GATEWAY_IP: '127.0.0.1', GAIA_GATEWAY_PORT: String(gw.port), GAIA_USERNAME: 'admin', GAIA_PASSWORD: SECRET,
  GAIA_CA_CERT: CERTS + 'ca.pem', ...extra,
});

{
  const s = await startServer(GAIA, 4302, env({}));
  const { sessionId } = await initialize(s.url);
  let r = await callTool(s.url, sessionId, 'show_dns', { gateway_ip: '127.0.0.2', port: evil.port });
  t.check('D043: a gateway_ip that is not configured is refused', /Refusing to connect to gateway "127\.0\.0\.2"/.test(r.text), r.text.slice(0, 200));
  t.check('D043: the other host received nothing', evil.received.length === 0, evil.received);

  r = await callTool(s.url, sessionId, 'show_dns', {});
  t.check('no gateway_ip: the configured gateway, TLS verified with GAIA_CA_CERT', sawSecret(gw, SECRET) && /served_by|objects/.test(r.text), r.text.slice(0, 200));
  reset();

  r = await callTool(s.url, sessionId, 'show_dns', { gateway_ip: ' 127.0.0.1 ' });
  t.check('the configured gateway_ip given explicitly (normalized) is allowed', sawSecret(gw, SECRET), r.text.slice(0, 120));
  reset();

  for (const bad of ['https://127.0.0.1', '127.0.0.1:443', '127.0.0.1/x', 'a b', '010.0.0.1', '0x7f.1', 'user@host']) {
    r = await callTool(s.url, sessionId, 'show_dns', { gateway_ip: bad });
    t.check(`invalid gateway_ip ${JSON.stringify(bad)} rejected`, /not a valid IP address or host name/.test(r.text) && evil.received.length === 0 && gw.received.length === 0, r.text.slice(0, 160));
  }
  for (const badPort of [0, 70000, 1.5]) {
    r = await callTool(s.url, sessionId, 'show_dns', { port: badPort });
    t.check(`invalid port ${badPort} rejected`, /port must be a whole number/.test(r.text), r.text.slice(0, 160));
  }
  t.check('the Gaia password is not in the server log', !s.logs.text.includes(SECRET), null);
  s.stop();
}
reset();

{
  const s = await startServer(GAIA, 4303, env({ GAIA_ALLOWED_GATEWAYS: '127.0.0.2, gw2.lab.test' }));
  const { sessionId } = await initialize(s.url);
  await callTool(s.url, sessionId, 'show_dns', { gateway_ip: '127.0.0.2', port: evil.port });
  t.check('GAIA_ALLOWED_GATEWAYS lets the operator add more gateways', sawSecret(evil, SECRET), evil.received.length);
  s.stop();
}
reset();

{
  const s = await startServer(GAIA, 4304, env({ GAIA_CA_CERT: '' }));
  const { sessionId } = await initialize(s.url);
  const r = await callTool(s.url, sessionId, 'show_dns', {});
  t.check('D053: gateway with an untrusted certificate -> TLS error with guidance, password not sent',
    /TLS certificate verification failed/.test(r.text) && gw.received.length === 0, r.text.slice(0, 200));
  s.stop();
}
reset();

{
  const started = Date.now();
  const s = await startServer(GAIA, 4305, { GAIA_GATEWAY_IP: '', GAIA_USERNAME: '', GAIA_PASSWORD: '' });
  const { sessionId } = await initialize(s.url);
  const r = await callTool(s.url, sessionId, 'show_dns', { gateway_ip: '127.0.0.2' });
  t.check('GAIA_* blank -> immediate "not configured" (no browser-dialog wait)',
    /Gaia is not configured/.test(r.text) && Date.now() - started < 10000, { ms: Date.now() - started, text: r.text.slice(0, 160) });
  s.stop();
}

{
  // IPv6 literal: validated, compared without brackets, dialed with brackets (no IPv6 server needed)
  const s = await startServer(GAIA, 4306, env({ GAIA_GATEWAY_IP: '::1', GAIA_GATEWAY_PORT: '9' }));
  const { sessionId } = await initialize(s.url);
  const r = await callTool(s.url, sessionId, 'show_dns', { gateway_ip: '[::1]' });
  t.check('IPv6 gateway_ip [::1] matches GAIA_GATEWAY_IP=::1 and is dialed as https://[::1]:9', /https:\/\/\[::1\]:9\/gaia_api/.test(r.text), r.text.slice(0, 200));
  s.stop();
}

gw.close();
evil.close();
t.done();
