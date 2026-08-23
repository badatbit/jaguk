# -*- coding: utf-8 -*-
"""jaguk gui — 원장 검수용 로컬 웹 툴 (표준 라이브러리만 사용).

왼쪽: text-only 묶음 그룹(saveloadspotname 류는 항목 리스트로) + 행이 있는 파일 목록
가운데: 이미지 뷰 — 원본 / erased / injected / crop-box / text-box
오른쪽: 선택한 상자의 속성 — inline(행/entry) → catalog 기본값 → style →
        상위 style(style 의 "base" 사슬)을 아래로 계속 표시

읽기 전용이다 — 원장 수정은 파일/CLI 로 한다. 서버는 localhost 전용.
"""

from __future__ import annotations

import io
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
    # overlay 그룹은 논리적 — 규칙의 base/members(실제 아카이브 경로)로 소속을
    # 판정한다. base 파일은 여러 그룹이 공유할 수 있어 양쪽에 다 뜬다.
    overlay_defs = ledgermod.overlay_groups(data)
    member_to_groups: dict[str, list] = {}
    for name, base, members in overlay_defs:
        for rel in ([base] + list(members)):
            if rel:
                member_to_groups.setdefault(rel, []).append(name)
    overlay_names = {name for name, _, _ in overlay_defs}

    grouped: dict[str, list] = {}
    loose = []
    for relative, count in sorted(by_file.items()):
        entry = {"file": relative, "rows": count}
        gnames = member_to_groups.get(relative)
        if gnames:                                  # overlay 그룹 멤버/베이스
            for gname in gnames:
                grouped.setdefault(gname, []).append(entry)
            continue
        rule_path, rule = ledgermod.match_rule(rules_map, relative)
        if (rule_path and rule_path != relative      # 디렉토리(접두) 규칙만 묶음
                and ledgermod.rule_mode(rule) != "overlay"):
            grouped.setdefault(rule_path, []).append(entry)
        else:
            loose.append(entry)
    groups = []
    for path, entries in sorted(grouped.items()):
        mode = ("overlay" if path in overlay_names
                else ledgermod.rule_mode(rules_map.get(path, {})))
        groups.append({"name": path, "mode": mode,
                       "count": len(entries), "files": entries})
    return {"catalogs": catalogs, "groups": groups, "files": loose,
            "rules": rules_map}


def overlay_group(project: Project, data: dict, relative: str,
                  group_name: str | None = None):
    """relative 이 overlay 그룹의 base/member 면 (그룹명, base_rel, [오버레이…]).

    그룹은 **논리적**이다 — 규칙의 base·members(실제 아카이브 경로)로 소속을
    판정한다. 보여줄 오버레이는 **연 파일**이 정한다:
      - base 를 열면 → 그룹 전체 오버레이 합성 (한 장 미리보기)
      - 특정 멤버를 열면 → **그 멤버 하나만** base 위에 올린다 (집중 편집)
    """
    grp = ledgermod.overlay_group_for(data, relative, prefer=group_name)
    if not grp:
        return None
    name, base_rel, members = grp
    all_overlays = [m for m in members if m and m != base_rel]
    if (relative or "").replace("\\", "/") == base_rel:
        overlays = all_overlays               # base = 그룹 전체
    else:
        overlays = [(relative or "").replace("\\", "/")]   # 멤버 = 그것만
    return name, base_rel, overlays


def composite_group(project: Project, base_rel: str, overlays: list,
                    roots: list | None = None) -> bytes | None:
    """base + 오버레이들을 합성한 PNG 바이트 (그룹 = 한 장).

    roots: 각 파일을 찾을 루트 우선순위. 기본 [originals]. erased 뷰는
    [erased, originals] 로 줘서 지운 타일(빨간 마커 포함)을 우선 얹는다.
    """
    roots = roots or [project.original_root]

    def find(rel):
        for r in roots:
            p = _safe_join(r, rel)
            if p and p.exists():
                return p
        return None

    base_path = find(base_rel) if base_rel else None
    canvas = Image.open(base_path).convert("RGBA") if base_path else None
    for rel in overlays:
        p = find(rel)
        if not p:
            continue
        layer = Image.open(p).convert("RGBA")
        if canvas is None:
            canvas = Image.new("RGBA", layer.size, (0, 0, 0, 0))
        canvas.alpha_composite(layer)
    if canvas is None:
        return None
    buf = io.BytesIO()
    canvas.convert("RGB").save(buf, "PNG")
    return buf.getvalue()


def build_detail(project: Project, relative: str,
                 group: str | None = None) -> dict:
    data = ledgermod.load(project)
    terms = ledgermod.load_terms(project, data)
    # overlay 그룹이면 그룹 전체 파일의 행을 한 장에 모은다 (blit 이 화면
    # 좌표라 합성본 위에 그대로 얹힌다). group 힌트는 공유 base 가 어느 그룹으로
    # 열릴지 정한다 (g1·g2 가 base 를 공유해도 클릭한 그룹으로).
    grp = overlay_group(project, data, relative, group_name=group)
    grp_files = (set(grp[2]) | {grp[1]}) if grp else {relative}
    flat = [r for r in ledgermod.flat_rows(data) if r["file"] in grp_files]
    if terms:
        ledgermod.apply_terms(flat, terms)
    raw_rows = {r["box_id"]: r for r in ledgermod.rows(data)
                if r.get("file") in grp_files}

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
    # 설정에 등록된 글꼴 → {패밀리: [weight...]} (GUI 스타일 드롭다운용).
    # 키는 "패밀리/weight" 꼴이라 마지막 '/' 로 가른다.
    fonts: dict[str, list[str]] = {}
    for key in (project.fonts or {}):
        family, sep, weight = str(key).rpartition("/")
        if not sep:
            family, weight = key, ""
        fonts.setdefault(family, [])
        if weight and weight not in fonts[family]:
            fonts[family].append(weight)
    for fam in fonts:
        fonts[fam].sort()
    images = {}
    for kind, get_root in IMAGE_ROOTS.items():
        target = _safe_join(get_root(project), relative)
        images[kind] = bool(target and target.exists())
    # injected 는 즉석 렌더 — ko(용어표 해석 포함)가 있는 행이 하나라도 있으면
    # 디스크 산출물 없이도 미리보기가 가능하다
    # erased = erased 디렉토리에 같은 파일이 있으면 그것, 없으면 base(원본).
    # config·blank·그룹 규칙에 의존하지 않는다 — 원본만 있어도 erased 뷰는
    # 열리고, 서빙 때 원본으로 폴백한다.
    images["erased"] = images["erased"] or images["original"]
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
        "group": grp[0] if grp else None,   # 해석된 overlay 그룹명 (공유 base 구분)
        "size": size,
        "recompose": recompose_spec,
        "post": posts,
        "rows": flat,                    # 평면 행 (text-only 전개 포함)
        "raw": raw_rows,                 # 일반 행의 구조형 (inline 속성 표시용)
        "catalog": catalog,
        "entry": entry,
        "rule": rule,
        "styles": styles,
        "fonts": fonts,
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


def style_add(project: Project, name: str, base: str = "") -> str:
    """새 스타일 추가 (GUI). 같은 이름이 있으면 오류. base 는 상속할 스타일명."""
    name = (name or "").strip()
    if not name:
        raise ValueError("스타일 이름을 입력하세요")
    data = ledgermod.load(project)
    styles = data.setdefault("styles", [])
    if any(s.get("name") == name for s in styles):
        raise ValueError(f"이미 있는 스타일: {name!r}")
    style = {"name": name}
    base = (base or "").strip()
    if base:
        if base not in {s.get("name") for s in styles}:
            raise ValueError(f"상속할 스타일이 없음: {base!r}")
        style["base"] = base
    else:
        # base 가 없는 새 스타일은 기본값으로 채운다 — 설정에 IBM Plex Sans KR
        # 이 있으면 그 첫 weight 를, 없으면 패밀리명만.
        fam = "IBM Plex Sans KR"
        weight = ""
        for key in (project.fonts or {}):
            f, _, w = str(key).rpartition("/")
            if f == fam:
                weight = w
                break
        style.update({
            "font_family_ko": fam,
            "font_weight": weight or "400",
            "font_size_px": "24",
            "text_align": "mm",
            "fill_rgb": "#000000",
        })
    styles.append(style)
    ledgermod.save(project, data)
    return f"스타일 추가: {name}" + (f" (base={base})" if base else "")


def box_set_ko(project: Project, box_id: str, ko: str) -> str:
    """행/entry 의 번역문(ko) 저장. box_id 로 찾는다(파일별 접두라 유니크).
    ko 가 차면 status 를 render_ready, 비면 todo 로 되돌린다.
    """
    data = ledgermod.load(project)
    for row in ledgermod.rows(data):
        if row.get("box_id") == box_id:
            row["ko"] = ko
            if (ko or "").strip():
                if row.get("status") in (None, "", "todo"):
                    row["status"] = "render_ready"
            elif row.get("status") == "render_ready":
                row["status"] = "todo"
            ledgermod.save(project, data)
            _INJECT_CACHE.pop(row.get("file"), None)
            return f"{box_id}.ko = {ko!r}"
    for cat in ledgermod.catalogs(data):
        for fname, entry in (cat.get("entries") or {}).items():
            if f"{cat['name']}:{fname.split('.')[0]}" == box_id:
                entry["ko"] = ko
                if (ko or "").strip():
                    entry["status"] = "render_ready"
                ledgermod.save(project, data)
                _INJECT_CACHE.clear()
                return f"{box_id}.ko = {ko!r}"
    raise ValueError(f"행 없음: {box_id}")


def box_set_jp(project: Project, box_id: str, jp: str) -> str:
    """행/entry 의 원문(jp) 수정 (OCR 오독 정정). box_id 로 찾는다."""
    data = ledgermod.load(project)
    for row in ledgermod.rows(data):
        if row.get("box_id") == box_id:
            row["jp"] = jp
            ledgermod.save(project, data)
            _INJECT_CACHE.pop(row.get("file"), None)
            return f"{box_id}.jp = {jp!r}"
    for cat in ledgermod.catalogs(data):
        for fname, entry in (cat.get("entries") or {}).items():
            if f"{cat['name']}:{fname.split('.')[0]}" == box_id:
                entry["jp"] = jp
                ledgermod.save(project, data)
                return f"{box_id}.jp = {jp!r}"
    raise ValueError(f"행 없음: {box_id}")


def box_set_angle(project: Project, box_id: str, angle: float) -> str:
    """상자 틸트(회전) 각도 저장 (도). 0 이면 필드 제거."""
    angle = round(float(angle), 2)
    data = ledgermod.load(project)
    for row in ledgermod.rows(data):
        if row.get("box_id") == box_id:
            if angle:
                row["angle"] = angle
            else:
                row.pop("angle", None)
            ledgermod.save(project, data)
            _INJECT_CACHE.pop(row.get("file"), None)
            return f"{box_id}.angle = {angle}°"
    raise ValueError(f"행 없음: {box_id}")


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
        if row.get("box_id") == box_id:      # box_id 유니크 (그룹 뷰 대응)
            _INJECT_CACHE.pop(row.get("file"), None)
            del rows[i]
            ledgermod.save(project, data)
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


def box_add(project: Project, relative: str, rect: list,
            sync: bool = False) -> str:
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
        # 동기화가 켜져 있으면 crop 도 처음부터 채운다 — 추가 후 편집 때
        # text-box→crop-box 로 갑자기 바뀌는 혼란을 없앤다
        "crop": list(rect) if sync else None,
        "text": list(rect), "source": list(rect),
        "canvas": None, "pad": None, "style": rule.get("style", ""),
        "opacity": "FF", "status": "todo", "notes": "manual",
    })
    ledgermod.save(project, data)
    _INJECT_CACHE.pop(relative, None)
    return f"행 {box_id} 추가 (jp 판독: {jp or '—'})"


def box_update(project: Project, relative: str, box_id: str,
               key: str, rect: list, sync: bool = False) -> str:
    """상자 크기/위치 갱신 (GUI 리사이즈 핸들). key = text|crop|source.

    sync=True 면 crop·text·source 를 한 상자로 묶어 같이 옮긴다 (동기화 옵션).

    slot 참조 행에 쓰면 행 자체에 override 가 생긴다 — 그 행만 규칙과
    달라진다는 뜻이며, flat 해석에서 행 값이 우선한다."""
    if key not in ("text", "crop", "source"):
        raise ValueError(f"모르는 상자 종류: {key}")
    rect = [int(v) for v in rect]
    if rect[2] < 2 or rect[3] < 2:
        raise ValueError(f"상자가 너무 작습니다: {rect}")
    data = ledgermod.load(project)
    for row in ledgermod.rows(data):
        if row.get("box_id") == box_id:      # box_id 유니크 (그룹 뷰 대응)
            if sync:
                for k in ("text", "crop", "source"):
                    row[k] = list(rect)      # 세 상자를 한 몸으로 동기화
            else:
                row[key] = rect              # crop 도 text/source 와 같은 평면 박스
            ledgermod.save(project, data)
            _INJECT_CACHE.pop(row.get("file"), None)
            return f"{box_id}.{key} = {rect}" + (" (동기화)" if sync else "")
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


def box_sync(project: Project, relative: str, scope: str = "file") -> str:
    """행의 text·crop·source 를 한 상자로 맞춘다 (동기화).

    기준 상자는 text(없으면 crop, 없으면 source). scope=file 이면 현재
    파일/overlay 그룹만, all 이면 원장 전체.
    """
    data = ledgermod.load(project)
    targets = None
    if scope != "all":
        grp = overlay_group(project, data, relative)
        targets = (set(grp[2]) | {grp[1]}) if grp else {relative}
    n = 0
    for row in ledgermod.rows(data):
        if targets is not None and row.get("file") not in targets:
            continue
        base = row.get("text") or row.get("crop") or row.get("source")
        if not base:
            continue
        for k in ("text", "crop", "source"):
            row[k] = list(base)
        n += 1
    if n:
        ledgermod.save(project, data)
        _INJECT_CACHE.clear()
    return f"동기화 {n}행 (crop=text=source)"


def box_set_style(project: Project, relative: str, box_id: str,
                  style_name: str) -> str:
    """행의 스타일 지정 변경 (GUI 드롭다운). 빈 문자열 = 지정 해제.

    text-only 항목이면 entry 에 override 로 얹힌다 (묶음 기본은 그대로)."""
    style_name = (style_name or "").strip()
    data = ledgermod.load(project)
    if style_name and style_name not in {s["name"] for s in data.get("styles", [])}:
        raise ValueError(f"원장에 없는 스타일: {style_name!r}")
    for row in ledgermod.rows(data):
        # box_id 는 파일별 접두라 유니크 — file 로 좁히지 않는다. (overlay 그룹은
        # 여러 파일 행을 한 뷰에 모으므로 보고 있는 file 과 행의 file 이 다르다)
        if row.get("box_id") == box_id:
            row["style"] = style_name
            ledgermod.save(project, data)
            _INJECT_CACHE.pop(row.get("file"), None)
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
                       if r.get("box_id") == box_id), None)  # box_id 유니크
    # 영역은 그 행의 파일에서 읽는다 (그룹 뷰: 보고 있는 file 과 다를 수 있음)
    read_file = (target_row or {}).get("file") or relative
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
                     if r["box_id"] == box_id), None)
        if flat is None:
            raise ValueError(f"행을 찾지 못함: {box_id}")
        read_file = flat.get("file") or read_file
        values = [flat.get(f"text_{k}", "") for k in ("x", "y", "w", "h")]
        if all(v != "" for v in values):
            rect = [int(v) for v in values]
    if rect is None:
        raise ValueError("판독할 영역(source/text)이 없습니다")
    jp = _read_region(project, read_file, rect)
    if not jp:
        raise ValueError("판독 결과가 비어 있습니다 (recognizer 설정 확인)")
    vocab = ocrmod.load_ocr_dict(project)
    if vocab:
        fake = [{"file": relative, "lines": [{"text": jp, "x": 0, "y": 0,
                                              "w": 1, "h": 1}]}]
        ocrmod.correct_results(fake, vocab, project.ocr_dict_min)
        jp = fake[0]["lines"][0]["text"]

    for row in ledgermod.rows(data):
        if row.get("box_id") == box_id:      # box_id 유니크 (그룹 뷰 대응)
            old = row.get("jp", "")
            row["jp"] = jp
            note = " (ko 는 보존 — 번역 확인 필요)" if (row.get("ko") or "").strip() else ""
            ledgermod.save(project, data)
            _INJECT_CACHE.pop(row.get("file"), None)
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
                  if r.get("box_id") == box_id),      # box_id 유니크 (그룹 뷰)
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
                    q = parse_qs(url.query)
                    relative = q.get("path", [""])[0]
                    group = q.get("group", [""])[0] or None
                    self._json(build_detail(project, relative, group=group))
                elif path.startswith("/img/"):
                    _, _, kind, relative = path.split("/", 3)
                    body = None
                    group = parse_qs(url.query).get("group", [""])[0] or None
                    # overlay 그룹 = 한 장:
                    #   original = 원본 base + 원본 오버레이(일본어) 합성
                    #   erased   = erased base + erased 오버레이(빨간 마커) 합성
                    #   injected = erased 합성 위에 그룹 전체 ko 주입
                    grp = overlay_group(project, ledgermod.load(project), relative,
                                        group_name=group)
                    if grp:
                        _gk, base_rel, overlays = grp
                        query = parse_qs(url.query)
                        overrides = json.loads(query.get("styles", ["null"])[0]
                                               or "null")
                        if kind == "original":
                            body = composite_group(project, base_rel, overlays)
                        elif kind == "erased":
                            # 지운 타일(빨간 마커) 우선, 없으면 원본으로 폴백
                            body = composite_group(
                                project, base_rel, overlays,
                                roots=[project.base_root, project.original_root])
                        elif kind == "injected":
                            # injected = erased 합성(지운 배경) 위에 ko 주입
                            er_png = composite_group(
                                project, base_rel, overlays,
                                roots=[project.base_root, project.original_root])
                            base_img = Image.open(io.BytesIO(er_png)) \
                                .convert("RGBA") if er_png else None
                            if base_img is not None:
                                # base 에 직접 놓인 행도 렌더한다 — 그룹을 base
                                # 로 열고 추가한 박스가 injected 에 안 나오던 문제
                                for rel in ([base_rel] if base_rel else []) + overlays:
                                    tl = render_text_layer(project, rel, overrides)
                                    if tl:
                                        base_img.alpha_composite(Image.open(
                                            io.BytesIO(tl)).convert("RGBA"))
                                buf = io.BytesIO()
                                base_img.convert("RGB").save(buf, "PNG")
                                body = buf.getvalue()
                        if body is None:
                            self.send_error(404)
                            return
                    if body is None and kind == "injected":
                        # 항상 원장 현재 상태로 즉석 렌더 — 디스크 산출물은
                        # 안 본다. ?styles=… 라이브 미리보기, ?layer=text 글자만
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
                    elif body is None and kind == "erased":
                        # erased 파일이 있으면 그것, 없으면 base(원본) 그대로.
                        disk = _safe_join(IMAGE_ROOTS["erased"](project), relative)
                        if disk and disk.exists():
                            body = disk.read_bytes()
                        else:
                            orig = _safe_join(project.original_root, relative)
                            if orig and orig.exists():
                                body = orig.read_bytes()
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
                    body = self._body()
                    self._json({"log": box_add(project, relative,
                                               body.get("rect"),
                                               bool(body.get("sync")))})
                elif path == "/api/box/split":
                    at = int(self._body().get("at", 0))
                    self._json({"log": box_split(project, relative, box_id, at)})
                elif path == "/api/box/update":
                    body = self._body()
                    self._json({"log": box_update(project, relative, box_id,
                                                  body.get("key", "text"),
                                                  body.get("rect"),
                                                  bool(body.get("sync")))})
                elif path == "/api/box/sync":
                    # 현재 파일(그룹) 행들의 crop·source 를 text 로 맞춘다
                    self._json({"log": box_sync(project, relative,
                                                self._body().get("scope", "file"))})
                elif path == "/api/box/reread":
                    self._json({"log": box_reread(project, relative, box_id)})
                elif path == "/api/box/style":
                    self._json({"log": box_set_style(
                        project, relative, box_id,
                        self._body().get("style", ""))})
                elif path == "/api/box/ko":
                    self._json({"log": box_set_ko(
                        project, box_id, self._body().get("ko", ""))})
                elif path == "/api/box/jp":
                    self._json({"log": box_set_jp(
                        project, box_id, self._body().get("jp", ""))})
                elif path == "/api/box/angle":
                    self._json({"log": box_set_angle(
                        project, box_id, self._body().get("angle", 0))})
                elif path == "/api/drop":
                    self._json({"log": drop_file(project, relative)})
                elif path == "/api/style/update":
                    body = self._body()
                    self._json({"log": style_update(
                        project, body.get("name", ""),
                        body.get("updates") or {})})
                elif path == "/api/style/add":
                    body = self._body()
                    self._json({"log": style_add(
                        project, body.get("name", ""), body.get("base", ""))})
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
