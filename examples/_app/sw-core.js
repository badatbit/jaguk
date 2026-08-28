// 서비스워커 — 진짜 gui.html 이 부르는 /api/·/img/ 요청을 가로채
// 페이지의 브리지(→ 엔진 워커의 typelet.gui 핸들러)로 넘긴다.
// 정적 파일 요청은 손대지 않는다 (네트워크 통과).
"use strict";

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(self.clients.claim()));

// 포트는 페이지(client)별로 관리한다 — 리로드하면 새 페이지가 새 포트를
// 등록하는데, 이전 페이지의 죽은 포트로 요청을 보내면 영원히 매달린다.
const ports = new Map();                     // clientId → MessagePort
const portWaiters = new Map();               // clientId → [resolve…]
const pending = new Map();
let seq = 0;

function onPortMessage(e) {
  const m = e.data;
  if (m && m.type === "response" && pending.has(m.id)) {
    const resolve = pending.get(m.id);
    pending.delete(m.id);
    resolve(m);
  }
}

self.addEventListener("message", e => {
  if (e.data && e.data.type === "engine-port" && e.ports[0]) {
    const cid = (e.source && e.source.id) || "_";
    const p = e.ports[0];
    p.onmessage = onPortMessage;
    ports.set(cid, p);
    for (const w of portWaiters.get(cid) || []) w(p);
    portWaiters.delete(cid);
  }
});

async function getPort(clientId) {
  const cid = clientId || "_";
  if (ports.has(cid)) return ports.get(cid);
  const client = clientId && await self.clients.get(clientId);
  if (client) client.postMessage({type: "need-port"});
  else {
    const clientList = await self.clients.matchAll({type: "window"});
    for (const c of clientList) c.postMessage({type: "need-port"});
  }
  return new Promise(resolve => {
    if (!portWaiters.has(cid)) portWaiters.set(cid, []);
    portWaiters.get(cid).push(resolve);
  });
}

async function serveApp(scopePath) {
  // gui.html 원본: Pages 는 _typelet/, 레포 루트 정적 서빙은 ../typelet/
  let res = await fetch(scopePath + "../_typelet/gui.html", {cache: "no-store"});
  if (!res.ok)
    res = await fetch(scopePath + "../../typelet/gui.html", {cache: "no-store"});
  if (!res.ok)
    return new Response("gui.html 을 찾을 수 없습니다", {status: 500});
  let html = await res.text();
  // 브리지 주입 — 엔진 부팅·요청 중계·localStorage 미러링
  html = html.replace(/<head[^>]*>/i,
                      m => m + `\n<script src="${scopePath}../_app/bridge.js"></script>`);
  return new Response(html,
    {headers: {"Content-Type": "text/html; charset=utf-8",
               "Cache-Control": "no-store"}});
}

async function proxy(request, clientId) {
  const p = await getPort(clientId);
  const id = ++seq;
  const url = new URL(request.url);
  const target = url.pathname + url.search;
  let body = null;
  if (request.method === "POST" || request.method === "PUT")
    body = await request.arrayBuffer();
  const reply = new Promise(resolve => pending.set(id, resolve));
  p.postMessage({id, method: request.method, url: target, body},
                body ? [body] : []);
  const m = await reply;
  return new Response(m.body, {
    status: m.status,
    headers: {"Content-Type": m.ctype, "Cache-Control": "no-store"},
  });
}

self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (url.origin !== self.location.origin) return;
  const scopePath = new URL(self.registration.scope).pathname;
  if (url.pathname === scopePath + "app" || url.pathname === scopePath + "app/") {
    e.respondWith(serveApp(scopePath));
    return;
  }
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/img/")) {
    e.respondWith(proxy(e.request, e.clientId));
  }
});
