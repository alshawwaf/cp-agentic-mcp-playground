// RAG relevance threshold, n8n: the committed n8n/backup/workflows/rag-cp-docs-retriever.json workflow,
// imported into a throwaway n8n (the base image docker/n8n/Dockerfile pins) and executed with `n8n execute`
// against a mock Ollama + Qdrant on 127.0.0.1 (Qdrant score_threshold semantics). No network.
//
//   node n8n_retriever_test.mjs /repo     (inside the n8n image; see run.sh)
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import http from 'node:http';

const REPO = process.argv[2] || '/repo';
const WORK = '/tmp/rag-n8n';
const WF_ID = 'RagCpDocsRetrieverWf1';
const TEST_KEY = 'qdrant-test-key-not-a-secret';
fs.mkdirSync(WORK, { recursive: true });

// ---- the workflow and its Qdrant credential, pointed at the mock --------------------------------
const wf = JSON.parse(fs.readFileSync(`${REPO}/n8n/backup/workflows/rag-cp-docs-retriever.json`, 'utf8'));
if (wf.id !== WF_ID) throw new Error(`unexpected workflow id ${wf.id}`);
const search = wf.nodes.find(n => n.name === 'Search cp_docs (Qdrant)');
const embed = wf.nodes.find(n => n.name === 'Embed query (Ollama)');
const threshold = Number((/"score_threshold"\s*:\s*([0-9.]+)/.exec(search.parameters.jsonBody) || [])[1]);
search.parameters.url = search.parameters.url.replace('http://qdrant:6333', 'http://127.0.0.1:6333');
embed.parameters.url = embed.parameters.url.replace('http://ollama-cpu:11434', 'http://127.0.0.1:11434');
const credRef = search.credentials.qdrantApi;
fs.writeFileSync(`${WORK}/wf.json`, JSON.stringify(wf));
fs.writeFileSync(`${WORK}/cred.json`, JSON.stringify([{
  id: credRef.id, name: credRef.name, type: 'qdrantApi', data: { apiKey: TEST_KEY, qdrantUrl: 'http://127.0.0.1:6333' },
}]));

// ---- mock Ollama (11434) + Qdrant (6333) --------------------------------------------------------
const requests = [];
let mode = 'relevant';
const handler = (req, res) => {
  let body = '';
  req.on('data', c => { body += c; });
  req.on('end', () => {
    let data = {};
    try { data = JSON.parse(body || '{}'); } catch { /* ignore */ }
    requests.push({ url: req.url, apiKey: req.headers['api-key'], data: { ...data, vector: undefined } });
    res.setHeader('Content-Type', 'application/json');
    if (req.url.startsWith('/api/embeddings')) return res.end(JSON.stringify({ embedding: [0.1, 0.2, 0.3] }));
    if (req.url.includes('/points/search')) {
      const scores = mode === 'relevant' ? [0.81, 0.62, 0.44, 0.30] : [0.41, 0.33, 0.2, 0.1];
      let hits = scores.map((s, i) => ({ id: i, score: s, payload: { title: `Title ${i}`, source: `doc${i}.md`, text: `text ${i}` } }));
      if (typeof data.score_threshold === 'number') hits = hits.filter(h => h.score >= data.score_threshold);
      return res.end(JSON.stringify({ result: hits.slice(0, data.limit || 10), status: 'ok', time: 0 }));
    }
    res.statusCode = 404;
    return res.end('{}');
  });
};
const servers = [http.createServer(handler).listen(11434, '127.0.0.1'), http.createServer(handler).listen(6333, '127.0.0.1')];

// ---- n8n ---------------------------------------------------------------------------------------
const env = {
  ...process.env, N8N_USER_FOLDER: `${WORK}/home`, N8N_ENCRYPTION_KEY: 'offline-test-only-key', N8N_DIAGNOSTICS_ENABLED: 'false',
  N8N_VERSION_NOTIFICATIONS_ENABLED: 'false', DB_TYPE: 'sqlite',  // default log level: execute prints its result as info
};
// async spawn: the mock servers in this process must keep answering while n8n runs. Output goes to
// files, not pipes: n8n exits right after printing, which can cut off output written to a pipe.
let runNo = 0;
const n8n = args => new Promise(resolve => {
  runNo += 1;
  const outFile = `${WORK}/n8n-${runNo}.out`, errFile = `${WORK}/n8n-${runNo}.err`;
  const outFd = fs.openSync(outFile, 'w'), errFd = fs.openSync(errFile, 'w');
  const child = spawn('n8n', args, { env, stdio: ['ignore', outFd, errFd] });
  child.on('close', code => {
    fs.closeSync(outFd);
    fs.closeSync(errFd);
    resolve({ code, out: fs.readFileSync(outFile, 'utf8'), err: fs.readFileSync(errFile, 'utf8') });
  });
});
for (const args of [['import:credentials', `--input=${WORK}/cred.json`], ['import:workflow', `--input=${WORK}/wf.json`]]) {
  const r = await n8n(args);
  if (r.code !== 0) { console.log(`FAIL  n8n ${args[0]}: ${(r.err || r.out).slice(-800)}`); process.exit(1); }
}

let fails = 0, total = 0;
const expect = (name, cond, detail) => { total++; if (!cond) fails++; console.log(`${cond ? 'PASS' : 'FAIL'}  ${name}${cond ? '' : '  -> ' + JSON.stringify(detail).slice(0, 600)}`); };
const response = raw => {
  const i = raw.indexOf('{');
  const d = JSON.parse(raw.slice(i));
  const runs = ((d.data || d).resultData || d.resultData).runData;
  return runs['Format hits + sources']?.[0]?.data?.main?.[0]?.[0]?.json?.response ?? '';
};

expect(`the committed workflow sends score_threshold ${threshold} (= RAG_MIN_SCORE 0.5)`, threshold === 0.5, search.parameters.jsonBody);
for (const m of ['relevant', 'irrelevant']) {
  mode = m;
  requests.length = 0;
  const r = await n8n(['execute', `--id=${WF_ID}`, '--rawOutput']);
  let text = '';
  try { text = response(r.out); } catch (e) { text = `unparsed: ${e.message} ${r.out.slice(0, 300)} ${r.err.slice(-300)}`; }
  if (process.env.RAG_DEBUG) console.log(`--- exit ${r.code}\n--- stdout (${r.out.length})\n${r.out.slice(0, 3000)}\n--- stderr\n${r.err.slice(-1500)}`);
  const searchReq = requests.find(x => x.url.includes('/points/search'));
  if (m === 'relevant') {
    expect('execute: the search request carries score_threshold 0.5', searchReq?.data?.score_threshold === 0.5, searchReq?.data);
    expect('execute: the Qdrant credential key is sent as the api-key header', searchReq?.apiKey === TEST_KEY, !!searchReq?.apiKey);
    expect('relevant query: only the 2 snippets above 0.5, with their sources', /^Retrieved 2 snippet\(s\) scoring at least 0\.5/.test(text) && /doc0\.md/.test(text) && !/doc2\.md/.test(text), text.slice(0, 200));
  } else {
    expect('irrelevant query: the plain "no snippet scored at least 0.5" answer', /^No snippet in the 'cp_docs' collection scored at least 0\.5 \(the relevance threshold\)/.test(text), text.slice(0, 200));
  }
}
for (const s of servers) s.close();
console.log(`n8n RAG threshold: ${total - fails}/${total} passed`);
process.exit(fails ? 1 : 0);
