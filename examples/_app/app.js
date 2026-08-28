// jaguk 예제 공용 앱 — UI 만 담당한다. 주입(렌더)은 별도 구현이 아니라
// 레포의 typelet 패키지를 Pyodide(wasm)로 브라우저에서 그대로 실행한다
// (_app/glue.py). 파이프라인·jaguk GUI 와 같은 코드 경로 = 같은 산출.
"use strict";

const SHELL = window.DEMO_SHELL;   // {title, sub, store}
const DATA = window.DEMO_DATA;     // {files, groups, loose, fonts}
const byRel = new Map(DATA.files.map(f => [f.rel, f]));

const PYODIDE_URL = "https://cdn.jsdelivr.net/pyodide/v0.26.4/full/pyodide.js";
const TYPELET_MODULES = ["__init__.py", "config.py", "ledger.py",
                         "render.py", "fonts.py", "recompose.py"];
// Pages 아티팩트(사이트 루트 = examples/)에는 워크플로가 typelet 을
// _typelet/ 로 실어 준다. 레포 루트를 정적 서빙하면 ../../typelet 이 소스다.
const TYPELET_BASES = ["../_typelet/", "../../typelet/"];

const STORE = SHELL.store + "-ko";      // 번역 수정 — localStorage 전용
const STATE = SHELL.store + "-state";   // 화면 상태
const BGS = ["#3c5a82", "#14161a", "#ffffff", "checker"];
const VIEWS = [["original", "원본"], ["erased", "erased"], ["injected", "주입 (라이브)"]];

let fileRel = DATA.files[0].rel;
let view = "injected", bgIdx = 3, zoomMode = "fit", showBoxes = false;
let edits = {};
try { edits = JSON.parse(localStorage.getItem(STORE) || "{}"); } catch (e) {}
try {
  const s = JSON.parse(localStorage.getItem(STATE) || "null");
  if (s) {
    if (byRel.has(s.rel)) fileRel = s.rel;
    if (["original", "erased", "injected"].includes(s.view)) view = s.view;
    if (Number.isInteger(s.bgIdx) && s.bgIdx >= 0 && s.bgIdx <= 3) bgIdx = s.bgIdx;
    if (["fit", "1", "2", "4"].includes(s.zoom)) zoomMode = s.zoom;
    if (typeof s.boxes === "boolean") showBoxes = s.boxes;
  }
} catch (e) {}
{ // URL 로 초기 상태 지정 — ?file=soz_012&view=original&bg=0&zoom=2&boxes=1
  const q = new URLSearchParams(location.search);
  const f = q.get("file");
  if (f !== null) {
    const hit = DATA.files.find(x => x.rel.includes(f));
    if (hit) fileRel = hit.rel;
  }
  if (["original", "erased", "injected"].includes(q.get("view"))) view = q.get("view");
  const bg = parseInt(q.get("bg"), 10);
  if (Number.isInteger(bg) && bg >= 0 && bg <= 3) bgIdx = bg;
  if (["fit", "1", "2", "4"].includes(q.get("zoom"))) zoomMode = q.get("zoom");
  if (q.get("boxes") === "1") showBoxes = true;
}

function saveEdits() {
  try { localStorage.setItem(STORE, JSON.stringify(edits)); } catch (e) {}
}
function saveState() {
  try {
    localStorage.setItem(STATE, JSON.stringify(
      {rel: fileRel, view, bgIdx, zoom: zoomMode, boxes: showBoxes}));
  } catch (e) {}
}
function koOf(row) { return edits[row.box_id] !== undefined ? edits[row.box_id] : row.ko; }

// ---- 골격 ----
document.body.insertAdjacentHTML("afterbegin", `
<header>
  <h1>${SHELL.title}<small>${SHELL.sub || "브라우저에서 typelet 그대로 주입"}</small></h1>
  <div class="group" id="viewButtons"><span class="lab">보기</span></div>
  <div class="group" id="bgButtons"><span class="lab">배경</span></div>
  <div class="group"><span class="lab">배율</span>
    <select id="zoomSel">
      <option value="fit">맞춤</option><option value="1">1x</option>
      <option value="2">2x</option><option value="4">4x</option>
    </select>
  </div>
  <div class="group">
    <button id="boxBtn">상자</button>
    <button id="resetBtn" title="이 브라우저에 저장된 수정(localStorage)을 지운다">수정 초기화</button>
  </div>
  <span id="engineStat"></span>
</header>
<main>
  <div id="files">
    <div class="head">
      <input type="search" id="filter" placeholder="그룹/파일 필터…">
      <div class="count" id="fileCount"></div>
    </div>
    <div id="fileList"></div>
  </div>
  <div id="stage-wrap"><div id="stage"></div></div>
  <div id="side">
    <div class="head" id="sideHead"></div>
    <div id="rows"></div>
    <div id="note">
      번역을 고치면 즉시 다시 렌더링됩니다. 수정은 이 브라우저의
      <b>localStorage에만</b> 저장됩니다 — 서버·원장에는 아무것도 쓰지 않습니다.<br><br>
      주입 렌더는 별도 구현이 아니라 <a
      href="https://github.com/badatbit/jaguk">typelet</a>
      (render.py — 파이프라인·jaguk GUI 와 같은 코드)을 Pyodide/WebAssembly 로
      브라우저에서 그대로 실행한 결과입니다. 첫 주입 보기에서 엔진(수 MB)을
      한 번 내려받습니다.
    </div>
  </div>
</main>`);

const stage = document.getElementById("stage");
const engineStat = document.getElementById("engineStat");
function setStat(text, isErr) {
  engineStat.textContent = text;
  engineStat.classList.toggle("err", !!isErr);
}

// ---- 이미지 로드 (원본/erased 보기용) ----
const imgCache = {};
function loadImage(src) {
  if (imgCache[src]) return imgCache[src];
  imgCache[src] = new Promise((ok, no) => {
    const im = new Image();
    im.onload = () => ok(im);
    im.onerror = () => no(new Error("이미지 로드 실패: " + src));
    im.src = src;
  });
  return imgCache[src];
}

// ---- 엔진 — typelet(wasm) ----
function loadScript(src) {
  return new Promise((ok, no) => {
    const s = document.createElement("script");
    s.src = src;
    s.onload = ok;
    s.onerror = () => no(new Error("스크립트 로드 실패: " + src));
    document.head.appendChild(s);
  });
}

const Engine = {
  state: "idle", py: null, renderFn: null, written: new Set(), promise: null,

  async ensure() {
    if (this.state === "ready") return;
    if (!this.promise) {
      this.promise = this._init().then(
        () => { this.state = "ready"; setStat(""); },
        e => { this.state = "error"; this.promise = null; throw e; });
      this.state = "loading";
    }
    await this.promise;
  },

  async _init() {
    setStat("엔진 로딩 — Pyodide…");
    await loadScript(PYODIDE_URL);
    this.py = await loadPyodide();
    setStat("엔진 로딩 — Pillow/NumPy…");
    await this.py.loadPackage(["pillow", "numpy"]);
    setStat("엔진 로딩 — typelet 소스…");
    let base = null;
    for (const b of TYPELET_BASES) {
      try {
        const r = await fetch(b + "__init__.py");
        if (r.ok) { base = b; break; }
      } catch (e) { /* 다음 후보 */ }
    }
    if (!base) throw new Error("typelet 소스를 찾을 수 없습니다");
    this.py.FS.mkdirTree("/lib/typelet");
    for (const m of TYPELET_MODULES)
      await this.fetchTo(base + m, "/lib/typelet/" + m);
    setStat("엔진 로딩 — 원장·글꼴…");
    await this.fetchTo("project/typelet.config.json", "/proj/typelet.config.json");
    await this.fetchTo("project/lettering.json", "/proj/lettering.json");
    for (const f of DATA.fonts)
      await this.fetchTo("fonts/" + f, "/proj/fonts/" + f);
    const glue = await (await fetch("../_app/glue.py")).text();
    this.py.runPython(glue);
    this.renderFn = this.py.globals.get("render_png");
  },

  async fetchTo(url, fsPath) {
    if (this.written.has(fsPath)) return;
    const r = await fetch(url);
    if (!r.ok) throw new Error("로드 실패: " + url);
    this.py.FS.mkdirTree(fsPath.slice(0, fsPath.lastIndexOf("/")));
    this.py.FS.writeFile(fsPath, new Uint8Array(await r.arrayBuffer()));
    this.written.add(fsPath);
  },

  async ensureAssets(file) {
    const jobs = [["assets/original/" + file.rel, "/proj/originals/" + file.rel]];
    if (file.canvas)
      jobs.push(["assets/erased/" + file.canvas, "/proj/erased/" + file.canvas]);
    if (file.underlay)
      jobs.push(["assets/original/" + file.underlay,
                 "/proj/originals/" + file.underlay]);
    for (const layer of file.posts || [])
      jobs.push(["assets/erased/" + layer, "/proj/erased/" + layer]);
    for (const [u, p] of jobs) await this.fetchTo(u, p);
  },

  async render(file) {
    await this.ensure();
    await this.ensureAssets(file);
    const res = this.renderFn(file.rel, JSON.stringify(edits));
    const bytes = new Uint8Array(res.toJs());
    if (res.destroy) res.destroy();
    return bytes;
  },
};

// ---- 화면 ----
let renderSeq = 0;
let lastBlobUrl = null;

async function plainView(file, src) {
  // 밑판(overlay 그룹 base) 위에 레이어 한 장 — 정적 보기 (엔진 불필요)
  const im = src ? await loadImage(src) : null;
  const un = file.underlay
    ? await loadImage("assets/original/" + file.underlay) : null;
  if (!un && im) return {el: im.cloneNode(), w: im.width, h: im.height};
  let w, h;
  if (un && im) { w = Math.max(un.width, im.width); h = Math.max(un.height, im.height); }
  else if (un) { w = un.width; h = un.height; }
  else {
    const o = await loadImage("assets/original/" + file.rel);
    w = o.width; h = o.height;
  }
  const cv = document.createElement("canvas");
  cv.width = w; cv.height = h;
  const c = cv.getContext("2d");
  if (un) c.drawImage(un, 0, 0);
  if (im) c.drawImage(im, 0, 0);
  return {el: cv, w, h};
}

async function renderStage() {
  const seq = ++renderSeq;
  const file = byRel.get(fileRel);
  let el, w, h;
  try {
    if (view === "original") {
      ({el, w, h} = await plainView(file, "assets/original/" + file.rel));
    } else if (view === "erased") {
      ({el, w, h} = await plainView(
        file, file.canvas ? "assets/erased/" + file.canvas : null));
    } else {
      if (Engine.state !== "ready")
        showMessage("같은 typelet 엔진(wasm)을 로딩하는 중…");
      else setStat("렌더 중…");
      const bytes = await Engine.render(file);
      if (seq !== renderSeq) return;
      setStat("");
      const url = URL.createObjectURL(new Blob([bytes], {type: "image/png"}));
      el = await new Promise((ok, no) => {
        const im = new Image();
        im.onload = () => ok(im);
        im.onerror = () => no(new Error("렌더 이미지 표시 실패"));
        im.src = url;
      });
      if (lastBlobUrl) URL.revokeObjectURL(lastBlobUrl);
      lastBlobUrl = url;
      w = el.width; h = el.height;
    }
  } catch (e) {
    if (seq !== renderSeq) return;
    setStat("", false);
    showMessage("렌더 실패: " + (e.message || e), true);
    return;
  }
  if (seq !== renderSeq) return;
  stage.textContent = "";
  const bg = BGS[bgIdx];
  stage.className = bg === "checker" ? "checker" : "";
  stage.style.background = bg === "checker" ? "" : bg;
  stage.appendChild(el);
  if (showBoxes) stage.appendChild(drawBoxesOverlay(file, w, h));
  applyZoom(w);
}

function showMessage(text, isErr) {
  stage.textContent = "";
  stage.className = "";
  stage.style.background = "";
  const div = document.createElement("div");
  div.className = "msg";
  if (isErr) div.style.color = "#e07070";
  div.textContent = text;
  stage.appendChild(div);
}

function drawBoxesOverlay(file, w, h) {
  const cv = document.createElement("canvas");
  cv.width = w; cv.height = h;
  cv.className = "layer";
  cv.style.pointerEvents = "none";
  const c = cv.getContext("2d");
  c.lineWidth = 1;
  for (const row of file.rows) {
    if (row.crop) {
      c.strokeStyle = "#d05ce3";
      c.strokeRect(row.crop[0] + .5, row.crop[1] + .5, row.crop[2] - 1, row.crop[3] - 1);
    }
    c.strokeStyle = "#39d98a";
    c.strokeRect(row.box[0] + .5, row.box[1] + .5, row.box[2] - 1, row.box[3] - 1);
    if (row.flow) {
      c.strokeStyle = "#e8b73c";
      for (const b of row.flow)
        c.strokeRect(b[0] + .5, b[1] + .5, b[2] - 1, b[3] - 1);
    }
  }
  return cv;
}

function applyZoom(naturalW) {
  const wrap = document.getElementById("stage-wrap");
  const els = stage.querySelectorAll("canvas, img");
  const scale = zoomMode === "fit"
    ? Math.min(1, (wrap.clientWidth - 40) / naturalW) : +zoomMode;
  for (const el of els) {
    el.style.width = Math.round(naturalW * scale) + "px";
    el.style.imageRendering = scale >= 2 ? "pixelated" : "";
  }
}

let renderTimer = null;
function scheduleRender() {
  clearTimeout(renderTimer);
  renderTimer = setTimeout(renderStage, 250);
}

// ---- 왼쪽: 규칙 그룹 / 파일 목록 ----
const openKeys = new Set();
function fileEntries() {
  // [{key, label, files:[rel…]}] — 규칙 그룹 우선, 그룹 밖은 디렉토리로
  const out = [];
  for (const g of DATA.groups || []) {
    const n = g.files.reduce((s, f) => s + byRel.get(f).rows.length, 0);
    out.push({key: "g:" + g.name, label: `${g.name} (${n} · ${g.mode})`,
              files: g.files});
  }
  const dirs = new Map();
  for (const rel of DATA.loose || []) {
    const dir = rel.includes("/") ? rel.slice(0, rel.lastIndexOf("/")) : ".";
    if (!dirs.has(dir)) dirs.set(dir, []);
    dirs.get(dir).push(rel);
  }
  for (const [dir, rels] of dirs)
    out.push({key: "d:" + dir, label: `${dir} (${rels.length})`, files: rels});
  return out;
}

function renderFileList() {
  const filter = document.getElementById("filter").value.trim().toLowerCase();
  const list = document.getElementById("fileList");
  list.textContent = "";
  let shown = 0;
  for (const entry of fileEntries()) {
    const files = filter
      ? (entry.label.toLowerCase().includes(filter)
         ? entry.files
         : entry.files.filter(r => r.toLowerCase().includes(filter)))
      : entry.files;
    if (!files.length) continue;
    shown += files.length;
    const det = document.createElement("details");
    det.open = !!filter || files.includes(fileRel) || openKeys.has(entry.key);
    const sum = document.createElement("summary");
    sum.textContent = entry.label;
    det.appendChild(sum);
    det.addEventListener("toggle", () => {
      if (det.open) openKeys.add(entry.key); else openKeys.delete(entry.key);
    });
    for (const rel of files) {
      const f = byRel.get(rel);
      const b = document.createElement("button");
      b.className = "item" + (rel === fileRel ? " on" : "");
      b.textContent = rel.slice(rel.lastIndexOf("/") + 1);
      b.title = rel;
      const n = document.createElement("span");
      n.className = "n";
      n.textContent = f.rows.length;
      b.appendChild(n);
      b.addEventListener("click", () => {
        fileRel = rel;
        saveState();
        renderFileList(); renderRowsPanel(); renderStage();
      });
      det.appendChild(b);
    }
    list.appendChild(det);
  }
  const total = DATA.files.reduce((s, f) => s + f.rows.length, 0);
  document.getElementById("fileCount").textContent = filter
    ? `${shown}개 일치`
    : `${DATA.files.length}개 파일 · ${total}개 행`;
}

// ---- 오른쪽: 상자 목록 (id · 원문 · 번역) ----
function renderRowsPanel() {
  const file = byRel.get(fileRel);
  document.getElementById("sideHead").textContent =
    `${file.rel} — ${file.rows.length}개 행`;
  const holder = document.getElementById("rows");
  holder.textContent = "";
  const table = document.createElement("table");
  table.innerHTML =
    "<thead><tr><th>id</th><th>원문 jp</th><th>번역 ko</th></tr></thead>";
  const tbody = document.createElement("tbody");
  for (const row of file.rows) {
    const tr = document.createElement("tr");
    if (edits[row.box_id] !== undefined) tr.className = "edited";
    const td1 = document.createElement("td");
    td1.className = "id"; td1.textContent = row.box_id;
    const td2 = document.createElement("td");
    td2.className = "jp"; td2.textContent = row.jp || "—";
    const td3 = document.createElement("td");
    td3.className = "ko";
    const input = document.createElement("input");
    input.value = koOf(row);
    input.addEventListener("input", () => {
      if (input.value === row.ko) delete edits[row.box_id];
      else edits[row.box_id] = input.value;
      tr.classList.toggle("edited", edits[row.box_id] !== undefined);
      saveEdits();                           // localStorage 전용 — 서버 기록 없음
      if (view !== "injected") { view = "injected"; renderViewButtons(); }
      scheduleRender();
    });
    td3.appendChild(input);
    tr.append(td1, td2, td3);
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  holder.appendChild(table);
}

// ---- 헤더 컨트롤 ----
function makeButtons(holder, items, isOn, onClick) {
  for (const el of [...holder.querySelectorAll("button")]) el.remove();
  items.forEach((it, i) => {
    const b = document.createElement("button");
    if (it.swatch) {
      b.className = "swatch" + (isOn(i) ? " on" : "");
      if (it.value === "checker") b.classList.add("checker");
      else b.style.background = it.value;
      b.title = it.label;
    } else {
      b.textContent = it.label;
      if (isOn(i)) b.classList.add("on");
    }
    b.addEventListener("click", () => onClick(i));
    holder.appendChild(b);
  });
}
function renderViewButtons() {
  makeButtons(document.getElementById("viewButtons"),
    VIEWS.map(v => ({label: v[1]})), i => VIEWS[i][0] === view,
    i => { view = VIEWS[i][0]; saveState(); renderViewButtons(); renderStage(); });
}
function renderBgButtons() {
  makeButtons(document.getElementById("bgButtons"),
    BGS.map(v => ({swatch: true, value: v, label: v})), i => i === bgIdx,
    i => { bgIdx = i; saveState(); renderBgButtons(); renderStage(); });
}
document.getElementById("zoomSel").addEventListener("change", e => {
  zoomMode = e.target.value; saveState(); renderStage();
});
document.getElementById("boxBtn").addEventListener("click", e => {
  showBoxes = !showBoxes;
  e.target.classList.toggle("on", showBoxes);
  saveState(); renderStage();
});
document.getElementById("resetBtn").addEventListener("click", () => {
  edits = {};
  try { localStorage.removeItem(STORE); } catch (e) {}
  renderRowsPanel(); renderStage();
});
document.getElementById("filter").addEventListener("input", renderFileList);
window.addEventListener("resize", () => { if (zoomMode === "fit") renderStage(); });

// ---- 시작 ----
document.getElementById("boxBtn").classList.toggle("on", showBoxes);
document.getElementById("zoomSel").value = zoomMode;
renderViewButtons(); renderBgButtons();
renderFileList(); renderRowsPanel();
renderStage();
