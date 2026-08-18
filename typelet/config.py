# -*- coding: utf-8 -*-
"""프로젝트 설정 — typelet.config.json.

설정 파일이 있는 디렉토리가 **프로젝트 루트**다. 모든 명령은 cwd 에서 위로
올라가며 설정 파일을 찾는다 (git 처럼). 경로 값은 설정 파일 기준 상대 경로
또는 절대 경로.

    original_root   원본 이미지 트리 (읽기 전용 취급)
    base_root       무문자 베이스 트리 (erase 출력, 손질본 포함)
    output_root     렌더 결과 트리
    preview_root    검수 산출물 (boxes 그림, on-original 덧구움)
    font_root       글꼴 파일 디렉토리
    ledger          원장 파일 (스타일 + 행)
    ocr_lang        OCR 언어 (BCP-47, 예: "ja" — tesseract 코드로는 자동 변환)
    ocr_backend     "auto" | "windows" | "tesseract" | "easyocr"
                    (auto = win32 면 windows, 아니면 tesseract → easyocr)
    ocr_min_conf    easyocr 검출 신뢰도 하한 (0~1, 기본 0.2) — 그림을 글자로
                    오인한 저신뢰 검출을 거른다
    fonts           {"패밀리/weight": "글꼴파일"} — 파일은 font_root 기준
                    상대 또는 절대 경로
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

CONFIG_NAME = "typelet.config.json"

DEFAULTS = {
    "original_root": "originals",
    "base_root": "base",
    "output_root": "out",
    "preview_root": "preview",
    "font_root": "fonts",
    "ledger": "lettering.json",
    "ocr_lang": "ja",
    "ocr_backend": "auto",
    "ocr_min_conf": 0.2,
    "fonts": {},
}


@dataclass
class Project:
    root: Path
    original_root: Path
    base_root: Path
    output_root: Path
    preview_root: Path
    font_root: Path
    ledger_path: Path
    ocr_lang: str
    ocr_backend: str
    ocr_min_conf: float
    fonts: dict[str, str]


def find_config(start: Path | None = None) -> Path | None:
    cur = (start or Path.cwd()).resolve()
    for d in (cur, *cur.parents):
        candidate = d / CONFIG_NAME
        if candidate.exists():
            return candidate
    return None


def _resolve(root: Path, value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else (root / p)


def load(start: Path | None = None) -> Project:
    config_path = find_config(start)
    if config_path is None:
        raise FileNotFoundError(
            f"{CONFIG_NAME} 을 찾을 수 없습니다 — 프로젝트 루트에서 실행하거나 "
            "`typelet init` 으로 새 프로젝트를 만드세요."
        )
    raw = {**DEFAULTS, **json.loads(config_path.read_text(encoding="utf-8"))}
    root = config_path.parent
    return Project(
        root=root,
        original_root=_resolve(root, raw["original_root"]),
        base_root=_resolve(root, raw["base_root"]),
        output_root=_resolve(root, raw["output_root"]),
        preview_root=_resolve(root, raw["preview_root"]),
        font_root=_resolve(root, raw["font_root"]),
        ledger_path=_resolve(root, raw["ledger"]),
        ocr_lang=raw["ocr_lang"],
        ocr_backend=raw["ocr_backend"],
        ocr_min_conf=float(raw["ocr_min_conf"]),
        fonts=dict(raw["fonts"]),
    )


def init(directory: Path) -> Path:
    """새 프로젝트 뼈대 — 설정·빈 원장·디렉토리를 만든다. 이미 있으면 오류."""
    directory = directory.resolve()
    config_path = directory / CONFIG_NAME
    if config_path.exists():
        raise FileExistsError(f"이미 프로젝트입니다: {config_path}")
    directory.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(DEFAULTS, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )
    for key in ("original_root", "base_root", "output_root",
                "preview_root", "font_root"):
        (directory / DEFAULTS[key]).mkdir(exist_ok=True)
    ledger_path = directory / DEFAULTS["ledger"]
    if not ledger_path.exists():
        ledger_path.write_text(
            json.dumps({"styles": [], "rows": []}, ensure_ascii=False, indent=1)
            + "\n",
            encoding="utf-8",
        )
    return config_path
