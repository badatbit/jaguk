# -*- coding: utf-8 -*-
"""브라우저(Pyodide)에서 jaguk GUI 서버를 **그대로** 구동하는 접착 코드.

별도 구현이 아니다 — /lib 의 typelet.gui.make_handler() 가 만드는 실제
HTTP 핸들러를 가짜 소켓(BytesIO)으로 구동한다. 로컬에서 `jaguk gui` 가
띄우는 서버와 같은 코드 경로이므로 응답도 같다. 원장 수정(상자 이동·
번역·스타일…)은 브라우저 FS 의 원장 파일에 쓰이고, 바깥(JS)이 이를
localStorage 로 미러링한다."""
import io
import sys
from pathlib import Path

sys.path.insert(0, "/lib")
from typelet import config as tconfig, gui as guimod  # noqa: E402

CONFIG = Path("/proj/typelet.config.json")
PROJECT = tconfig.load_path(CONFIG)
Handler = guimod.make_handler(PROJECT, config_path=CONFIG)


class Driver(Handler):
    """소켓 없이 핸들러를 한 요청만큼 돌린다 — rfile/wfile 만 흉내낸다."""

    def __init__(self, raw: bytes):                 # noqa: super().__init__ 안 함
        self.rfile = io.BytesIO(raw)
        self.wfile = io.BytesIO()
        self.client_address = ("127.0.0.1", 0)
        self.server = None
        self.close_connection = True
        self.raw_requestline = self.rfile.readline()
        if not self.parse_request():
            return
        method = getattr(self, "do_" + self.command, None)
        if method is None:
            self.send_error(501)
        else:
            method()


def _debug_render(target):
    """gui.render_injected 가 삼키는 예외를 트레이스백으로 꺼내는 진단 경로."""
    import traceback
    from urllib.parse import parse_qs, urlparse
    rel = parse_qs(urlparse(target).query).get("path", [""])[0]
    try:
        from typelet import ledger as ledgermod, render as rendermod
        data = ledgermod.load(PROJECT)
        styles = ledgermod.styles_map(data)
        flat = [r for r in ledgermod.flat_rows(data) if r["file"] == rel]
        specs = []
        for row in flat:
            if row.get("status") == "no_inject":
                continue
            if not (row.get("ko_text") or "").strip():
                continue
            try:
                specs.append(rendermod.resolve(row, styles))
            except rendermod.SkipRow as e:
                specs.append(None)
        posts = [p for p in data.get("post", []) if p.get("file") == rel]
        out, _, _ = rendermod.compose_file(
            PROJECT, rel, [s for s in specs if s], posts=posts or None)
        text = f"OK {out.size} specs={len(specs)}"
    except Exception:
        text = traceback.format_exc()
    return 200, "text/plain; charset=utf-8", text.encode("utf-8")


def serve(method, target, body=None):
    """(status, content_type, body_bytes) — target 은 경로+쿼리."""
    if target.startswith("/api/debug-render"):
        return _debug_render(target)
    payload = bytes(body) if body is not None else b""
    head = (f"{method} {target} HTTP/1.1\r\n"
            f"Host: local\r\n"
            f"Content-Length: {len(payload)}\r\n"
            f"Content-Type: application/json\r\n\r\n")
    driver = Driver(head.encode("latin-1") + payload)
    raw = driver.wfile.getvalue()
    header_end = raw.find(b"\r\n\r\n")
    if header_end < 0:
        return 500, "text/plain", b"empty response"
    head_lines = raw[:header_end].decode("latin-1").split("\r\n")
    status = int(head_lines[0].split(" ", 2)[1])
    ctype = "application/octet-stream"
    for line in head_lines[1:]:
        if line.lower().startswith("content-type:"):
            ctype = line.split(":", 1)[1].strip()
    return status, ctype, raw[header_end + 4:]


def ledger_text():
    """현재 원장 파일 내용 — 수정 후 localStorage 미러링용."""
    return PROJECT.ledger_path.read_text(encoding="utf-8")


def write_ledger(text):
    """localStorage 에 미러링해 둔 원장을 복원한다 (부팅 시)."""
    PROJECT.ledger_path.write_text(text, encoding="utf-8")
