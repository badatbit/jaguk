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

from PIL import Image

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
    # 디스크(originals)에 있는데 행이 없는 파일도 상자 0개로 남긴다 —
    # 상자 없음은 "할 일 없음"이지 목록에서 사라질 이유가 아니다.
    # text-only 카탈로그 소속과 ignore 규칙 파일만 뺀다.
    catalog_files = {f"{(cat.get('dir') or '').strip('/')}/{fname}"
                     for cat in ledgermod.catalogs(data)
                     for fname in (cat.get("entries") or {})}
    if project.original_root.is_dir():
        for path in sorted(project.original_root.rglob("*.png")):
            relative = path.relative_to(project.original_root).as_posix()
            if relative in by_file or relative in catalog_files:
                continue
            _, rule = ledgermod.match_rule(rules_map, relative)
            if ledgermod.rule_mode(rule) == "ignore":
                continue
            by_file[relative] = 0
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
    recompose_spec = next((s for s in (data.get("recompose") or [])
                           if s.get("file") == relative), None)
    posts = [p for p in (data.get("post") or []) if p.get("file") == relative]
    # 이미지 크기 — 클라이언트가 로드 전에 자리를 확정할 수 있게 (리플로 방지).
    # 세 트리 모두 같은 크기다; blank(text-only)는 canvas 가 크기다.
    size = None
    for get_root in (IMAGE_ROOTS["original"], IMAGE_ROOTS["erased"]):
        target = _safe_join(get_root(project), relative)
        if target and target.exists():
            with Image.open(target) as im:
                size = list(im.size)
            break
    if size is None and flat:
        w = int(flat[0].get("canvas_w") or 0)
        h = int(flat[0].get("canvas_h") or 0)
        if w and h:
            size = [w, h]
    return {
        "file": relative,
        "size": size,
        "recompose": recompose_spec,
        "post": posts,
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
    parts = [project.supersample]        # 렌더 배율이 바뀌면 캐시도 갈린다
    for path in (project.ledger_path,
                 _safe_join(project.base_root, relative),
                 _safe_join(project.original_root, relative)):
        parts.append(path.stat().st_mtime_ns if path and path.exists() else 0)
    return tuple(parts)


def style_coerce(value):
    """GUI 입력 문자열 → 원장 값. JSON 이 되면 그 타입(수·배열·객체),
    아니면 문자열. 빈 문자열은 None (키 제거 신호)."""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def style_update(project: Project, name: str, updates: dict) -> str:
    """스타일 저장 (GUI 저장 버튼). 빈 값 키는 지운다."""
    data = ledgermod.load(project)
    style = next((s for s in data.get("styles", [])
                  if s.get("name") == name), None)
    if style is None:
        raise ValueError(f"원장에 없는 스타일: {name!r}")
    changed = []
    for key, raw in (updates or {}).items():
        if key == "name":
            continue                       # 이름 변경은 참조가 다 깨진다
        value = style_coerce(raw)
        if value is None:
            if key in style:
                del style[key]
                changed.append(f"-{key}")
        elif style.get(key) != value:
            style[key] = value
            changed.append(f"{key}={value!r}")
    if not changed:
        return f"{name}: 변경 없음"
    ledgermod.save(project, data)
    _INJECT_CACHE.clear()
    return f"{name}: " + ", ".join(changed)


def render_text_layer(project: Project, relative: str,
                      style_overrides: dict | None = None) -> bytes | None:
    """글자 레이어만 — 투명 캔버스에 스펙을 그린 PNG (겹침 보기용).

    베이스를 합성하지 않으므로 원본 위에 얹으면 원문과 한글 잉크가
    같이 보인다. 베이스 픽셀을 직접 만지는 alpha_clear 행은 표현할 수
    없어 건너뛴다."""
    from io import BytesIO

    from PIL import Image

    from . import render as rendermod

    cache_key = _inject_cache_key(project, relative)
    if not style_overrides:
        cached = _INJECT_CACHE.get((relative, "text"))
        if cached and cached[0] == cache_key:
            return cached[1]

    data = ledgermod.load(project)
    styles = ledgermod.styles_map(data)
    for name, edits in (style_overrides or {}).items():
        if name in styles:
            patched = dict(styles[name])
            for key, raw in edits.items():
                value = style_coerce(raw)
                if value is None:
                    patched.pop(key, None)
                else:
                    patched[key] = value
            styles[name] = patched
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
    size = None
    for root in (project.base_root, project.original_root):
        path = _safe_join(root, relative)
        if path and path.exists():
            with Image.open(path) as im:
                size = im.size
            break
    if size is None:                      # blank 캔버스 (text-only)
        c = next((s.canvas for s in specs if s.canvas != (0, 0)), None)
        if c is None:
            return None
        size = c
    ss = project.supersample
    layer_ss = Image.new("RGBA", (size[0] * ss, size[1] * ss), (0, 0, 0, 0))
    fonts = rendermod.FontCache(project)
    from collections import defaultdict
    runs = defaultdict(list)
    for spec in specs:
        if spec.effect == "alpha_clear":
            continue
        if spec.run_id:
            runs[spec.run_id].append(spec)
        else:
            rendermod.render_single(layer_ss, rendermod.scale_spec(spec, ss),
                                    fonts)
    for members in runs.values():
        rendermod.render_run(
            layer_ss, [rendermod.scale_spec(m, ss) for m in members], fonts)
    layer = layer_ss if ss == 1 else \
        layer_ss.resize(size, Image.Resampling.LANCZOS)
    buffer = BytesIO()
    layer.save(buffer, "PNG")
    body = buffer.getvalue()
    if not style_overrides:
        _INJECT_CACHE[(relative, "text")] = (cache_key, body)
    return body


def render_injected(project: Project, relative: str,
                    style_overrides: dict | None = None) -> bytes | None:
    """원장 **현재 상태**로 즉석 합성한 injected 미리보기 PNG.

    status 와 무관하게 ko(용어표 해석 포함)가 있고 스타일이 갖춰진 행을
    전부 렌더한다 — inject 를 돌리기 전에도 결과를 볼 수 있다.
    합성 불가(스펙 없음·베이스 없음 등)면 None — 호출자가 디스크 산출물로
    폴백한다.

    style_overrides = {스타일명: {키: GUI 입력 문자열}} — 원장에 쓰지 않고
    이번 렌더에만 얹는 라이브 미리보기. 캐시를 거치지 않는다.
    """
    from io import BytesIO

    from . import render as rendermod

    cache_key = _inject_cache_key(project, relative)
    if not style_overrides:
        cached = _INJECT_CACHE.get(relative)
        if cached and cached[0] == cache_key:
            return cached[1]

    data = ledgermod.load(project)
    styles = ledgermod.styles_map(data)
    for name, edits in (style_overrides or {}).items():
        if name not in styles:
            continue
        patched = dict(styles[name])
        for key, raw in edits.items():
            value = style_coerce(raw)
            if value is None:
                patched.pop(key, None)
            else:
                patched[key] = value
        styles[name] = patched
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
        if body is not None and not style_overrides:   # 미리보기는 캐시 안 함
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
    rows = ledgermod.rows(data)
    box_id = _alloc_id({r.get("box_id") for r in rows},
                       ocrmod._id_prefix(relative))
    jp = _read_region(project, relative, rect)
    _, rule = ledgermod.match_rule(data.get("rules", {}), relative)
    rows.append({
        "box_id": box_id, "file": relative, "element_id": None,
        "run_id": None, "jp": jp, "ko": "", "ocr_id": None,
        "crop": None, "text": list(rect), "source": list(rect),
        "canvas": None, "pad": None, "style": rule.get("style", ""),
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
            row[key] = rect          # crop 도 text/source 와 같은 평면 박스
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


def box_set_style(project: Project, relative: str, box_id: str,
                  style_name: str) -> str:
    """행의 스타일 지정 변경 (GUI 드롭다운). 빈 문자열 = 지정 해제.

    text-only 항목이면 entry 에 override 로 얹힌다 (묶음 기본은 그대로)."""
    style_name = (style_name or "").strip()
    data = ledgermod.load(project)
    if style_name and style_name not in {s["name"] for s in data.get("styles", [])}:
        raise ValueError(f"원장에 없는 스타일: {style_name!r}")
    for row in ledgermod.rows(data):
        if row.get("box_id") == box_id and row.get("file") == relative:
            row["style"] = style_name
            ledgermod.save(project, data)
            _INJECT_CACHE.pop(relative, None)
            return f"{box_id}.style = {style_name or '(없음)'}"
    for cat in ledgermod.catalogs(data):
        for fname, entry in (cat.get("entries") or {}).items():
            if f"{cat['name']}:{fname.split('.')[0]}" == box_id:
                if style_name and style_name != cat.get("style"):
                    entry["style"] = style_name
                else:
                    entry.pop("style", None)   # 묶음 기본과 같으면 override 제거
                ledgermod.save(project, data)
                _INJECT_CACHE.pop(relative, None)
                return f"{box_id}.style = {style_name or cat.get('style', '')}" \
                       + ("" if style_name != cat.get("style") else " (묶음 기본)")
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
    crop_l, crop_r = halves(ledgermod.crop_rect(row) or None)
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
        part["crop"] = crop or None
        part["jp"] = _read_region(project, relative, source or text) or row.get("jp", "")
        part["ko"] = ""
        part["notes"] = "manual split"
        parts.append(part)
    rows[index:index + 1] = parts
    ledgermod.save(project, data)
    _INJECT_CACHE.pop(relative, None)
    return (f"{box_id} → {parts[0]['box_id']}({parts[0]['jp'] or '—'}) + "
            f"{parts[1]['box_id']}({parts[1]['jp'] or '—'})")


def drop_file(project: Project, relative: str) -> str:
    """파일 제외 (jaguk drop 과 동일) — ignore 규칙 + 행·항목 즉시 제거."""
    from contextlib import redirect_stdout
    from io import StringIO

    from . import jaguk as jagukmod
    data = ledgermod.load(project)
    rules = data.setdefault("rules", {})
    rules[relative] = {"mode": "ignore"}
    buffer = StringIO()
    with redirect_stdout(buffer):
        jagukmod.apply_rule(project, data, relative, rules[relative])
    ledgermod.save(project, data)
    _INJECT_CACHE.pop(relative, None)
    return f"제외: {relative}\n" + buffer.getvalue()


def make_handler(project: Project, config_path: Path | None = None):
    state = {"project": project,
             "mtime": config_path.stat().st_mtime_ns if config_path else None}

    def current() -> Project:
        # 설정 파일이 바뀌면 재로드 — 서버 재시작 없이 폰트·경로 변경 반영.
        # 원장·이미지는 원래 요청마다 읽으므로 이걸로 GUI 전체가 동적이 된다.
        if config_path is not None:
            try:
                mtime = config_path.stat().st_mtime_ns
            except OSError:
                return state["project"]
            if mtime != state["mtime"]:
                from .config import load_path
                state["project"] = load_path(config_path)
                state["mtime"] = mtime
                _INJECT_CACHE.clear()
        return state["project"]

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
            project = current()
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
                        # 항상 원장 현재 상태로 즉석 렌더 — 디스크 산출물은
                        # 안 본다 (jaguk inject 결과가 낡았어도 GUI 는 지금
                        # 데이터를 보여준다). 실패는 404 로 정직하게.
                        # ?styles={스타일명:{키:값}} = 저장 전 라이브 미리보기
                        # ?layer=text = 글자 레이어만 (겹침 보기)
                        query = parse_qs(url.query)
                        overrides = None
                        raw = query.get("styles", [""])[0]
                        if raw:
                            overrides = json.loads(raw)
                        if query.get("layer", [""])[0] == "text":
                            body = render_text_layer(project, relative,
                                                     overrides)
                        else:
                            body = render_injected(project, relative, overrides)
                        if body is None:
                            self.send_error(404)
                            return
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
            except ConnectionError:
                pass    # 새로고침 등으로 브라우저가 끊음 — 정상 (10053/10054)
            except Exception as error:          # 페이지가 오류를 볼 수 있게
                try:
                    self._json({"error": str(error)}, status=500)
                except ConnectionError:
                    pass
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
            project = current()
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
                elif path == "/api/box/style":
                    self._json({"log": box_set_style(
                        project, relative, box_id,
                        self._body().get("style", ""))})
                elif path == "/api/drop":
                    self._json({"log": drop_file(project, relative)})
                elif path == "/api/style/update":
                    body = self._body()
                    self._json({"log": style_update(
                        project, body.get("name", ""),
                        body.get("updates") or {})})
                else:
                    self.send_error(404)
            except ConnectionError:
                pass    # 브라우저가 끊음 — 정상
            except Exception as error:
                try:
                    self._json({"error": str(error)}, status=500)
                except ConnectionError:
                    pass

    return Handler


def run(project: Project, port: int = 52485, open_browser: bool = True,
        config_path: Path | None = None) -> int:
    server = ThreadingHTTPServer(("127.0.0.1", port),
                                 make_handler(project, config_path))
    url = f"http://127.0.0.1:{port}/"
    print(f"jaguk gui: {url}  (프로젝트 {project.root}, Ctrl+C 로 종료)")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n종료")
    return 0
