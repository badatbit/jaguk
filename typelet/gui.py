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
    images = {}
    for kind, get_root in IMAGE_ROOTS.items():
        target = _safe_join(get_root(project), relative)
        images[kind] = bool(target and target.exists())
    # injected 는 즉석 렌더 — ko(용어표 해석 포함)가 있는 행이 하나라도 있으면
    # 디스크 산출물 없이도 미리보기가 가능하다
    has_translation = any((r.get("ko_text") or "").strip() for r in flat)
    images["injected"] = images["injected"] or has_translation
    # blank 베이스(text-only)의 erased 는 공백 이미지로 즉석 생성된다
    images["erased"] = images["erased"] or any(
        r.get("base") == "blank" for r in flat)
    # 못 보여주는 이유 — 이미지가 없는 게 아니라 데이터 문제임을 구분한다
    injected_reason = None
    if not images["injected"]:
        injected_reason = ("번역 데이터가 없음 (ko 비어 있고 용어표 미해석)"
                           if flat else "원장 데이터가 없음 (행/카탈로그 항목 없음)")
    return {
        "file": relative,
        "rows": flat,                    # 평면 행 (카탈로그 전개 포함)
        "raw": raw_rows,                 # 일반 행의 구조형 (inline 속성 표시용)
        "catalog": catalog,
        "entry": entry,
        "rule": rule,
        "styles": styles,
        "images": images,
        "injected_reason": injected_reason,
    }


def render_blank(project: Project, relative: str) -> bytes | None:
    """blank 베이스(text-only 카탈로그) 파일의 erased = 공백 이미지.

    이미지 전체가 글자라 지우면 아무것도 안 남는다 — 파일 없이 캔버스
    크기의 투명 PNG 를 즉석 생성한다. blank 가 아니면 None."""
    from io import BytesIO

    from PIL import Image

    data = ledgermod.load(project)
    flat = [r for r in ledgermod.flat_rows(data) if r["file"] == relative]
    if not any(r.get("base") == "blank" for r in flat):
        return None
    width = height = 0
    for row in flat:
        w = int(row.get("canvas_w") or 0)
        h = int(row.get("canvas_h") or 0)
        if w and h:
            width, height = w, h
            break
    if not (width and height):          # canvas "original" — 원본 크기
        original = _safe_join(project.original_root, relative)
        if not original or not original.exists():
            return None
        with Image.open(original) as image:
            width, height = image.size
    buffer = BytesIO()
    Image.new("RGBA", (width, height), (0, 0, 0, 0)).save(buffer, "PNG")
    return buffer.getvalue()


# 즉석 렌더 캐시 — 원장/베이스/원본이 안 바뀌었으면 재합성하지 않는다
# (겹침 보기 등에서 같은 이미지를 연속 요청할 때 1~3초 재렌더 방지)
_INJECT_CACHE: dict[str, tuple[tuple, bytes]] = {}


def _inject_cache_key(project: Project, relative: str) -> tuple:
    parts = []
    for path in (project.ledger_path,
                 _safe_join(project.base_root, relative),
                 _safe_join(project.original_root, relative)):
        parts.append(path.stat().st_mtime_ns if path and path.exists() else 0)
    return tuple(parts)


def render_injected(project: Project, relative: str) -> bytes | None:
    """원장 **현재 상태**로 즉석 합성한 injected 미리보기 PNG.

    status 와 무관하게 ko(용어표 해석 포함)가 있고 스타일이 갖춰진 행을
    전부 렌더한다 — inject 를 돌리기 전에도 결과를 볼 수 있다.
    합성 불가(스펙 없음·베이스 없음 등)면 None — 호출자가 디스크 산출물로
    폴백한다.
    """
    from io import BytesIO

    from . import render as rendermod

    cache_key = _inject_cache_key(project, relative)
    cached = _INJECT_CACHE.get(relative)
    if cached and cached[0] == cache_key:
        return cached[1]

    data = ledgermod.load(project)
    styles = ledgermod.styles_map(data)
    flat = [r for r in ledgermod.flat_rows(data) if r["file"] == relative]
    terms = ledgermod.load_terms(project, data)
    if terms:
        ledgermod.apply_terms(flat, terms)
    flows = {r["box_id"]: r.get("flow") for r in ledgermod.rows(data)
             if r.get("flow")}
    specs = []
    for row in flat:
        if row.get("status") == "no_inject":
            continue
        if not (row.get("ko_text") or "").strip():
            continue
        try:
            spec = rendermod.resolve(row, styles)
        except rendermod.SkipRow:
            continue
        spec.flow = flows.get(spec.box_id)
        specs.append(spec)
    if not specs:
        return None
    posts = [p for p in data.get("post", []) if p.get("file") == relative]
    try:
        output, _, _ = rendermod.compose_file(project, relative, specs,
                                              posts=posts or None)
    except Exception:
        return None
    buffer = BytesIO()
    output.save(buffer, "PNG")
    body = buffer.getvalue()
    _INJECT_CACHE[relative] = (cache_key, body)
    return body


def make_handler(project: Project):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):     # 콘솔 소음 줄이기
            pass

        def _json(self, payload, status=200):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
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
                    self.send_header("Cache-Control", "no-store")
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
                    body = None
                    if kind == "injected":
                        # 원장 현재 상태로 즉석 렌더 — 실패 시 디스크 폴백
                        body = render_injected(project, relative)
                    elif kind == "erased":
                        root = IMAGE_ROOTS["erased"](project)
                        disk = _safe_join(root, relative)
                        if not disk or not disk.exists():
                            # text-only(blank 베이스)는 공백 이미지가 정답
                            body = render_blank(project, relative)
                    if body is None:
                        root_fn = IMAGE_ROOTS.get(kind)
                        target = root_fn and _safe_join(root_fn(project), relative)
                        if not target or not target.exists():
                            self.send_error(404)
                            return
                        body = target.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Cache-Control", "no-store")
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
