# -*- coding: utf-8 -*-
"""글꼴 관리 — 설정의 fonts 매핑("패밀리/weight" → 파일)에서 찾는다.

프로젝트가 쓰는 글꼴은 typelet.config.json 의 "fonts" 에 선언한다:

    "fonts": {
        "IBM Plex Sans KR/400": "IBMPlexSansKR-Text.otf",
        "Malgun Gothic/400": "C:/Windows/Fonts/malgun.ttf"
    }

값은 font_root 기준 상대 경로 또는 절대 경로. 다운로드·해시 검증은 하지
않는다 — 글꼴 준비는 프로젝트 몫이다 (라이선스도 거기서 판단).
"""

from __future__ import annotations

from pathlib import Path

from PIL import ImageFont

from .config import Project


def font_path(project: Project, family: str, weight: int) -> Path:
    key = f"{family}/{weight}"
    if key not in project.fonts:
        raise ValueError(
            f"설정에 없는 글꼴입니다: {key!r} — typelet.config.json 의 "
            "\"fonts\" 에 파일을 매핑하세요."
        )
    value = Path(project.fonts[key])
    path = value if value.is_absolute() else project.font_root / value
    if not path.exists():
        raise FileNotFoundError(f"글꼴 파일이 없습니다: {path}")
    return path


class FontCache:
    def __init__(self, project: Project) -> None:
        self.project = project
        self._cache: dict[tuple[str, int, int], ImageFont.FreeTypeFont] = {}

    def get_key(self, family: str, weight: int, size: int) -> ImageFont.FreeTypeFont:
        key = (family, weight, size)
        if key not in self._cache:
            self._cache[key] = ImageFont.truetype(
                font_path(self.project, family, weight), size)
        return self._cache[key]

    def get(self, spec) -> ImageFont.FreeTypeFont:
        return self.get_key(spec.family, spec.weight, spec.size)
