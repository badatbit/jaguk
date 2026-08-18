# -*- coding: utf-8 -*-
"""지우기 — 원본에서 글자 영역을 지워 무문자 베이스를 만든다.

행의 crop.rect(있으면) 또는 source 상자를 pad 만큼 넓혀 마스크로 쓴다.

방식(--method):
    inpaint     OpenCV 인페인트 (TELEA) — RGB 와 알파를 따로 복원. 배경에
                무늬·그라데이션이 있을 때. opencv-python 필요.
    median      상자 둘레 고리의 중앙값 색으로 채움 — 단색 배경이면 충분하고
                의존성이 없다.
    alpha       영역의 알파를 0 으로 — 투명 바탕 스프라이트(글자가 곧 잉크)용.
    fill        영역을 --color 단색으로 채움 — 안내판처럼 판 색이 정해진 경우
                (roadguidesign 의 #0a579d 등).
    auto        cv2 가 있으면 inpaint, 없으면 median. (기본)

결과는 base_root 에 같은 상대 경로로 저장한다. **이미 있는 베이스는 손질본일
수 있으므로 --force 없이는 덮지 않는다** — 자동 지우기는 시작점일 뿐,
마무리는 손이 하는 워크플로를 전제한다.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image

from . import ledger as ledgermod
from .config import Project


def _rects_by_file(data: dict, only: str) -> dict[str, list[tuple[int, int, int, int]]]:
    grouped: dict[str, list[tuple[int, int, int, int]]] = defaultdict(list)
    for r in ledgermod.rows(data):
        relative = r.get("file") or ""
        if only and only.lower() not in relative.lower():
            continue
        if (r.get("status") or "") == "no_inject":
            continue
        crop = r.get("crop") or {}
        rect = crop.get("rect") or r.get("source")
        if rect:
            grouped[relative].append(tuple(int(v) for v in rect))
    return grouped


def build_mask(size: tuple[int, int], rects, pad: int) -> np.ndarray:
    mask = np.zeros((size[1], size[0]), dtype=np.uint8)
    for x, y, w, h in rects:
        x0 = max(0, x - pad)
        y0 = max(0, y - pad)
        x1 = min(size[0], x + w + pad)
        y1 = min(size[1], y + h + pad)
        mask[y0:y1, x0:x1] = 255
    return mask


def erase_inpaint(image: Image.Image, mask: np.ndarray) -> Image.Image:
    import cv2

    arr = np.asarray(image.convert("RGBA"))
    rgb = cv2.inpaint(arr[:, :, :3], mask, 3, cv2.INPAINT_TELEA)
    alpha = cv2.inpaint(arr[:, :, 3], mask, 3, cv2.INPAINT_TELEA)
    return Image.fromarray(np.dstack([rgb, alpha]), "RGBA")


def erase_median(image: Image.Image, rects, pad: int) -> Image.Image:
    """상자 둘레 고리(ring)의 중앙값 색으로 상자를 채운다 — 단색 배경용."""
    arr = np.asarray(image.convert("RGBA")).copy()
    height, width = arr.shape[:2]
    ring = 4
    for x, y, w, h in rects:
        x0 = max(0, x - pad)
        y0 = max(0, y - pad)
        x1 = min(width, x + w + pad)
        y1 = min(height, y + h + pad)
        ox0 = max(0, x0 - ring)
        oy0 = max(0, y0 - ring)
        ox1 = min(width, x1 + ring)
        oy1 = min(height, y1 + ring)
        outer = arr[oy0:oy1, ox0:ox1]
        keep = np.ones(outer.shape[:2], dtype=bool)
        keep[y0 - oy0:y1 - oy0, x0 - ox0:x1 - ox0] = False
        samples = outer[keep]
        if not len(samples):        # 상자가 이미지 전체 — 그냥 둔다
            continue
        arr[y0:y1, x0:x1] = np.median(samples, axis=0).astype(np.uint8)
    return Image.fromarray(arr, "RGBA")


def erase_alpha(image: Image.Image, mask: np.ndarray) -> Image.Image:
    arr = np.asarray(image.convert("RGBA")).copy()
    arr[:, :, 3][mask > 0] = 0
    return Image.fromarray(arr, "RGBA")


def erase_fill(image: Image.Image, mask: np.ndarray,
               rgb: tuple[int, int, int]) -> Image.Image:
    arr = np.asarray(image.convert("RGBA")).copy()
    arr[mask > 0] = (*rgb, 255)
    return Image.fromarray(arr, "RGBA")


def parse_fill_color(value: str) -> tuple[int, int, int]:
    import re
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", value or ""):
        raise ValueError(f"--color 는 #RRGGBB 형식이어야 합니다: {value!r}")
    return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))


def _has_cv2() -> bool:
    try:
        import cv2  # noqa: F401
        return True
    except ImportError:
        return False


def run(project: Project, only: str = "", method: str = "auto",
        pad: int = 2, force: bool = False, color: str = "") -> int:
    data = ledgermod.load(project)
    grouped = _rects_by_file(data, only)
    if not grouped:
        print(f"지울 행 없음 (only={only!r}) — 원장에 source/crop 상자가 필요합니다")
        return 1

    if method == "auto":
        method = "inpaint" if _has_cv2() else "median"
        print(f"method=auto -> {method}")
    if method == "inpaint" and not _has_cv2():
        print("opencv 가 없습니다 — `pip install type-lettering[inpaint]` "
              "하거나 --method median 을 쓰세요.")
        return 1
    fill_rgb = parse_fill_color(color) if method == "fill" else None

    done = skipped = 0
    for relative, rects in sorted(grouped.items()):
        src_path = project.original_root / Path(*relative.split("/"))
        dst_path = project.base_root / Path(*relative.split("/"))
        if not src_path.exists():
            print(f"  {relative:40} 원본 없음 — 건너뜀")
            continue
        if dst_path.exists() and not force:
            print(f"  {relative:40} 베이스 있음 (손질본 보호) — --force 로 덮기")
            skipped += 1
            continue
        image = Image.open(src_path).convert("RGBA")
        if method == "inpaint":
            result = erase_inpaint(image, build_mask(image.size, rects, pad))
        elif method == "median":
            result = erase_median(image, rects, pad)
        elif method == "alpha":
            result = erase_alpha(image, build_mask(image.size, rects, pad))
        elif method == "fill":
            result = erase_fill(image, build_mask(image.size, rects, pad),
                                fill_rgb)
        else:
            raise ValueError(f"모르는 method: {method!r}")
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        result.save(dst_path)
        print(f"  {relative:40} {len(rects):>3}상자 지움 -> {dst_path}")
        done += 1
    print(f"\n{done}장 저장, {skipped}장 보호됨 -> {project.base_root}")
    return 0
