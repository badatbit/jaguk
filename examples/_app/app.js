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
// 구 형식({box_id: "ko"}) → {box_id: {ko}} 로 이행 — 상자 수정과 공존
for (const k of Object.keys(edits))
  if (typeof edits[k] === "string") edits[k] = {ko: edits[k]};
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
function koOf(row) {
  const e = edits[row.box_id];
  return e && e.ko !== undefined ? e.ko : row.ko;
}
function effBox(row) {
  const e = edits[row.box_id];
  return e && e.box ? e.box : row.box;
}
function effCrop(row) {
  const e = edits[row.box_id];
  return e && e.crop ? e.crop : (row.crop || null);
}
function isEdited(id) { return edits[id] !== undefined; }
function editEntry(id) { return edits[id] || (edits[id] = {}); }
function pruneEntry(id) {
  const e = edits[id];
  if (e && e.ko === undefined && !e.box && !e.crop) delete edits[id];
}
function setRect(file, id, kind, rect) {
  // run 멤버는 text 상자를 공유한다 — 같이 옮겨야 렌더 검증이 통과한다
  const row = file.rows.find(r => r.box_id === id);
  const targets = (kind === "box" && row && row.run)
    ? file.rows.filter(r => r.run === row.run) : [row];
  for (const t of targets) {
    if (!t) continue;
    const orig = kind === "box" ? t.box : t.crop;
    const e = editEntry(t.box_id);
    if (orig && rect.every((v, i) => v === orig[i])) delete e[kind];
    else e[kind] = rect.slice();
    pruneEntry(t.box_id);
  }
  saveEdits();
}

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
      번역을 고치면 즉시 다시 렌더링됩니다. <b>상자</b>를 켜면 스테이지에서
      상자를 드래그해 옮기고 핸들로 크기를 바꿀 수 있습니다 (초록 = text,
      자주 = crop; 행의 id/원문을 클릭해도 선택). 수정은 이 브라우저의
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

// ---- 상자 편집 오버레이 — 드래그로 이동, 핸들로 리사이즈 ----
// 수정은 edits[box_id].box/.crop 에 얹혀 localStorage 로만 저장되고,
// 렌더는 같은 typelet 엔진이 수정된 좌표로 다시 그린다.
let selected = null;             // {id, kind: "box"|"crop"}
let overlayEl = null;
let drag = null;
const HANDLES = [[0, 0], [.5, 0], [1, 0], [1, .5], [1, 1], [.5, 1], [0, 1], [0, .5]];
const HANDLE_CURSORS = ["nwse-resize", "ns-resize", "nesw-resize", "ew-resize",
                        "nwse-resize", "ns-resize", "nesw-resize", "ew-resize"];

function selectedRect(file) {
  if (!selected) return null;
  const row = file.rows.find(r => r.box_id === selected.id);
  if (!row) return null;
  return selected.kind === "crop" ? effCrop(row) : effBox(row);
}

function redrawOverlay(file) {
  if (!overlayEl) return;
  const c = overlayEl.getContext("2d");
  c.clearRect(0, 0, overlayEl.width, overlayEl.height);
  c.lineWidth = 1;
  for (const row of file.rows) {
    const crop = effCrop(row);
    if (crop) {
      c.strokeStyle = "#d05ce3";
      c.strokeRect(crop[0] + .5, crop[1] + .5, crop[2] - 1, crop[3] - 1);
    }
    const box = effBox(row);
    c.strokeStyle = "#39d98a";
    c.strokeRect(box[0] + .5, box[1] + .5, box[2] - 1, box[3] - 1);
    if (row.flow) {
      c.strokeStyle = "#e8b73c";
      for (const b of row.flow)
        c.strokeRect(b[0] + .5, b[1] + .5, b[2] - 1, b[3] - 1);
    }
  }
  const rect = selectedRect(file);
  if (rect) {
    const scale = overlayScale();
    c.lineWidth = Math.max(1, 2 / scale);
    c.strokeStyle = selected.kind === "crop" ? "#d05ce3" : "#39d98a";
    c.strokeRect(rect[0] + .5, rect[1] + .5, rect[2] - 1, rect[3] - 1);
    const hs = Math.max(3, 6 / scale);
    c.fillStyle = "#ffffff";
    c.strokeStyle = "#14161a";
    c.lineWidth = Math.max(1, 1 / scale);
    for (const [fx, fy] of HANDLES) {
      const hx = rect[0] + fx * rect[2], hy = rect[1] + fy * rect[3];
      c.fillRect(hx - hs / 2, hy - hs / 2, hs, hs);
      c.strokeRect(hx - hs / 2, hy - hs / 2, hs, hs);
    }
  }
}

function overlayScale() {
  if (!overlayEl) return 1;
  const r = overlayEl.getBoundingClientRect();
  return r.width ? r.width / overlayEl.width : 1;
}
function toImg(ev) {
  const r = overlayEl.getBoundingClientRect();
  const s = overlayScale();
  return [(ev.clientX - r.left) / s, (ev.clientY - r.top) / s];
}
function hitHandle(file, pt) {
  const rect = selectedRect(file);
  if (!rect) return -1;
  const tol = Math.max(4, 7 / overlayScale());
  for (let i = 0; i < HANDLES.length; i++) {
    const hx = rect[0] + HANDLES[i][0] * rect[2];
    const hy = rect[1] + HANDLES[i][1] * rect[3];
    if (Math.abs(pt[0] - hx) <= tol && Math.abs(pt[1] - hy) <= tol) return i;
  }
  return -1;
}
function hitBox(file, pt) {
  const tol = Math.max(2, 4 / overlayScale());
  let best = null;
  for (const row of file.rows) {
    for (const [kind, rect] of [["crop", effCrop(row)], ["box", effBox(row)]]) {
      if (!rect) continue;
      if (pt[0] >= rect[0] - tol && pt[0] <= rect[0] + rect[2] + tol &&
          pt[1] >= rect[1] - tol && pt[1] <= rect[1] + rect[3] + tol) {
        const area = rect[2] * rect[3];
        if (!best || area < best.area)
          best = {id: row.box_id, kind, rect, area};
      }
    }
  }
  return best;
}

function resizeRect(orig, handle, dx, dy) {
  let [x, y, w, h] = orig;
  const [fx, fy] = HANDLES[handle];
  if (fx === 0) { x += dx; w -= dx; }
  else if (fx === 1) { w += dx; }
  if (fy === 0) { y += dy; h -= dy; }
  else if (fy === 1) { h += dy; }
  if (w < 1) { if (fx === 0) x += w - 1; w = 1; }
  if (h < 1) { if (fy === 0) y += h - 1; h = 1; }
  return [x, y, w, h].map(Math.round);
}

function attachOverlayEvents(file) {
  const cv = overlayEl;
  cv.style.pointerEvents = "auto";
  cv.addEventListener("pointerdown", ev => {
    ev.preventDefault();
    const pt = toImg(ev);
    const hi = hitHandle(file, pt);
    if (hi >= 0) {
      drag = {mode: hi, start: pt, orig: selectedRect(file).slice()};
    } else {
      const hit = hitBox(file, pt);
      if (hit) {
        selected = {id: hit.id, kind: hit.kind};
        drag = {mode: "move", start: pt,
                orig: selectedRect(file).slice()};
        syncTableSel();
      } else {
        selected = null;
      }
    }
    redrawOverlay(file);
    cv.setPointerCapture(ev.pointerId);
  });
  cv.addEventListener("pointermove", ev => {
    const pt = toImg(ev);
    if (drag && selected) {
      const dx = pt[0] - drag.start[0], dy = pt[1] - drag.start[1];
      const rect = drag.mode === "move"
        ? [Math.round(drag.orig[0] + dx), Math.round(drag.orig[1] + dy),
           drag.orig[2], drag.orig[3]]
        : resizeRect(drag.orig, drag.mode, Math.round(dx), Math.round(dy));
      setRect(file, selected.id, selected.kind, rect);
      setStat(`${selected.id} ${selected.kind}: `
              + `${rect[0]},${rect[1]} ${rect[2]}×${rect[3]}`);
      redrawOverlay(file);
      return;
    }
    const hi = hitHandle(file, pt);
    cv.style.cursor = hi >= 0 ? HANDLE_CURSORS[hi]
      : (hitBox(file, pt) ? "move" : "default");
  });
  const finish = () => {
    if (!drag) return;
    drag = null;
    setStat("");
    renderRowsPanel();               // 수정 표시 갱신
    scheduleRender();                // 엔진 재렌더 (주입 보기일 때)
  };
  cv.addEventListener("pointerup", finish);
  cv.addEventListener("pointercancel", finish);
}

function drawBoxesOverlay(file, w, h) {
  const cv = document.createElement("canvas");
  cv.width = w; cv.height = h;
  cv.className = "layer";
  overlayEl = cv;
  redrawOverlay(file);
  attachOverlayEvents(file);
  return cv;
}

window.addEventListener("keydown", ev => {
  if (ev.key === "Escape" && selected) {
    selected = null;
    if (overlayEl) redrawOverlay(byRel.get(fileRel));
  }
});

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
// jaguk GUI 규약: cat 있는 그룹은 카테고리 폴더로 접고(원장 rules 등장
// 순서 유지), cat 없는 그룹은 폴더 없이 최상위에 먼저 온다.
const openKeys = new Set();
function sidebarTree() {
  const bare = [], folders = [], byCat = new Map();
  for (const g of DATA.groups || []) {
    const n = g.files.reduce((s, f) => s + byRel.get(f).rows.length, 0);
    const entry = {kind: "group", key: "g:" + g.name,
                   label: `${g.name} (${n} · ${g.mode})`, files: g.files};
    if (g.cat) {
      if (!byCat.has(g.cat)) {
        const folder = {kind: "cat", key: "c:" + g.cat, label: g.cat,
                        children: []};
        byCat.set(g.cat, folder);
        folders.push(folder);
      }
      byCat.get(g.cat).children.push(entry);
    } else bare.push(entry);
  }
  const dirs = new Map();
  for (const rel of DATA.loose || []) {
    const dir = rel.includes("/") ? rel.slice(0, rel.lastIndexOf("/")) : ".";
    if (!dirs.has(dir)) dirs.set(dir, []);
    dirs.get(dir).push(rel);
  }
  const loose = [...dirs].map(([dir, rels]) =>
    ({kind: "dir", key: "d:" + dir, label: `${dir} (${rels.length})`,
      files: rels}));
  return [...bare, ...folders, ...loose];
}

function koPreview(f) {
  // 텍스트 1개인 파일은 파일명 옆에 ko 축약 (전각공백 제거, 5자 넘으면 …)
  if (f.rows.length !== 1) return null;
  const ko = (koOf(f.rows[0]) || "").replace(/　/g, "");
  if (!ko) return null;
  return [...ko].length > 5 ? [...ko].slice(0, 5).join("") + "…" : ko;
}

function makeGroupDetails(entry, filter) {
  const files = filter
    ? (entry.label.toLowerCase().includes(filter)
       ? entry.files
       : entry.files.filter(r => r.toLowerCase().includes(filter)))
    : entry.files;
  if (!files.length) return null;
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
    const preview = koPreview(f);
    const n = document.createElement("span");
    n.className = "n";
    n.textContent = preview !== null ? preview : f.rows.length;
    b.appendChild(n);
    b.addEventListener("click", () => {
      fileRel = rel;
      saveState();
      renderFileList(); renderRowsPanel(); renderStage();
    });
    det.appendChild(b);
  }
  det._count = files.length;
  return det;
}

function renderFileList() {
  const filter = document.getElementById("filter").value.trim().toLowerCase();
  const list = document.getElementById("fileList");
  list.textContent = "";
  let shown = 0;
  for (const node of sidebarTree()) {
    if (node.kind === "cat") {
      const kids = [];
      const catHit = filter && node.label.toLowerCase().includes(filter);
      for (const child of node.children) {
        const det = makeGroupDetails(child, catHit ? "" : filter);
        if (det) kids.push(det);
      }
      if (!kids.length) continue;
      const folder = document.createElement("details");
      folder.className = "cat";
      folder.open = !!filter
        || kids.some(k => k.querySelector(".item.on"))
        || openKeys.has(node.key);
      const sum = document.createElement("summary");
      sum.textContent = `${node.label} (${node.children.length})`;
      folder.appendChild(sum);
      folder.addEventListener("toggle", () => {
        if (folder.open) openKeys.add(node.key); else openKeys.delete(node.key);
      });
      for (const k of kids) { shown += k._count; folder.appendChild(k); }
      list.appendChild(folder);
    } else {
      const det = makeGroupDetails(node, filter);
      if (!det) continue;
      shown += det._count;
      list.appendChild(det);
    }
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
    tr.dataset.id = row.box_id;
    if (isEdited(row.box_id)) tr.classList.add("edited");
    if (selected && selected.id === row.box_id) tr.classList.add("sel");
    const td1 = document.createElement("td");
    td1.className = "id"; td1.textContent = row.box_id;
    if (isEdited(row.box_id)) {              // 행 단위 되돌리기 (ko + 상자)
      const undo = document.createElement("button");
      undo.className = "undo";
      undo.textContent = "↺";
      undo.title = "이 행의 수정(번역·상자)을 되돌린다";
      undo.addEventListener("click", ev => {
        ev.stopPropagation();
        delete edits[row.box_id];
        saveEdits();
        renderRowsPanel();
        renderStage();
      });
      td1.appendChild(undo);
    }
    const td2 = document.createElement("td");
    td2.className = "jp"; td2.textContent = row.jp || "—";
    // id/원문 클릭 = 그 행의 text 상자 선택 (상자 오버레이 자동 켬)
    for (const td of [td1, td2])
      td.addEventListener("click", () => {
        selected = {id: row.box_id, kind: "box"};
        if (!showBoxes) {
          showBoxes = true;
          document.getElementById("boxBtn").classList.add("on");
          saveState();
          renderStage();
        } else if (overlayEl) redrawOverlay(file);
        syncTableSel();
      });
    const td3 = document.createElement("td");
    td3.className = "ko";
    const input = document.createElement("input");
    input.value = koOf(row);
    input.addEventListener("input", () => {
      const e = editEntry(row.box_id);
      if (input.value === row.ko) delete e.ko;
      else e.ko = input.value;
      pruneEntry(row.box_id);
      tr.classList.toggle("edited", isEdited(row.box_id));
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

function syncTableSel() {
  for (const tr of document.querySelectorAll("#rows tr"))
    tr.classList.toggle("sel", !!selected && tr.dataset.id === selected.id);
  const cur = document.querySelector("#rows tr.sel");
  if (cur) cur.scrollIntoView({block: "nearest"});
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
