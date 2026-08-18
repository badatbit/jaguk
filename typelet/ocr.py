# -*- coding: utf-8 -*-
"""추출 — Windows OCR 로 원본 트리의 원문 텍스트·좌표를 읽는다.

WinRT(Windows.Media.Ocr)는 파이썬에서 직접 부르기 번거로워 동봉한
winocr.ps1 을 PowerShell 로 실행한다 (furaiki3-l10n 에서 검증된 방식).

결과는 두 갈래:
    ocr-raw.json    파일별 {file, width, height, lines:[{text,x,y,w,h}]} —
                    원장에 안 넣고 참고만 할 때
    --seed          원장에 씨앗 행 추가 — jp·source·canvas 를 채우고
                    text 상자는 source 를 복사해 시작점으로 둔다.
                    이미 같은 (file, source) 행이 있으면 건너뛴다 (재실행 안전).

씨앗 행은 status "todo" 로 들어간다 — ko·style 을 채우고 render_ready 로
바꿔야 렌더된다.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path

from . import ledger as ledgermod
from .config import Project

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff"}
SCRIPT = Path(__file__).resolve().parent / "winocr.ps1"


def collect_files(project: Project, only: str = "") -> list[Path]:
    files = [
        p for p in sorted(project.original_root.rglob("*"))
        if p.suffix.lower() in IMAGE_SUFFIXES
        and (not only or only.lower()
             in p.relative_to(project.original_root).as_posix().lower())
    ]
    return files


def run_ocr(project: Project, files: list[Path], lang: str | None = None
            ) -> list[dict]:
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", suffix=".txt", delete=False
    ) as list_file:
        list_file.write("\n".join(str(p) for p in files))
        list_path = Path(list_file.name)
    out_path = list_path.with_suffix(".json")
    try:
        proc = subprocess.run(
            [
                "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(SCRIPT),
                "-ListPath", str(list_path),
                "-Root", str(project.original_root),
                "-OutputPath", str(out_path),
                "-Lang", lang or project.ocr_lang,
            ],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"winocr.ps1 실패 (exit {proc.returncode}):\n{proc.stderr.strip()}"
            )
        return json.loads(out_path.read_text(encoding="utf-8"))
    finally:
        list_path.unlink(missing_ok=True)
        out_path.unlink(missing_ok=True)


def _id_prefix(relative: str) -> str:
    """파일명에서 box_id 접두 — 영문 소문자 두 글자 (pa226 스타일)."""
    stem = Path(relative).stem
    letters = re.sub(r"[^a-z]", "", stem.lower())
    return (letters[:2] or "xx")


def seed_ledger(project: Project, results: list[dict]) -> tuple[int, int]:
    """OCR 결과를 원장 씨앗 행으로. (추가, 건너뜀) 개수를 돌려준다."""
    data = ledgermod.load(project)
    rows = ledgermod.rows(data)
    existing_ids = {r.get("box_id") for r in rows}
    existing_boxes = {
        (r.get("file"), tuple(r.get("source") or ()))
        for r in rows if r.get("source")
    }
    counters: dict[str, int] = {}
    for bid in existing_ids:
        m = re.fullmatch(r"([a-z]+)(\d+)", bid or "")
        if m:
            counters[m.group(1)] = max(counters.get(m.group(1), 0),
                                       int(m.group(2)))

    added = skipped = 0
    for entry in results:
        relative = entry["file"]
        prefix = _id_prefix(relative)
        for line in entry["lines"]:
            box = [line["x"], line["y"], line["w"], line["h"]]
            if (relative, tuple(box)) in existing_boxes:
                skipped += 1
                continue
            counters[prefix] = counters.get(prefix, 0) + 1
            box_id = f"{prefix}{counters[prefix]}"
            while box_id in existing_ids:
                counters[prefix] += 1
                box_id = f"{prefix}{counters[prefix]}"
            existing_ids.add(box_id)
            existing_boxes.add((relative, tuple(box)))
            rows.append({
                "box_id": box_id,
                "file": relative,
                "element_id": None,
                "run_id": None,
                "jp": line["text"],
                "ko": "",
                "ocr_id": None,
                "crop": None,
                "text": list(box),      # 시작점 — 배치 정하면서 다듬는다
                "source": list(box),
                "canvas": [entry["width"], entry["height"]],
                "pad": None,
                "style": "",
                "opacity": "FF",
                "status": "todo",
                "notes": "OCR seed",
            })
            added += 1
    if added:
        ledgermod.save(project, data)
    return added, skipped


def run(project: Project, only: str = "", seed: bool = False,
        lang: str | None = None, out: str = "") -> int:
    files = collect_files(project, only)
    if not files:
        print(f"원본 이미지 없음: {project.original_root} (only={only!r})")
        return 1
    print(f"OCR {len(files)}장 ({lang or project.ocr_lang}) ...")
    results = run_ocr(project, files, lang)

    total_lines = sum(len(e["lines"]) for e in results)
    for entry in results:
        print(f"  {entry['file']:40} {len(entry['lines']):>3}줄")
    print(f"합계 {len(results)}장 {total_lines}줄")

    if out:
        out_path = project.root / out
        out_path.write_text(
            json.dumps(results, ensure_ascii=False, indent=1) + "\n",
            encoding="utf-8",
        )
        print(f"raw 저장 -> {out_path}")
    if seed:
        added, skipped = seed_ledger(project, results)
        print(f"원장 씨앗 행 {added}개 추가, 중복 {skipped}개 건너뜀 "
              f"-> {project.ledger_path}")
    return 0
