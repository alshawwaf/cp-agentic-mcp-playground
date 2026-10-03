// Static checks on the built mcp-src tree: every server the lab image wraps was built, no bundle switches
// TLS verification off or carries the upstream telemetry, and redactSecrets() masks credential fields.
import fs from 'node:fs';
import { WORK, entry, suite } from './lib/env.mjs';

const t = suite('Static checks on the built servers');

// The 14 server packages behind the wrappers in docker/n8n/Dockerfile (and the compose sidecars).
const SERVERS = ['documentation-tool', 'https-inspection', 'management', 'management-logs', 'threat-emulation',
  'threat-prevention', 'spark-management', 'reputation-service', 'harmony-sase', 'gw-cli',
  'gw-cli-connection-analysis', 'gaia', 'cpinfo-analysis', 'policy-insights'];

const TLS_OFF = [
  [/rejectUnauthorized\s*:\s*(false|!1|0)\b/, 'rejectUnauthorized: false'],
  [/NODE_TLS_REJECT_UNAUTHORIZED\s*\]?\s*=\s*['"`]0/, 'NODE_TLS_REJECT_UNAUTHORIZED = "0"'],
  [/checkServerIdentity\s*:\s*\(\)\s*=>\s*(undefined|null|void 0)/, 'checkServerIdentity disabled'],
];
const TELEMETRY = ['metrics.security.ai.checkpoint.com', 'machineIdSync'];

for (const pkg of SERVERS) {
  const file = entry(pkg);
  if (!fs.existsSync(file)) {
    t.check(`${pkg}: built (dist/index.js exists)`, false, file);
    continue;
  }
  const code = fs.readFileSync(file, 'utf8');
  const tlsOff = TLS_OFF.filter(([re]) => re.test(code)).map(([, label]) => label);
  const telemetry = TELEMETRY.filter(s => code.includes(s));
  t.check(`${pkg}: built, TLS verification never switched off, no telemetry`, tlsOff.length === 0 && telemetry.length === 0, { tlsOff, telemetry });
}

const { redactSecrets } = await import(`${WORK}/packages/mcp-utils/dist/index.js`);
const input = {
  apiKey: 'k1', 'api-key': 'k2', API_KEY: 'k3', password: 'p', GAIA_PASSWORD: 'p2', clientSecret: 's', token: 't',
  Authorization: 'Bearer x', cookie: 'c', 'X-chkp-sid': 'sid1', 'access-key': 'a', private_key: 'pk', credentials: { user: 'u' },
  nested: { headers: { authorization: 'Basic y' }, list: [{ secret_key: 'z' }] },
  host: '10.0.0.1', port: 443, username: 'admin', empty: '', sidebar: 'kept',
};
const out = redactSecrets(input);
const masked = ['apiKey', 'api-key', 'API_KEY', 'password', 'GAIA_PASSWORD', 'clientSecret', 'token', 'Authorization', 'cookie',
  'X-chkp-sid', 'access-key', 'private_key', 'credentials'];
t.check('redactSecrets masks every credential-looking field', masked.every(k => out[k] === '***'), out);
t.check('redactSecrets masks nested headers and arrays', out.nested.headers.authorization === '***' && out.nested.list[0].secret_key === '***', out.nested);
t.check('redactSecrets keeps non-secret fields', out.host === '10.0.0.1' && out.port === 443 && out.username === 'admin' && out.sidebar === 'kept', out);
t.check('redactSecrets does not modify its input', input.apiKey === 'k1' && input.nested.headers.authorization === 'Basic y', null);
t.check('redactSecrets leaves no secret value in its output', !/k1|k2|k3|Bearer x|Basic y|sid1|"z"/.test(JSON.stringify(out)), out);

t.done();
