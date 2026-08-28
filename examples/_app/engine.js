// 엔진 워커 — Pyodide 로 typelet.gui 의 실제 HTTP 핸들러를 구동한다.
// 서비스워커가 넘겨준 /api/·/img/ 요청을 glue.py serve() 로 처리해 돌려준다.
"use strict";

const PYODIDE_URL = "https://cdn.jsdelivr.net/pyodide/v0.26.4/full/pyodide.js";
const MODULES = ["__init__.py", "cli.py", "config.py", "erase.py", "fonts.py",
                 "gui.py", "jaguk.py", "ledger.py", "ocr.py", "preview.py",
                 "recompose.py", "render.py", "gui.html"];

let py = null, serveFn = null, ledgerFn = null;
let ready = false;
const queue = [];
let fileCache = null;                        // 읽어온 파일의 브라우저 캐시

function post(msg, transfer) { self.postMessage(msg, transfer || []); }

// 읽어온 파일(원장·글꼴·이미지)은 Cache Storage 에 빌드 버전 키로 저장한다
// — 재방문 시 재다운로드 없음, 새 버전 배포 시 옛 캐시는 자동 삭제.
// (localStorage 는 용량(수 MB)이 작아 이미지 트리를 담을 수 없다.)
async function setupCache(base, version) {
  try {
    const prefix = "jaguk-files:" + base + ":";
    const current = prefix + (version || "dev");
    for (const name of await caches.keys())
      if (name.startsWith(prefix) && name !== current) await caches.delete(name);
    fileCache = await caches.open(current);
    const metaUrl = new URL(base + "__cache_meta__", self.location.href).href;
    if (!(await fileCache.match(metaUrl)))
      await fileCache.put(metaUrl, new Response(JSON.stringify(
        {version: version || "dev", cachedAt: new Date().toISOString()})));
  } catch (e) { fileCache = null; /* 캐시 불가 환경 — 매번 받는다 */ }
}

async function fetchBin(url, opts) {
  const cacheable = fileCache && !(opts && opts.noStore);
  if (cacheable) {
    const hit = await fileCache.match(url);
    if (hit) return new Uint8Array(await hit.arrayBuffer());
  }
  const r = await fetch(url, opts && opts.revalidate ? {cache: "no-cache"} : undefined);
  if (!r.ok) throw new Error("로드 실패: " + url);
  const buf = await r.arrayBuffer();
  if (cacheable) {
    try { await fileCache.put(url, new Response(buf.slice(0))); }
    catch (e) { /* 저장 실패(용량 등) — 무시 */ }
  }
  return new Uint8Array(buf);
}

function writeFile(path, bytes) {
  py.FS.mkdirTree(path.slice(0, path.lastIndexOf("/")));
  py.FS.writeFile(path, bytes);
}

async function init(msg) {
  const base = msg.base;                       // 예제 루트 절대경로 ('/kitae/' 등)
  const manifest = msg.manifest;
  await setupCache(base, manifest && manifest.version);
  post({type: "status", text: "Pyodide 로딩…"});
  importScripts(PYODIDE_URL);
  py = await loadPyodide();
  post({type: "status", text: "Pillow/NumPy 로딩…"});
  await py.loadPackage(["pillow", "numpy"]);

  post({type: "status", text: "typelet 소스 로딩…"});
  let srcBase = null;
  for (const b of [base + "../_typelet/", base + "../../typelet/"]) {
    try {
      const r = await fetch(b + "__init__.py");
      if (r.ok) { srcBase = b; break; }
    } catch (e) { /* 다음 후보 */ }
  }
  if (!srcBase) throw new Error("typelet 소스를 찾을 수 없습니다");
  for (const m of MODULES) {
    // typelet 소스는 캐시하지 않고 재검증한다 — 커밋만으로도 바뀐다
    try { writeFile("/lib/typelet/" + m,
                    await fetchBin(srcBase + m, {noStore: true, revalidate: true})); }
    catch (e) { /* 선택 모듈이 없어도 계속 (예: ocr) */ }
  }

  post({type: "status", text: "원장·글꼴 로딩…"});
  writeFile("/proj/typelet.config.json",
            await fetchBin(base + "project/typelet.config.json"));
  if (msg.saved) {                             // localStorage 미러 복원
    py.FS.mkdirTree("/proj");
    py.FS.writeFile("/proj/lettering.json", msg.saved);
  } else {
    writeFile("/proj/lettering.json",
              await fetchBin(base + "project/lettering.json"));
  }
  for (const f of manifest.fonts || [])
    writeFile("/proj/fonts/" + f, await fetchBin(base + "fonts/" + f));

  // 이미지 전체를 FS 에 미리 싣는다 — 로컬 프로젝트 트리와 같은 상태
  const jobs = new Map();                      // fs 경로 → url
  for (const f of manifest.files || []) {
    jobs.set("/proj/originals/" + f.rel, base + "assets/original/" + f.rel);
    if (f.canvas)
      jobs.set("/proj/erased/" + f.canvas, base + "assets/erased/" + f.canvas);
    if (f.underlay)
      jobs.set("/proj/originals/" + f.underlay,
               base + "assets/original/" + f.underlay);
    for (const layer of f.posts || [])
      jobs.set("/proj/erased/" + layer, base + "assets/erased/" + layer);
  }
  const entries = [...jobs];
  let done = 0;
  const workers = Array.from({length: 16}, async () => {
    while (entries.length) {
      const [fsPath, url] = entries.pop();
      writeFile(fsPath, await fetchBin(url));
      done += 1;
      if (done % 40 === 0 || done === jobs.size)
        post({type: "progress", done, total: jobs.size});
    }
  });
  await Promise.all(workers);

  post({type: "status", text: "jaguk GUI 핸들러 기동…"});
  const glue = await (await fetch(base + "../_app/glue.py",
                                  {cache: "no-cache"})).text();
  py.runPython(glue);
  serveFn = py.globals.get("serve");
  ledgerFn = py.globals.get("ledger_text");
  ready = true;
  post({type: "ready"});
  while (queue.length) handleRequest(queue.shift());
}

function handleRequest(m) {
  let status = 500, ctype = "application/json", body;
  try {
    const res = serveFn(m.method, m.url,
                        m.body ? new Uint8Array(m.body) : null);
    const js = res.toJs();
    res.destroy();
    status = js[0];
    ctype = js[1];
    body = new Uint8Array(js[2]);              // 뷰 복사 (destroy 대비)
  } catch (e) {
    body = new TextEncoder().encode(JSON.stringify({error: String(e)}));
  }
  post({type: "response", id: m.id, status, ctype, body}, [body.buffer]);
  if (m.method === "POST" && status === 200 && ledgerFn) {
    try { post({type: "ledger", text: ledgerFn()}); } catch (e) { /* 무시 */ }
  }
}

self.onmessage = ev => {
  const m = ev.data;
  if (m.type === "init") {
    init(m).catch(e => post({type: "fatal", text: String(e && e.message || e)}));
  } else if (m.type === "request") {
    if (ready) handleRequest(m);
    else queue.push(m);
  }
};
