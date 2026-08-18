# -*- coding: utf-8 -*-
"""프로젝트 설정 — typelet.config.json.

설정 파일이 있는 디렉토리가 **프로젝트 루트**다. 모든 명령은 cwd 에서 위로
올라가며 설정 파일을 찾는다 (git 처럼). 경로 값은 설정 파일 기준 상대 경로
또는 절대 경로.

    디렉토리 키는 파이프라인 단계 이름이다:

    source          (선택) 대량 원본 트리 — scan 의 대상, copy 의 출발지.
                    보통 프로젝트 밖 읽기 전용 덤프. 비어 있으면 originals
                    를 직접 스캔한다
    originals       작업 원본 — copy 의 도착지이자 파이프라인의 입력
    texts           추출 텍스트(파일별 JSON)와 원장(lettering.json) 저장소
    erased          텍스트 지운 이미지 (clean/erase 출력, 손질본 포함)
    injected        텍스트 주입 결과 (inject/render 출력)
    preview_root    검수 산출물 (boxes 그림, on-original 덧구움)
    font_root       글꼴 파일 디렉토리
    ledger          원장 파일 (스타일 + 행)

    구세대 키(source_root/original_root/texts_root/base_root/output_root)도
    계속 읽는다 — 새 키가 없을 때의 알리아스.
    ocr_lang        OCR 언어 (BCP-47, 예: "ja" — tesseract 코드로는 자동 변환)
    ocr_backend     "auto" | "windows" | "tesseract" | "easyocr"
                    (auto = win32 면 windows, 아니면 tesseract → easyocr)
    ocr_min_conf    easyocr 검출 신뢰도 하한 (0~1, 기본 0.2) — 그림을 글자로
                    오인한 저신뢰 검출을 거른다
    ocr_dict        OCR 교정 사전 파일 목록 — 알려진 원문 어휘. OCR 결과를
                    유사도로 사전 항목에 스냅해 오독(火鍵日→火曜日)을 고친다.
                    .txt(한 줄 하나) 또는 용어표 형식(json/tsv — 원문 키만 씀)
    ocr_dict_min    교정 유사도 하한 (0~1, 기본 0.7) — 미만이면 원문 유지
    fonts           {"패밀리/weight": "글꼴파일"} — 파일은 font_root 기준
                    상대 또는 절대 경로
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

CONFIG_NAME = "typelet.config.json"

DEFAULTS = {
    "source": "",
    "originals": "originals",
    "texts": "texts",
    "erased": "erased",
    "injected": "injected",
    "preview_root": "preview",
    "font_root": "fonts",
    "ledger": "lettering.json",
    "ocr_lang": "ja",
    "ocr_backend": "auto",
    "ocr_min_conf": 0.2,
    "ocr_dict": [],
    "ocr_dict_min": 0.7,
    "fonts": {},
}


@dataclass
class Project:
    root: Path
    original_root: Path
    source_root: Path | None
    texts_root: Path
    base_root: Path
    output_root: Path
    preview_root: Path
    font_root: Path
    ledger_path: Path
    ocr_lang: str
    ocr_backend: str
    ocr_min_conf: float
    ocr_dict: list[str]
    ocr_dict_min: float
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
    return load_path(config_path)


# 구세대 키 → 새 키 (설정 파일에 구 키만 있으면 그 값을 새 키로 옮긴다)
LEGACY_KEYS = {
    "source_root": "source",
    "original_root": "originals",
    "texts_root": "texts",
    "base_root": "erased",
    "output_root": "injected",
}


def load_path(config_path: Path) -> Project:
    """설정 파일 경로를 직접 지정해 읽는다 (jaguk -c 등)."""
    file_raw = json.loads(config_path.read_text(encoding="utf-8"))
    for old, new in LEGACY_KEYS.items():
        if old in file_raw and new not in file_raw:
            file_raw[new] = file_raw[old]
    raw = {**DEFAULTS, **file_raw}
    root = config_path.parent
    return Project(
        root=root,
        original_root=_resolve(root, raw["originals"]),
        source_root=_resolve(root, raw["source"]) if raw["source"] else None,
        texts_root=_resolve(root, raw["texts"]),
        base_root=_resolve(root, raw["erased"]),
        output_root=_resolve(root, raw["injected"]),
        preview_root=_resolve(root, raw["preview_root"]),
        font_root=_resolve(root, raw["font_root"]),
        ledger_path=_resolve(root, raw["ledger"]),
        ocr_lang=raw["ocr_lang"],
        ocr_backend=raw["ocr_backend"],
        ocr_min_conf=float(raw["ocr_min_conf"]),
        ocr_dict=list(raw["ocr_dict"]),
        ocr_dict_min=float(raw["ocr_dict_min"]),
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
    for key in ("originals", "texts", "erased", "injected",
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
