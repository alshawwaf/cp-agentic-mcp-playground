// Child process for tls.test.mjs: one OnPremAPIClient call against the fake management API.
// argv: <host> <port>. Prints one JSON line: { ok, status, error }. The API key is a generated test value.
import { WORK } from './env.mjs';

const [host, port] = process.argv.slice(2);
const infra = await import(`${WORK}/packages/infra/dist/index.js`);

const client = new infra.OnPremAPIClient(process.env.TEST_SECRET, host, port);
client.debug = process.env.CLIENT_DEBUG === '1';
try {
  const resp = await client.callApi('POST', 'show-hosts', {});
  console.log(JSON.stringify({ ok: resp.status === 200, status: resp.status, error: resp.response?.message || null }));
} catch (e) {
  console.log(JSON.stringify({ ok: false, status: null, error: e.message }));
}
