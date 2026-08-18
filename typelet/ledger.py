# -*- coding: utf-8 -*-
"""원장(lettering.json) 입출력 — furaiki3-l10n 의 image_text.json 과 같은 스키마.

## 파일이 원본이다
원장 하나에 **스타일과 행**이 같이 들어 있다.

## 행 스키마 (JSON)
    box_id      고유 id (`pa226` — 이미지 약칭+번호, 그림에도 이 이름으로 적힌다)
    file        원본 트리 하위 경로 (`parts/parts2.png` 처럼 / 구분)
    element_id  외부 세대의 요소 이름 (참고용)
    run_id      같은 값끼리 한 상자를 이어 그리는 한 줄 (null = 단독)
    jp / ko     원문 / 번역문. **ko 의 앞뒤 공백은 내용이다**
    ocr_id      원문 글자 범위 상자의 id
    crop        {"id", "src", "rect":[x,y,w,h]} | null — 스프라이트 조각 범위
    text        [x,y,w,h] — 번역문을 앉힐 상자. **좌상단 기준**, 상자 안 정렬은
                스타일의 text_align 이 정한다
    source      [x,y,w,h] — 원문 글자가 있던 범위 (ocr-box)
    canvas      [w,h] — 캔버스 크기 (검증용)
    pad         {"l","t","r","b"} 중 있는 것만
    style       styles 의 name
    opacity     두 자리 hex — 그 자리가 알파를 얼마나 먹었나 (기본 "FF")
    status      todo 흐름 (render_ready 만 렌더된다) · no_inject = 주입 안 함
    flow        [[x,y,w,h], …] — 여러 줄 상자 (어절 단위 자동 줄바꿈)
    notes       사람 메모

## post (선택, 최상위 키)
파일 단위 후처리 목록: {"file", "op", …인자, "note"}. 현재 op 는 overlay
(무문자 트리의 RGBA 레이어를 글자 위에 알파 합성 — render.apply_post) 하나.

## catalogs (선택, 최상위 키) — 텍스트만 달랑 있는 이미지 묶음
saveloadspotname 처럼 이미지 전체가 글자 하나인 파일 무리는 행 대신
카탈로그로 묶는다 — 공통 속성을 한 번만 선언하고 항목은 파일명→번역만 든다:

    {"name":  카탈로그 이름 (box_id 접두, status 집계 단위),
     "dir":   원본 트리 하위 디렉토리 ("parts/saveloadspotname"),
     "canvas": [w, h] 또는 "original" (원본 이미지 크기 — 장마다 다를 때),
     "base":  "blank" (기본) — base 파일 없이 투명 캔버스에서 렌더.
              "file" 이면 일반 행처럼 base 트리를 읽는다,
     "style": 공통 스타일 이름, "text": 공통 text 상자,
     "overflow": "squeeze" — 넘치면 가로만 압축 (크기·높이 불변),
     "fit":   "original-body" — 크기·테두리·자리를 원장에 굽지 않고 **원본을
              실측해 재현**한다 (touringspotname 규칙: 글자 몸통 높이에 맞는
              최대 크기, 테두리 폭은 잉크 여백에서, 가로 중앙·세로는 원본
              몸통 상단). 원본 이미지가 필요하다. text 상자·canvas 생략 가능,
     "entries": {"slsn00001.tga.png": {"jp","ko","status", …개별 override}}}

entries 의 개별 항목은 canvas·text·style·opacity·overflow·fit 을 override 할
수 있다.

로드 시 expand_catalogs() 가 항목을 가상 행으로 펼친다 (box_id =
"이름:파일stem"). **가상 행은 원장 파일에 저장되지 않는다** — entries 가
원본이다. flat_rows() 는 일반 행 + 카탈로그 전개를 함께 돌려준다.

## 스타일 스키마
name, label, font_family_ko, font_weight, font_size_px, fill_rgb(#RRGGBB),
outline_rgb, outline_weight_px, effect, font_style,
text_align(가로 l/m/r + 세로 t/m/b), orientation("vertical"=세로쓰기),
distribute(글자 균등 분배), offset_x/offset_y(그리기 원점 이동 px).

## flat_rows()
평면 문자열 dict 를 돌려준다 — 렌더·그림 코드가 쓰는 호환층.
"""

from __future__ import annotations

import json

from .config import Project

FLAT_KEYS = (
    "box_id", "file", "element_id", "run_id", "jp_text", "ko_text",
    "ocr_id", "crop_id", "crop_src", "crop_x", "crop_y", "crop_w", "crop_h",
    "text_x", "text_y", "text_w", "text_h", "canvas_w", "canvas_h",
    "source_x", "source_y", "source_box_w", "source_box_h",
    "pad_l", "pad_t", "pad_r", "pad_b", "style", "opacity", "status", "notes",
)


def load(project: Project) -> dict:
    return json.loads(project.ledger_path.read_text(encoding="utf-8"))


def save(project: Project, data: dict) -> None:
    text = json.dumps(data, ensure_ascii=False, indent=1)
    project.ledger_path.write_text(text + "\n", encoding="utf-8")


def styles_map(data: dict) -> dict[str, dict]:
    return {s["name"]: s for s in data["styles"]}


def rows(data: dict) -> list[dict]:
    return data["rows"]


def catalogs(data: dict) -> list[dict]:
    return data.get("catalogs") or []


def expand_catalogs(data: dict) -> list[dict]:
    """카탈로그 항목 → 가상 행. 저장 대상이 아니다 — entries 가 원본이다."""
    out = []
    for cat in catalogs(data):
        name = cat["name"]
        directory = (cat.get("dir") or "").strip("/")
        base = cat.get("base", "blank")
        if base not in ("blank", "file"):
            raise ValueError(f"카탈로그 {name}: base 는 blank|file 이다: {base!r}")
        for fname, e in (cat.get("entries") or {}).items():
            stem = fname.split(".")[0]
            canvas = e.get("canvas", cat.get("canvas"))
            if canvas == "original":
                canvas = None       # 렌더러가 원본 크기를 쓴다 (fit 류)
            out.append({
                "box_id": f"{name}:{stem}",
                "file": f"{directory}/{fname}" if directory else fname,
                "element_id": None,
                "run_id": None,
                "jp": e.get("jp", ""),
                "ko": e.get("ko", ""),
                "ocr_id": None,
                "crop": None,
                "text": e.get("text", cat.get("text")),
                "source": None,
                "canvas": canvas,
                "pad": None,
                "style": e.get("style", cat.get("style", "")),
                "opacity": e.get("opacity", "FF"),
                "status": e.get("status", "todo"),
                "notes": e.get("notes"),
                "base": base,
                "overflow": e.get("overflow", cat.get("overflow", "")),
                "fit": e.get("fit", cat.get("fit", "")),
                "catalog": name,
            })
    return out


def _s(v) -> str:
    return "" if v is None else str(v)


def flatten_row(r: dict) -> dict:
    """구조형 행 → 평면 문자열 dict."""
    crop = r.get("crop") or {}
    rect = crop.get("rect") or [None] * 4
    text = r.get("text") or [None] * 4
    src = r.get("source") or [None] * 4
    canvas = r.get("canvas") or [None, None]
    pad = r.get("pad") or {}
    return {
        "box_id": _s(r.get("box_id")),
        "file": _s(r.get("file")),
        "element_id": _s(r.get("element_id")),
        "run_id": _s(r.get("run_id")),
        "jp_text": _s(r.get("jp")),
        "ko_text": _s(r.get("ko")),
        "ocr_id": _s(r.get("ocr_id")),
        "crop_id": _s(crop.get("id")),
        "crop_src": _s(crop.get("src")),
        "crop_x": _s(rect[0]), "crop_y": _s(rect[1]),
        "crop_w": _s(rect[2]), "crop_h": _s(rect[3]),
        "text_x": _s(text[0]), "text_y": _s(text[1]),
        "text_w": _s(text[2]), "text_h": _s(text[3]),
        "canvas_w": _s(canvas[0]), "canvas_h": _s(canvas[1]),
        "source_x": _s(src[0]), "source_y": _s(src[1]),
        "source_box_w": _s(src[2]), "source_box_h": _s(src[3]),
        "pad_l": _s(pad.get("l")), "pad_t": _s(pad.get("t")),
        "pad_r": _s(pad.get("r")), "pad_b": _s(pad.get("b")),
        "style": _s(r.get("style")),
        "opacity": _s(r.get("opacity")),
        "status": _s(r.get("status")),
        "notes": _s(r.get("notes")),
        "base": _s(r.get("base")),
        "overflow": _s(r.get("overflow")),
        "fit": _s(r.get("fit")),
        "catalog": _s(r.get("catalog")),
    }


def flat_rows(data: dict) -> list[dict]:
    """일반 행 + 카탈로그 전개 — 렌더·그림이 보는 전체 목록."""
    return [flatten_row(r) for r in rows(data) + expand_catalogs(data)]
