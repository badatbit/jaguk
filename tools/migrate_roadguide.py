# -*- coding: utf-8 -*-
"""furaiki3 방면 안내판(roadguidesign) → typelet 프로젝트 이관.

furaiki3-l10n 의 실측 레이아웃(roadguide_signs.json)을 typelet 행으로 펼친다.
좌표는 OCR 로 다시 읽지 않는다 — probe 실측으로 실기 검증된 데이터가 원본이다.

행 구성 (행선지 1개 = 행 1개):
    jp      한자 지명 — 번역 키 (ko 는 비워 두고 terms 가 해석)
    source  한자 상자 — 앵커 근거 (화면에 유지되는 원문)
    crop    지울 로마자(--row=kanji 면 한자) 밴드 조각 → erase --method fill
    text    한글 자리 — 한자 중심 앵커, 이웃·화살표·패널 여백으로 폭 제한

번역은 terms 로 roadguide_ko.json 을 참조한다 — 지명 222종이 1,745행에
반복되므로 행에 굽지 않는다 (수정이 한 곳에서 전판 반영).

사용:
    typelet init <프로젝트> 후
    python tools/migrate_roadguide.py --project <프로젝트> \
        --l10n <furaiki3-l10n 레포> [--row romaji|kanji]
    이어서 프로젝트에서:
    typelet erase --method fill --color '#0a579d' --pad 0
    typelet render
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from PIL import ImageFont

# roadguide_probe 실측 레이아웃 상수 (furaiki3-l10n roadguide.py 와 동일)
SLOTS = {"left": (1, 237), "straight": (240, 476), "right": (479, 715)}
PANEL_PAD = 6            # 패널 테두리 안쪽 여백
GAP_PAD = 4              # 행선지 사이 확보할 최소 간격의 절반
CANVAS = [768, 63]
# 줄별 (지울 밴드 y0,y1) / (그릴 영역 y0,y1) / 글꼴 크기 / 기준선 y
BANDS = {"kanji": ((13, 36), (15, 33)), "romaji": ((36, 51), (38, 49))}
FONT_SIZE = {"kanji": 18, "romaji": 12}
BASELINE = {"kanji": 31, "romaji": 47}
MIN_SQUEEZE = 0.72
FILL_COLOR = "#0a579d"   # 판 파랑 — erase --method fill --color 에 쓴다


def avail_x(panel: dict, slot: str, i: int) -> tuple[float, float]:
    """행선지 i가 쓸 수 있는 x 범위 — 화살표·이웃·패널 테두리로 제한."""
    px0, px1 = SLOTS[slot]
    ds = panel["dests"]
    ax0, ax1 = panel["arrow_x"]
    lo, hi = px0 + PANEL_PAD, px1 - PANEL_PAD
    if slot in ("left", "straight"):
        lo = max(lo, px0 + ax1 + GAP_PAD * 2)
    else:
        hi = min(hi, px0 + ax0 - GAP_PAD * 2)

    def mid(a, b):
        return px0 + (ds[a]["box"]["x1"] + ds[b]["box"]["x0"]) / 2
    if i > 0:
        lo = max(lo, mid(i - 1, i) + GAP_PAD)
    if i + 1 < len(ds):
        hi = min(hi, mid(i, i + 1) - GAP_PAD)
    return lo, hi


def style_offset_y(font_path: Path, row: str) -> int:
    """mm 정렬의 메트릭 baseline 을 원 렌더러의 고정 baseline 에 맞추는 보정."""
    font = ImageFont.truetype(str(font_path), FONT_SIZE[row])
    ascent, descent = font.getmetrics()
    _, (y0, y1) = BANDS[row]
    mm_baseline = (y0 + y1) / 2 + (ascent - descent) / 2
    return round(BASELINE[row] - mm_baseline)


def build_rows(layout: dict, row: str) -> list[dict]:
    erase_band, draw_band = BANDS[row]
    rows = []
    n = 0
    for name in sorted(layout["signs"]):
        rec = layout["signs"][name]
        for slot, panel in rec.items():
            px0 = SLOTS[slot][0]
            for i, dest in enumerate(panel["dests"]):
                lo, hi = avail_x(panel, slot, i)
                anchor = dest.get("kanji") or dest["box"]
                cx = px0 + (anchor["x0"] + anchor["x1"]) / 2
                cx = min(max(cx, lo + 1), hi - 1)
                maxw = 2 * min(cx - lo, hi - cx)
                box = dest["box"]
                kanji = dest.get("kanji") or box
                n += 1
                rows.append({
                    "box_id": f"rg{n}",
                    "file": f"parts/roadguidesign/{name}.tga.png",
                    "element_id": f"{name}/{slot}/{i}",
                    "run_id": None,
                    "jp": dest["jp"],
                    "ko": "",                   # terms(roadguide_ko.json) 가 해석
                    "ocr_id": None,
                    "crop": {"id": None, "src": "실측",
                             "rect": [px0 + box["x0"] - 2, erase_band[0],
                                      box["x1"] - box["x0"] + 4,
                                      erase_band[1] - erase_band[0]]},
                    "text": [round(cx - maxw / 2), draw_band[0], round(maxw),
                             draw_band[1] - draw_band[0]],
                    "source": [px0 + kanji["x0"], BANDS["kanji"][1][0],
                               kanji["x1"] - kanji["x0"],
                               BANDS["kanji"][1][1] - BANDS["kanji"][1][0]],
                    "canvas": list(CANVAS),
                    "pad": None,
                    "style": "roadguide",
                    "opacity": "FF",
                    "status": "render_ready",
                    "overflow": "squeeze",
                    "notes": None,
                })
    return rows


def _portable_path(target: Path, project: Path) -> str:
    """프로젝트 기준 상대 경로 — 드라이브가 다르면 절대 경로로 둔다."""
    try:
        return os.path.relpath(target, project).replace("\\", "/")
    except ValueError:
        return str(target).replace("\\", "/")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project", required=True, help="typelet 프로젝트 루트")
    parser.add_argument("--l10n", required=True,
                        help="furaiki3-l10n 레포 루트 (roadguide 데이터 소스)")
    parser.add_argument("--row", choices=("romaji", "kanji"), default="romaji")
    args = parser.parse_args()

    project = Path(args.project).resolve()
    l10n = Path(args.l10n).resolve()
    config_path = project / "typelet.config.json"
    if not config_path.exists():
        sys.exit(f"typelet 프로젝트가 아닙니다 (먼저 typelet init): {project}")

    layout = json.loads(
        (l10n / "translation/images/roadguide_signs.json").read_text(encoding="utf-8"))
    komap_path = l10n / "translation/images/roadguide_ko.json"
    font_path = l10n / "tools/fonts/IBMPlexSansKR-SemiBold.otf"

    config = json.loads(config_path.read_text(encoding="utf-8"))
    config.setdefault("fonts", {})["IBM Plex Sans KR/600"] = str(font_path)
    config_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    rows = build_rows(layout, args.row)
    data = {
        "styles": [{
            "name": "roadguide",
            "label": f"방면 안내판 {args.row} 줄",
            "font_family_ko": "IBM Plex Sans KR",
            "font_weight": 600,
            "font_size_px": FONT_SIZE[args.row],
            "fill_rgb": "#FFFFFF",
            "outline_rgb": "",
            "outline_weight_px": 0,
            "effect": "",
            "font_style": "regular",
            "text_align": "mm",
            "offset_y": style_offset_y(font_path, args.row),
            "squeeze_min": MIN_SQUEEZE,
        }],
        "rows": rows,
        "terms": [_portable_path(komap_path, project)],
    }
    (project / "lettering.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    signs = {r["file"] for r in rows}
    print(f"판 {len(signs)}장 / 행 {len(rows)}개 -> {project / 'lettering.json'}")
    print(f"다음: originals/parts/roadguidesign 에 원본을 두고")
    print(f"  typelet erase --method fill --color '{FILL_COLOR}' --pad 0")
    print(f"  typelet render")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
