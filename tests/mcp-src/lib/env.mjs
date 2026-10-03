// Where the suites find the build and the test certificates (set by run-suites.sh in the container).
//   MCP_SRC_DIR    the built mcp-src tree (default /src/work, mounted read-only)
//   TEST_CERTS_DIR certificates generated for this run by lib/certs.mjs (default /tmp/certs)
const dir = d => (d.endsWith('/') ? d : d + '/');
export const WORK = (process.env.MCP_SRC_DIR || '/src/work').replace(/\/$/, '');
export const CERTS = dir(process.env.TEST_CERTS_DIR || '/tmp/certs');
export const entry = pkg => `${WORK}/packages/${pkg}/dist/index.js`;

/** Collects checks and prints the PASS / FAIL table; call done() last (sets the exit code). */
export function suite(title) {
  const results = [];
  return {
    check(name, cond, detail) { results.push({ name, pass: !!cond, detail }); },
    info(name) { results.push({ name, pass: true, info: true }); },
    done() {
      let failed = 0;
      console.log(`== ${title}`);
      for (const x of results) {
        if (!x.pass) failed++;
        const tag = x.info ? 'INFO' : x.pass ? 'PASS' : 'FAIL';
        console.log(`${tag}  ${x.name}${x.pass ? '' : '  ' + JSON.stringify(x.detail)}`);
      }
      const n = results.filter(x => !x.info).length;
      console.log(`${title}: ${n - failed}/${n} passed (node ${process.version})`);
      process.exit(failed ? 1 : 0);
    },
  };
}
