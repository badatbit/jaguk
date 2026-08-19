# -*- coding: utf-8 -*-
"""jaguk gui — 원장 검수용 로컬 웹 툴 (표준 라이브러리만 사용).

왼쪽: 카탈로그 그룹(saveloadspotname 류는 항목 리스트로) + 행이 있는 파일 목록
가운데: 이미지 뷰 — 원본 / erased / injected / crop-box / text-box
오른쪽: 선택한 상자의 속성 — inline(행/entry) → catalog 기본값 → style →
        상위 style(style 의 "base" 사슬)을 아래로 계속 표시

읽기 전용이다 — 원장 수정은 파일/CLI 로 한다. 서버는 localhost 전용.
"""

from __future__ import annotations

import json
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import ledger as ledgermod
from .config import Project

PAGE = Path(__file__).resolve().parent / "gui.html"

IMAGE_ROOTS = {
    "original": lambda p: p.original_root,
    "erased": lambda p: p.base_root,
    "injected": lambda p: p.output_root,
}


def _safe_join(root: Path, relative: str) -> Path | None:
    candidate = (root / Path(*relative.split("/"))).resolve()
    if root.resolve() != candidate and root.resolve() not in candidate.parents:
        return None
    return candidate


def build_tree(project: Project) -> dict:
    data = ledgermod.load(project)
    catalogs = []
    for cat in ledgermod.catalogs(data):
        entries = [
            {"name": fname, "file": f"{cat.get('dir', '').strip('/')}/{fname}",
             "jp": e.get("jp", ""), "ko": e.get("ko", ""),
             "status": e.get("status", "todo")}
            for fname, e in sorted((cat.get("entries") or {}).items())
        ]
        catalogs.append({"name": cat["name"], "dir": cat.get("dir", ""),
                         "count": len(entries), "entries": entries})
    by_file: dict[str, int] = {}
    for row in ledgermod.rows(data):
        by_file[row["file"]] = by_file.get(row["file"], 0) + 1
    files = [{"file": f, "rows": n} for f, n in sorted(by_file.items())]
    return {"catalogs": catalogs, "files": files,
            "rules": data.get("rules", {})}


def build_detail(project: Project, relative: str) -> dict:
    data = ledgermod.load(project)
    terms = ledgermod.load_terms(project, data)
    flat = [r for r in ledgermod.flat_rows(data) if r["file"] == relative]
    if terms:
        ledgermod.apply_terms(flat, terms)
    raw_rows = {r["box_id"]: r for r in ledgermod.rows(data)
                if r.get("file") == relative}

    catalog = None
    entry = None
    for cat in ledgermod.catalogs(data):
        prefix = (cat.get("dir") or "").strip("/")
        if prefix and relative.startswith(prefix + "/"):
            fname = relative[len(prefix) + 1:]
            if fname in (cat.get("entries") or {}):
                entry = cat["entries"][fname]
                catalog = {k: v for k, v in cat.items() if k != "entries"}
            break

    _, rule = ledgermod.match_rule(data.get("rules", {}), relative)
    styles = {s["name"]: s for s in data.get("styles", [])}
    images = {kind: (_safe_join(get_root(project), relative) or Path()).exists()
              for kind, get_root in IMAGE_ROOTS.items()}
    return {
        "file": relative,
        "rows": flat,                    # 평면 행 (카탈로그 전개 포함)
        "raw": raw_rows,                 # 일반 행의 구조형 (inline 속성 표시용)
        "catalog": catalog,
        "entry": entry,
        "rule": rule,
        "styles": styles,
        "images": images,
    }


def make_handler(project: Project):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):     # 콘솔 소음 줄이기
            pass

        def _json(self, payload, status=200):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            url = urlparse(self.path)
            path = unquote(url.path)
            try:
                if path == "/":
                    body = PAGE.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif path == "/api/tree":
                    self._json(build_tree(project))
                elif path == "/api/file":
                    relative = parse_qs(url.query).get("path", [""])[0]
                    self._json(build_detail(project, relative))
                elif path.startswith("/img/"):
                    _, _, kind, relative = path.split("/", 3)
                    root_fn = IMAGE_ROOTS.get(kind)
                    target = root_fn and _safe_join(root_fn(project), relative)
                    if not target or not target.exists():
                        self.send_error(404)
                        return
                    body = target.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_error(404)
            except BrokenPipeError:
                pass
            except Exception as error:          # 페이지가 오류를 볼 수 있게
                self._json({"error": str(error)}, status=500)

    return Handler


def run(project: Project, port: int = 52485, open_browser: bool = True) -> int:
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(project))
    url = f"http://127.0.0.1:{port}/"
    print(f"jaguk gui: {url}  (프로젝트 {project.root}, Ctrl+C 로 종료)")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n종료")
    return 0
