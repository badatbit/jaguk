// 브리지 — 서비스워커가 서빙한 진짜 gui.html 안에 주입되어,
// ① 엔진 워커(Pyodide + typelet.gui 핸들러)를 부팅하고
// ② 서비스워커 ↔ 엔진 사이 요청을 중계하며
// ③ 원장 변경을 localStorage 로 미러링한다 (서버·디스크 기록 없음).
"use strict";
(() => {
  if (window.__jagukBridge) return;
  window.__jagukBridge = true;

  const root = location.pathname.replace(/app\/?$/, "");   // 예제 루트
  const storeKey = "jaguk-wasm-ledger:" + root;

  // 로딩 오버레이 — 엔진이 준비될 때까지 요청은 SW 에서 대기한다
  const ov = document.createElement("div");
  ov.style.cssText =
    "position:fixed;inset:0;z-index:99999;background:rgba(14,16,20,.92);" +
    "color:#d6dae2;display:flex;flex-direction:column;gap:10px;" +
    "align-items:center;justify-content:center;" +
    "font:14px/1.6 'IBM Plex Sans KR',system-ui,sans-serif;text-align:center";
  ov.innerHTML = "<b>jaguk GUI (브라우저 판)</b>" +
    "<span id='jw-stat'>같은 typelet 엔진(wasm) 준비 중…</span>" +
    "<small style='color:#8a90a0'>로컬 <code>jaguk gui</code> 와 같은 " +
    "gui.html + gui.py 를 그대로 실행합니다.<br>원장 수정은 이 브라우저의 " +
    "localStorage 에만 저장됩니다. OCR·erase 는 브라우저 판 미지원.</small>";
  document.documentElement.appendChild(ov);
  const stat = ov.querySelector("#jw-stat");
  const setStat = t => {
    if (t === null) ov.remove();
    else stat.textContent = t;
  };

  const worker = new Worker(root + "../_app/engine.js");

  let currentPort = null;
  function makePort() {
    if (!navigator.serviceWorker.controller) return;
    const ch = new MessageChannel();
    ch.port1.onmessage = e => {
      const m = e.data;
      worker.postMessage({type: "request", ...m}, m.body ? [m.body] : []);
    };
    currentPort = ch.port1;
    navigator.serviceWorker.controller.postMessage(
      {type: "engine-port"}, [ch.port2]);
  }

  worker.onmessage = e => {
    const m = e.data;
    if (m.type === "status") setStat(m.text);
    else if (m.type === "progress") setStat(`이미지 로딩 ${m.done}/${m.total}`);
    else if (m.type === "ready") setStat(null);
    else if (m.type === "fatal") setStat("엔진 오류: " + m.text);
    else if (m.type === "response") {
      if (currentPort)
        currentPort.postMessage(m, m.body ? [m.body.buffer] : []);
    } else if (m.type === "ledger") {
      // 원장 미러 — 저장 시각·빌드 버전을 봉투로 함께 남긴다
      try {
        localStorage.setItem(storeKey, JSON.stringify(
          {version: window.__jagukVersion || "dev",
           savedAt: new Date().toISOString(), text: m.text}));
      } catch (err) { /* 용량 초과 등 — 포기 */ }
    }
  };

  navigator.serviceWorker.addEventListener("message", e => {
    if (e.data && e.data.type === "need-port") makePort();
  });
  makePort();

  (async () => {
    // data.js 는 재검증해 현재 빌드 버전을 안다 — 파일 캐시 무효화 기준
    const text = await (await fetch(root + "data.js",
                                    {cache: "no-cache"})).text();
    const shim = {};
    new Function("window", text)(shim);        // window.DEMO_DATA 추출
    window.__jagukVersion = (shim.DEMO_DATA || {}).version || "dev";
    let saved = null;
    try {
      const raw = localStorage.getItem(storeKey);
      if (raw) {
        try {
          const env = JSON.parse(raw);
          saved = env && typeof env.text === "string" ? env.text : raw;
        } catch (e) { saved = raw; }           // 구 형식(원장 원문 그대로)
      }
    } catch (e) { /* 없음 */ }
    worker.postMessage({type: "init", base: root,
                        manifest: shim.DEMO_DATA, saved});
  })().catch(e => setStat("부팅 실패: " + (e && e.message || e)));
})();
