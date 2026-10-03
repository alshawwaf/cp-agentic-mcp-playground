// Spark Management starts without credentials (LAB PATCH D031) and reports the missing settings per tool
// call, instead of exiting at start-up (which made the sidecar crash-loop on every default lab).
import { initialize, callTool, startServer } from './lib/mcp-client.mjs';
import { entry, suite } from './lib/env.mjs';

const t = suite('Spark Management without credentials');
const s = await startServer(entry('spark-management'), 4404, {});
t.check('the server starts and answers /health with no SPARK_MGMT_* settings', true, null);
const { sessionId, status } = await initialize(s.url);
t.check('initialize works', status === 200 && !!sessionId, status);
const r = await callTool(s.url, sessionId, 'show_gateway', { gatewayName: 'gw1' });
t.check('a tool call reports the missing setting', /Client ID is required/.test(r.text + JSON.stringify(r.body)), (r.text || JSON.stringify(r.body)).slice(0, 200));
s.stop();
t.done();
