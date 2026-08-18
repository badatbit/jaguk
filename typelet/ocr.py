# -*- coding: utf-8 -*-
"""추출 — OCR 로 원본 트리의 원문 텍스트·좌표를 읽는다.

백엔드 셋 (--backend 또는 설정 ocr_backend, 기본 auto):
    windows     WinRT(Windows.Media.Ocr) — 동봉한 winocr.ps1 을 PowerShell 로
                실행한다 (furaiki3-l10n 에서 검증된 방식). Windows 전용,
                해당 언어팩 필요.
    tesseract   pytesseract — 리눅스/맥 포함 어디서나. tesseract 바이너리와
                언어 데이터(예: tesseract-ocr-jpn) 필요. 단어 상자를
                block/par/line 번호로 묶어 줄 상자를 만든다.
    easyocr     EasyOCR — 순수 pip 설치(torch 포함, 무겁다). 바이너리 의존
                없이 리눅스에서 가장 간단. 검출 단위가 대략 줄이다.
    auto        win32 면 windows, 아니면 tesseract → easyocr 순으로 있는 것.

어느 백엔드든 결과 형태는 같다: 파일별
    {file, width, height, lines: [{text, x, y, w, h}]}

언어는 설정 ocr_lang 에 BCP-47 로 적는다 ("ja") — tesseract 세 글자 코드
("jpn")로는 내부에서 변환한다.

결과는 두 갈래:
    --out       raw JSON 저장 — 원장에 안 넣고 참고만 할 때
    --seed      원장에 씨앗 행 추가 — jp·source·canvas 를 채우고
                text 상자는 source 를 복사해 시작점으로 둔다.
                이미 같은 (file, source) 행이 있으면 건너뛴다 (재실행 안전).

씨앗 행은 status "todo" 로 들어간다 — ko·style 을 채우고 render_ready 로
바꿔야 렌더된다.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import ledger as ledgermod
from .config import Project

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff"}
SCRIPT = Path(__file__).resolve().parent / "winocr.ps1"

BACKENDS = ("auto", "windows", "tesseract", "easyocr")

# BCP-47 → tesseract 세 글자 코드. 목록 밖 값은 그대로 넘긴다 (이미 세 글자
# 코드거나 "jpn+eng" 같은 조합 표기일 수 있다).
TESSERACT_LANGS = {"ja": "jpn", "ko": "kor", "en": "eng",
                   "zh": "chi_sim", "zh-tw": "chi_tra"}


def collect_files(project: Project, only: str = "") -> list[Path]:
    files = [
        p for p in sorted(project.original_root.rglob("*"))
        if p.suffix.lower() in IMAGE_SUFFIXES
        and (not only or only.lower()
             in p.relative_to(project.original_root).as_posix().lower())
    ]
    return files


def _has_module(name: str) -> bool:
    import importlib.util
    return importlib.util.find_spec(name) is not None


def pick_backend(requested: str) -> str:
    if requested != "auto":
        return requested
    if sys.platform == "win32":
        return "windows"
    if _has_module("pytesseract") and shutil.which("tesseract"):
        return "tesseract"
    if _has_module("easyocr"):
        return "easyocr"
    raise RuntimeError(
        "쓸 수 있는 OCR 백엔드가 없습니다 — tesseract(+pytesseract) 를 "
        "설치하거나 `pip install type-lettering[easyocr]` 하세요."
    )


def ocr_windows(project: Project, files: list[Path], lang: str) -> list[dict]:
    """WinRT OCR — 동봉 winocr.ps1 을 PowerShell 로 실행."""
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
                "-Lang", lang,
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


def _flatten(image, rgb: tuple[int, int, int]):
    from PIL import Image
    background = Image.new("RGBA", image.size, rgb + (255,))
    background.alpha_composite(image)
    return background.convert("RGB")


def _variants(path: Path) -> list:
    """OCR 에 넘길 배경 합성본들.

    투명 배경은 엔진이 임의 색으로 읽으므로 불투명하게 깔아야 하는데, 게임
    스프라이트는 흰 글자와 어두운 글자가 한 장에 섞여 있다 — 흰 바탕이면 흰
    글자가, 검은 바탕이면 어두운 글자가 사라진다 (saveloadday 실측: 흰 바탕
    에서 0줄). 그래서 대체로 투명한 이미지(비율 > 0.5)는 흰·검 두 바탕을
    다 만들어 각각 OCR 하고 상자가 안 겹치는 검출을 합친다.
    """
    import numpy as np
    from PIL import Image
    image = Image.open(path).convert("RGBA")
    alpha_ratio = float((np.asarray(image)[:, :, 3] < 255).mean())
    variants = [_flatten(image, (255, 255, 255))]
    if alpha_ratio > 0.5:
        variants.append(_flatten(image, (0, 0, 0)))
    return variants


def _overlaps(a: dict, b: dict) -> bool:
    """상자 IoU > 0.3 — 두 배경 합성본이 같은 글자를 잡은 것으로 본다."""
    x0 = max(a["x"], b["x"])
    y0 = max(a["y"], b["y"])
    x1 = min(a["x"] + a["w"], b["x"] + b["w"])
    y1 = min(a["y"] + a["h"], b["y"] + b["h"])
    if x1 <= x0 or y1 <= y0:
        return False
    inter = (x1 - x0) * (y1 - y0)
    union = a["w"] * a["h"] + b["w"] * b["h"] - inter
    return inter / union > 0.3


def _ocr_file(path: Path, ocr_one) -> list[dict]:
    """한 파일 = 배경 합성본별 OCR 을 겹침 제거로 합친 줄 목록."""
    merged: list[dict] = []
    for image in _variants(path):
        for line in ocr_one(image):
            if not any(_overlaps(line, kept) for kept in merged):
                merged.append(line)
    merged.sort(key=lambda l: (l["y"], l["x"]))
    return merged


def ocr_tesseract(project: Project, files: list[Path], lang: str) -> list[dict]:
    """pytesseract — image_to_data 의 단어를 (block, par, line) 으로 묶는다."""
    import pytesseract

    tess_lang = TESSERACT_LANGS.get(lang.lower(), lang)
    # 일본어는 붙여 쓴다 — WinRT 백엔드와 같은 규약 (공백 없이 join)
    joiner = "" if tess_lang.startswith(("jpn", "chi")) else " "

    def ocr_one(image) -> list[dict]:
        data = pytesseract.image_to_data(
            image, lang=tess_lang, output_type=pytesseract.Output.DICT)
        grouped: dict[tuple, list[int]] = {}
        order: list[tuple] = []
        for i, text in enumerate(data["text"]):
            if not text.strip() or int(data["conf"][i]) < 0:
                continue
            key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
            if key not in grouped:
                grouped[key] = []
                order.append(key)
            grouped[key].append(i)
        lines = []
        for key in order:
            idx = grouped[key]
            x0 = min(data["left"][i] for i in idx)
            y0 = min(data["top"][i] for i in idx)
            x1 = max(data["left"][i] + data["width"][i] for i in idx)
            y1 = max(data["top"][i] + data["height"][i] for i in idx)
            lines.append({
                "text": joiner.join(data["text"][i].strip() for i in idx),
                "x": int(x0), "y": int(y0),
                "w": int(x1 - x0), "h": int(y1 - y0),
            })
        return lines

    results = []
    for path in files:
        lines = _ocr_file(path, ocr_one)
        from PIL import Image
        with Image.open(path) as image:
            size = image.size
        results.append({
            "file": path.relative_to(project.original_root).as_posix(),
            "width": size[0], "height": size[1],
            "lines": lines,
        })
    return results


def ocr_easyocr(project: Project, files: list[Path], lang: str) -> list[dict]:
    """EasyOCR — 검출 단위가 대략 줄이라 그대로 한 줄로 쓴다.

    신뢰도 하한(설정 ocr_min_conf, 기본 0.2)으로 저신뢰 검출을 거른다 —
    게임 아틀라스는 그림 스프라이트를 글자로 오인한 검출이 많다
    (albummode2 실측: 실제 텍스트 최저 0.206, 스프라이트 오인 최고 0.163).
    """
    import easyocr
    import numpy as np

    reader = easyocr.Reader([lang], verbose=False)

    def make_ocr_one(size):
        def ocr_one(image) -> list[dict]:
            lines = []
            for quad, text, conf in reader.readtext(np.asarray(image)):
                if conf < project.ocr_min_conf:
                    continue
                xs = [p[0] for p in quad]
                ys = [p[1] for p in quad]
                # quad 꼭짓점은 이미지 밖(음수)일 수 있다 — 캔버스로 클램프
                x0 = max(0, int(min(xs)))
                y0 = max(0, int(min(ys)))
                x1 = min(size[0], int(max(xs)))
                y1 = min(size[1], int(max(ys)))
                if x1 <= x0 or y1 <= y0:
                    continue
                lines.append({
                    "text": text,
                    "x": x0, "y": y0,
                    "w": x1 - x0, "h": y1 - y0,
                })
            return lines
        return ocr_one

    results = []
    for path in files:
        from PIL import Image
        with Image.open(path) as image:
            size = image.size
        lines = _ocr_file(path, make_ocr_one(size))
        results.append({
            "file": path.relative_to(project.original_root).as_posix(),
            "width": size[0], "height": size[1],
            "lines": lines,
        })
    return results


def run_ocr(project: Project, files: list[Path], lang: str | None = None,
            backend: str = "auto") -> tuple[str, list[dict]]:
    """(실제 쓴 백엔드, 결과) — 결과 형태는 백엔드와 무관하게 같다."""
    backend = pick_backend(backend)
    lang = lang or project.ocr_lang
    if backend == "windows":
        return backend, ocr_windows(project, files, lang)
    if backend == "tesseract":
        return backend, ocr_tesseract(project, files, lang)
    if backend == "easyocr":
        return backend, ocr_easyocr(project, files, lang)
    raise ValueError(f"모르는 OCR 백엔드: {backend!r}")


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
        lang: str | None = None, out: str = "", backend: str = "") -> int:
    files = collect_files(project, only)
    if not files:
        print(f"원본 이미지 없음: {project.original_root} (only={only!r})")
        return 1
    backend = pick_backend(backend or project.ocr_backend)
    print(f"OCR {len(files)}장 ({lang or project.ocr_lang}, {backend}) ...")
    _, results = run_ocr(project, files, lang, backend)

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
