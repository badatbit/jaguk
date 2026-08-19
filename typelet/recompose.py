# -*- coding: utf-8 -*-
"""아틀라스 재조합 — 조각난 이미지를 게임이 그리는 모양으로 이어 붙인다.

인터넷 모드류 아틀라스는 캔버스 한 장에 페이지 위쪽 + 아래쪽 판 조각들이
따로 담겨 있고, 게임이 런타임에 조각을 제자리에 blit 해 완성한다. 편집·
좌표 작업은 완성된 모양에서 해야 하므로 아틀라스를 그 모양으로 재조합하고,
게임에 넣기 직전에 원형으로 복원한다 — **왕복 무손실**.

규칙은 원장 최상위 "recompose" 에 데이터로 둔다 (조각 배치는 엔진 blit
지식이라 스크립트가 추론하지 않는다 — 사람/AI 가 정해 기록):

    "recompose": [{
      "file": "parts/internetmode1a.tga.png",
      "canvas": [1024, 1024],          ← 게임 네이티브 크기
      "to": [1280, 1024],              ← 재조합(작업) 크기
      "moves": [[sx, sy, w, h, dx, dy], …]   ← 조각 이동 (원 위치는 비운다)
    }]

jaguk recompose            적용 — originals/erased/injected 세 트리에서
                           canvas 크기인 파일을 to 크기로 변환 (이미 to 면
                           건너뜀). 원장 행 좌표(canvas 가 원 크기인 행)도
                           함께 변환. 저장 전 왕복 검증.
jaguk recompose --restore  injected 트리의 재조합 파일을 게임 네이티브로
                           복원해 별도 디렉토리에 출력 (원장은 불변 —
                           작업 좌표계는 재조합 기준으로 유지).
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from . import ledger as ledgermod
from .config import Project


def specs(data: dict) -> list[dict]:
    return data.get("recompose") or []


def compose(image: Image.Image, spec: dict) -> Image.Image:
    assert list(image.size) == list(spec["canvas"]), image.size
    out = Image.new("RGBA", tuple(spec["to"]), (0, 0, 0, 0))
    out.paste(image, (0, 0))
    for sx, sy, w, h, dx, dy in spec["moves"]:
        out.paste(image.crop((sx, sy, sx + w, sy + h)), (dx, dy))
        out.paste(Image.new("RGBA", (w, h), (0, 0, 0, 0)), (sx, sy))
    return out


def restore(image: Image.Image, spec: dict) -> Image.Image:
    assert list(image.size) == list(spec["to"]), image.size
    out = image.crop((0, 0, *spec["canvas"])).copy()
    for sx, sy, w, h, dx, dy in spec["moves"]:
        out.paste(image.crop((dx, dy, dx + w, dy + h)), (sx, sy))
    return out


def map_rect(rect: list, spec: dict) -> list:
    """네이티브 좌표계 사각형 → 재조합 좌표계. 이동 판에 걸치면 오류."""
    x, y, w, h = rect
    for sx, sy, bw, bh, dx, dy in spec["moves"]:
        if sx <= x and x + w <= sx + bw and sy <= y and y + h <= sy + bh:
            return [x - sx + dx, y - sy + dy, w, h]
        intersects = not (x + w <= sx or sx + bw <= x
                          or y + h <= sy or sy + bh <= y)
        if intersects:
            raise ValueError(f"이동 판에 걸친 사각형: {rect}")
    return [x, y, w, h]


def _transform_rows(data: dict, spec: dict) -> int:
    """원장 행 좌표를 재조합 좌표계로 — canvas 가 네이티브 크기인 행만."""
    changed = 0
    for row in ledgermod.rows(data):
        if row.get("file") != spec["file"]:
            continue
        if row.get("canvas") != list(spec["canvas"]):
            continue                     # 이미 재조합 좌표계 (또는 무관)
        row["canvas"] = list(spec["to"])
        crop = row.get("crop") or {}
        if crop.get("rect"):
            crop["rect"] = map_rect(crop["rect"], spec)
        if row.get("text"):
            row["text"] = map_rect(row["text"], spec)
        if row.get("source"):
            row["source"] = map_rect(row["source"], spec)
        changed += 1
    return changed


def run(project: Project, only: str = "") -> int:
    data = ledgermod.load(project)
    rules = specs(data)
    if not rules:
        print('원장에 "recompose" 스펙이 없습니다 — ledger 에 조각 배치 규칙을 '
              "기록하세요 (모듈 도크스트링 참고).")
        return 1
    trees = (("originals", project.original_root),
             ("erased", project.base_root),
             ("injected", project.output_root))
    rows_changed = 0
    for spec in rules:
        relative = spec["file"]
        if only and only.lower() not in relative.lower():
            continue
        for label, root in trees:
            path = root / Path(*relative.split("/"))
            if not path.exists():
                continue
            image = Image.open(path).convert("RGBA")
            if list(image.size) == list(spec["to"]):
                print(f"  {label:9} {relative}: 이미 재조합 — 건너뜀")
                continue
            out = compose(image, spec)
            if restore(out, spec).tobytes() != image.tobytes():
                raise RuntimeError(f"{relative}: 왕복 검증 실패 — 중단")
            out.save(path)
            print(f"  {label:9} {relative}: {image.size} → {out.size}")
        rows_changed += _transform_rows(data, spec)
    if rows_changed:
        ledgermod.save(project, data)
        print(f"원장 행 좌표 {rows_changed}개 변환")
    return 0


def run_restore(project: Project, outdir: str = "", only: str = "") -> int:
    """injected 트리의 재조합 파일을 게임 네이티브로 복원해 outdir 에 출력."""
    data = ledgermod.load(project)
    rules = specs(data)
    target = Path(outdir) if outdir else \
        project.output_root.parent / (project.output_root.name + "-native")
    count = 0
    for spec in rules:
        relative = spec["file"]
        if only and only.lower() not in relative.lower():
            continue
        path = project.output_root / Path(*relative.split("/"))
        if not path.exists():
            print(f"  {relative}: injected 없음 — 건너뜀")
            continue
        image = Image.open(path).convert("RGBA")
        if list(image.size) != list(spec["to"]):
            print(f"  {relative}: 재조합 크기가 아님 {image.size} — 건너뜀")
            continue
        out_path = target / Path(*relative.split("/"))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        restore(image, spec).save(out_path)
        print(f"  {relative}: {image.size} → {spec['canvas']} -> {out_path}")
        count += 1
    print(f"복원 {count}장 -> {target}")
    return 0
