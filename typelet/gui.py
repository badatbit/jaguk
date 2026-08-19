# -*- coding: utf-8 -*-
"""jaguk gui — 원장 검수용 로컬 웹 툴 (표준 라이브러리만 사용).

왼쪽: text-only 묶음 그룹(saveloadspotname 류는 항목 리스트로) + 행이 있는 파일 목록
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
    # 규칙(set) 경로 기준 그룹핑 — roadguidesign 434판 같은 무리가 트리에서
    # 접히는 그룹으로 보이게 (데이터 모델은 행 그대로, 표시만 묶는다)
    rules_map = data.get("rules", {})
    grouped: dict[str, list] = {}
    loose = []
    for relative, count in sorted(by_file.items()):
        rule_path, rule = ledgermod.match_rule(rules_map, relative)
        entry = {"file": relative, "rows": count}
        if rule_path and rule_path != relative:     # 디렉토리 규칙만 그룹
            grouped.setdefault(rule_path, []).append(entry)
        else:
            loose.append(entry)
    groups = [{"name": path, "mode": ledgermod.rule_mode(rules_map[path]),
               "count": len(entries), "files": entries}
              for path, entries in sorted(grouped.items())]
    return {"catalogs": catalogs, "groups": groups, "files": loose,
            "rules": rules_map}


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
    # blank 베이스(text-only)의 erased 는 공백 이미지로 즉석 생성된다
    images["erased"] = images["erased"] or any(
        r.get("base") == "blank" for r in flat)
    # injected 는 항상 생성한다 — erased 없으면 원본 덧구움, 스펙 없으면
    # 베이스/원본 그대로. 원본조차 없을 때만 못 보여준다
    images["injected"] = images["injected"] or images["original"] \
        or images["erased"]
    injected_reason = None
    if not images["injected"]:
        injected_reason = "원본/베이스 이미지가 없음"

    # 행별 문제 진단 — 개별 text 상자에 에러로 표시된다
    from . import render as rendermod
    styles_by_name = ledgermod.styles_map(data)
    for row in flat:
        problem = None
        if not (row.get("ko_text") or "").strip():
            problem = "번역문 없음"
        else:
            try:
                rendermod.resolve(dict(row), styles_by_name)
            except rendermod.SkipRow as skip:
                problem = f"미완: {skip}"
            except Exception as error:
                problem = f"오류: {error}"
        row["_problem"] = problem
    return {
        "file": relative,
        "rows": flat,                    # 평면 행 (text-only 전개 포함)
        "raw": raw_rows,                 # 일반 행의 구조형 (inline 속성 표시용)
        "catalog": catalog,
        "entry": entry,
        "rule": rule,
        "styles": styles,
        "images": images,
        "injected_reason": injected_reason,
    }


def render_blank(project: Project, relative: str) -> bytes | None:
    """blank 베이스(text-only 묶음) 파일의 erased = 공백 이미지.

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
    def store(body: bytes | None) -> bytes | None:
        if body is not None:
            _INJECT_CACHE[relative] = (cache_key, body)
        return body

    if not specs:
        # 번역문이 하나도 없어도 injected 는 생성 — 지워진 베이스 그대로,
        # 그것도 없으면 원본 그대로. 문제는 개별 text 상자 에러가 알린다.
        blank = render_blank(project, relative)
        if blank is not None:
            return store(blank)
        for root in (project.base_root, project.original_root):
            path = _safe_join(root, relative)
            if path and path.exists():
                return store(path.read_bytes())
        return None

    posts = [p for p in data.get("post", []) if p.get("file") == relative]
    output = None
    try:
        output, _, _ = rendermod.compose_file(project, relative, specs,
                                              posts=posts or None)
    except FileNotFoundError:
        # erased 베이스가 아직 없으면 원본 위 덧구움으로 미리보기
        try:
            output, _, _ = rendermod.compose_file(
                project, relative, specs,
                base_root=project.original_root, posts=posts or None)
        except Exception:
            return None
    except Exception:
        return None
    buffer = BytesIO()
    output.save(buffer, "PNG")
    return store(buffer.getvalue())


def reextract(project: Project, relative: str) -> str:
    """이 파일의 상자를 지우고 현행 OCR 스택으로 재추출 (GUI 버튼).

    **ko 가 입력된 행/항목은 보존한다** — 사람이 넣은 번역을 잃지 않도록.
    보존된 행의 자리는 중복 판정에 걸려 새 씨앗이 그 위에 덧나지 않는다.
    """
    from contextlib import redirect_stdout
    from io import StringIO

    data = ledgermod.load(project)
    removed = kept = 0
    new_rows = []
    for row in ledgermod.rows(data):
        if row.get("file") == relative and not (row.get("ko") or "").strip():
            removed += 1
            continue
        if row.get("file") == relative:
            kept += 1
        new_rows.append(row)
    data["rows"] = new_rows
    for cat in ledgermod.catalogs(data):
        prefix = (cat.get("dir") or "").strip("/")
        if not (prefix and relative.startswith(prefix + "/")):
            continue
        fname = relative[len(prefix) + 1:]
        entry = (cat.get("entries") or {}).get(fname)
        if entry is not None:
            if (entry.get("ko") or "").strip():
                kept += 1
            else:
                del cat["entries"][fname]
                removed += 1
    ledgermod.save(project, data)
    _INJECT_CACHE.pop(relative, None)

    from . import jaguk as jagukmod
    buffer = StringIO()
    with redirect_stdout(buffer):
        jagukmod.run_extract(project, only=relative)
    return (f"기존 상자 {removed}개 삭제, ko 입력 {kept}개 보존\n"
            + buffer.getvalue())


# ---- 박스 편집 (GUI 전용) ------------------------------------------------------

def _read_region(project: Project, relative: str, rect: list) -> str:
    """상자 영역의 원문 판독 — recognizer(manga-ocr)가 켜져 있을 때만."""
    if project.ocr_recognizer != "manga-ocr":
        return ""
    from PIL import Image

    from . import ocr as ocrmod
    path = _safe_join(project.original_root, relative)
    if not path or not path.exists():
        return ""
    image = Image.open(path).convert("RGBA")
    pad = 2
    x, y, w, h = rect
    crop = image.crop((max(0, x - pad), max(0, y - pad),
                       min(image.width, x + w + pad),
                       min(image.height, y + h + pad)))
    try:
        return ocrmod._recognize_crop(crop)
    except Exception:
        return ""


def _alloc_id(existing: set, prefix: str) -> str:
    import re
    n = 0
    for bid in existing:
        m = re.fullmatch(rf"{re.escape(prefix)}(\d+)", bid or "")
        if m:
            n = max(n, int(m.group(1)))
    while True:
        n += 1
        bid = f"{prefix}{n}"
        if bid not in existing:
            return bid


def box_delete(project: Project, relative: str, box_id: str) -> str:
    data = ledgermod.load(project)
    rows = ledgermod.rows(data)
    for i, row in enumerate(rows):
        if row.get("box_id") == box_id and row.get("file") == relative:
            del rows[i]
            ledgermod.save(project, data)
            _INJECT_CACHE.pop(relative, None)
            return f"행 {box_id} 삭제"
    for cat in ledgermod.catalogs(data):        # text-only 항목 (이름:stem)
        prefix = (cat.get("dir") or "").strip("/")
        for fname in list(cat.get("entries") or {}):
            stem = fname.split(".")[0]
            if f"{cat['name']}:{stem}" == box_id:
                del cat["entries"][fname]
                ledgermod.save(project, data)
                _INJECT_CACHE.pop(relative, None)
                return f"text-only 항목 {fname} 삭제"
    raise ValueError(f"행을 찾지 못함: {box_id}")


def box_add(project: Project, relative: str, rect: list) -> str:
    from PIL import Image

    from . import ocr as ocrmod
    data = ledgermod.load(project)
    flat = [r for r in ledgermod.flat_rows(data) if r["file"] == relative]
    if any(r.get("base") == "blank" for r in flat):
        raise ValueError("text-only 파일은 entries 로 관리합니다 — 행 추가 불가")
    path = _safe_join(project.original_root, relative)
    with Image.open(path) as image:
        canvas = list(image.size)
    rows = ledgermod.rows(data)
    box_id = _alloc_id({r.get("box_id") for r in rows},
                       ocrmod._id_prefix(relative))
    jp = _read_region(project, relative, rect)
    _, rule = ledgermod.match_rule(data.get("rules", {}), relative)
    rows.append({
        "box_id": box_id, "file": relative, "element_id": None,
        "run_id": None, "jp": jp, "ko": "", "ocr_id": None,
        "crop": None, "text": list(rect), "source": list(rect),
        "canvas": canvas, "pad": None, "style": rule.get("style", ""),
        "opacity": "FF", "status": "todo", "notes": "manual",
    })
    ledgermod.save(project, data)
    _INJECT_CACHE.pop(relative, None)
    return f"행 {box_id} 추가 (jp 판독: {jp or '—'})"


def box_update(project: Project, relative: str, box_id: str,
               key: str, rect: list) -> str:
    """상자 크기/위치 갱신 (GUI 리사이즈 핸들). key = text|crop|source.

    slot 참조 행에 쓰면 행 자체에 override 가 생긴다 — 그 행만 규칙과
    달라진다는 뜻이며, flat 해석에서 행 값이 우선한다."""
    if key not in ("text", "crop", "source"):
        raise ValueError(f"모르는 상자 종류: {key}")
    rect = [int(v) for v in rect]
    if rect[2] < 2 or rect[3] < 2:
        raise ValueError(f"상자가 너무 작습니다: {rect}")
    data = ledgermod.load(project)
    for row in ledgermod.rows(data):
        if row.get("box_id") == box_id and row.get("file") == relative:
            if key == "crop":
                crop = row.get("crop") or {"id": None, "src": "manual"}
                crop["rect"] = rect
                row["crop"] = crop
            else:
                row[key] = rect
            ledgermod.save(project, data)
            _INJECT_CACHE.pop(relative, None)
            return f"{box_id}.{key} = {rect}"
    for cat in ledgermod.catalogs(data):        # text-only 항목 — text 만
        prefix = (cat.get("dir") or "").strip("/")
        for fname, entry in (cat.get("entries") or {}).items():
            if f"{cat['name']}:{fname.split('.')[0]}" == box_id:
                if key != "text":
                    raise ValueError("text-only 항목은 text 상자만 조정 가능")
                entry["text"] = rect
                ledgermod.save(project, data)
                _INJECT_CACHE.pop(relative, None)
                return f"{box_id}.text = {rect} (entry override)"
    raise ValueError(f"행을 찾지 못함: {box_id}")


def box_reread(project: Project, relative: str, box_id: str) -> str:
    """이 박스만 원문 재판독 — 상자를 고친 뒤 그 영역의 jp 를 갱신한다.

    영역 = source(없으면 text, slot 참조면 규칙에서 해석). ko 는 보존 —
    번역이 이미 있으면 jp 만 바뀌었다고 알린다. 교정 사전이 있으면 스냅."""
    from . import ocr as ocrmod
    data = ledgermod.load(project)
    rect = None
    target_row = next((r for r in ledgermod.rows(data)
                       if r.get("box_id") == box_id
                       and r.get("file") == relative), None)
    if target_row is not None:
        # 자기 상자를 가진 행만 — slot 참조 행의 합집합 영역을 읽으면
        # 자간 넓은 원문을 오독한다 (실측: 厚田 → 年月)
        rect = target_row.get("source") or target_row.get("text")
        if rect is None:
            raise ValueError(
                "slot 참조 행은 재판독 불가 — 상자를 먼저 조정해 행 고유 "
                "source 를 만들거나, 원장의 jp 를 직접 고치세요")
    else:
        flat = next((r for r in ledgermod.flat_rows(data)
                     if r["box_id"] == box_id and r["file"] == relative), None)
        if flat is None:
            raise ValueError(f"행을 찾지 못함: {box_id}")
        values = [flat.get(f"text_{k}", "") for k in ("x", "y", "w", "h")]
        if all(v != "" for v in values):
            rect = [int(v) for v in values]
    if rect is None:
        raise ValueError("판독할 영역(source/text)이 없습니다")
    jp = _read_region(project, relative, rect)
    if not jp:
        raise ValueError("판독 결과가 비어 있습니다 (recognizer 설정 확인)")
    vocab = ocrmod.load_ocr_dict(project)
    if vocab:
        fake = [{"file": relative, "lines": [{"text": jp, "x": 0, "y": 0,
                                              "w": 1, "h": 1}]}]
        ocrmod.correct_results(fake, vocab, project.ocr_dict_min)
        jp = fake[0]["lines"][0]["text"]

    for row in ledgermod.rows(data):
        if row.get("box_id") == box_id and row.get("file") == relative:
            old = row.get("jp", "")
            row["jp"] = jp
            note = " (ko 는 보존 — 번역 확인 필요)" if (row.get("ko") or "").strip() else ""
            ledgermod.save(project, data)
            _INJECT_CACHE.pop(relative, None)
            return f"jp 재판독: {old!r} → {jp!r}{note}"
    for cat in ledgermod.catalogs(data):
        prefix = (cat.get("dir") or "").strip("/")
        for fname, entry in (cat.get("entries") or {}).items():
            if f"{cat['name']}:{fname.split('.')[0]}" == box_id:
                old = entry.get("jp", "")
                entry["jp"] = jp
                ledgermod.save(project, data)
                _INJECT_CACHE.pop(relative, None)
                return f"jp 재판독: {old!r} → {jp!r}"
    raise ValueError(f"행을 찾지 못함: {box_id}")


def box_split(project: Project, relative: str, box_id: str, at: int) -> str:
    data = ledgermod.load(project)
    rows = ledgermod.rows(data)
    index = next((i for i, r in enumerate(rows)
                  if r.get("box_id") == box_id and r.get("file") == relative),
                 None)
    if index is None:
        raise ValueError(f"행을 찾지 못함: {box_id} (slot 참조/text-only 는 나누기 불가)")
    row = rows[index]

    def halves(rect):
        if not rect:
            return None, None
        x, y, w, h = rect
        cut = max(x + 2, min(at, x + w - 2))
        return [x, y, cut - x, h], [cut, y, x + w - cut, h]

    text_l, text_r = halves(row.get("text"))
    source_l, source_r = halves(row.get("source"))
    crop_rect = (row.get("crop") or {}).get("rect")
    crop_l, crop_r = halves(crop_rect)
    if not (text_l or source_l):
        raise ValueError("나눌 상자(text/source)가 없습니다")

    existing = {r.get("box_id") for r in rows}
    parts = []
    for suffix, text, source, crop in (("a", text_l, source_l, crop_l),
                                       ("b", text_r, source_r, crop_r)):
        part = dict(row)
        new_id = f"{box_id}{suffix}"
        while new_id in existing:
            new_id += suffix
        existing.add(new_id)
        part["box_id"] = new_id
        part["text"] = text
        part["source"] = source
        part["crop"] = ({"id": None, "src": "manual", "rect": crop}
                        if crop else None)
        part["jp"] = _read_region(project, relative, source or text) or row.get("jp", "")
        part["ko"] = ""
        part["notes"] = "manual split"
        parts.append(part)
    rows[index:index + 1] = parts
    ledgermod.save(project, data)
    _INJECT_CACHE.pop(relative, None)
    return (f"{box_id} → {parts[0]['box_id']}({parts[0]['jp'] or '—'}) + "
            f"{parts[1]['box_id']}({parts[1]['jp'] or '—'})")


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

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def do_POST(self):
            url = urlparse(self.path)
            query = parse_qs(url.query)
            relative = query.get("path", [""])[0]
            box_id = query.get("id", [""])[0]
            try:
                path = unquote(url.path)
                if path == "/api/reextract":
                    self._json({"log": reextract(project, relative)})
                elif path == "/api/box/delete":
                    self._json({"log": box_delete(project, relative, box_id)})
                elif path == "/api/box/add":
                    rect = self._body().get("rect")
                    self._json({"log": box_add(project, relative, rect)})
                elif path == "/api/box/split":
                    at = int(self._body().get("at", 0))
                    self._json({"log": box_split(project, relative, box_id, at)})
                elif path == "/api/box/update":
                    body = self._body()
                    self._json({"log": box_update(project, relative, box_id,
                                                  body.get("key", "text"),
                                                  body.get("rect"))})
                elif path == "/api/box/reread":
                    self._json({"log": box_reread(project, relative, box_id)})
                else:
                    self.send_error(404)
            except BrokenPipeError:
                pass
            except Exception as error:
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
