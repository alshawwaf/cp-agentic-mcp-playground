// DELETE /mcp on the real Management MCP server: the session closes, the process survives, and the
// former onclose recursion (RangeError: Maximum call stack size exceeded) does not come back.
import { initialize, del, health, startServer } from './lib/mcp-client.mjs';
import { entry, suite } from './lib/env.mjs';

const t = suite('DELETE /mcp (Management MCP server)');
const s = await startServer(entry('management'), 4602, {});
const { sessionId } = await initialize(s.url);
const n = (await health(s.base)).activeSessions;
const started = Date.now();
let status;
try { status = await del(s.url, sessionId); } catch (e) { status = 'error ' + e.message; }
const ms = Date.now() - started;
let h;
try { h = await health(s.base); } catch { h = null; }
t.check('DELETE answers 200', status === 200, status);
t.check('DELETE returns promptly (< 5 s)', ms < 5000, ms);
t.check('the server is alive afterwards', h?.status === 'ok', h);
t.check('the session is gone', h?.activeSessions === n - 1, { before: n, after: h?.activeSessions });
t.check('no stack overflow in the log', !/Maximum call stack|RangeError/.test(s.logs.text), s.logs.text.slice(-400));
const again = await del(s.url, sessionId);
t.check('a second DELETE of the same session -> 404', again === 404, again);
s.stop();
t.done();
