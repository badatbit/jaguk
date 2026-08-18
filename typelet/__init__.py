# -*- coding: utf-8 -*-
"""typelet — 이미지 텍스트 로컬라이징 파이프라인.

이미지 속 텍스트를 ① 추출(OCR) → ② 지우기(무문자 베이스) → ③ 주입(번역
렌더) 하는 범용 도구다. furaiki3-l10n 의 imgtext 파이프라인에서 이미지 처리
부분만 떼어 게임 의존성(아카이브 입출력·exe 좌표 검증) 없이 독립시켰다.

    config      프로젝트 설정 (typelet.config.json — 경로·글꼴·OCR 언어)
    ledger      원장 입출력 (lettering.json — 스타일 + 행, image_text.json 호환)
    ocr         추출 — Windows OCR 로 원문·좌표를 읽어 원장에 씨앗 행 생성
    erase       지우기 — 원본에서 글자 영역을 지워 무문자 베이스 생성
    render      주입 — 원장 기반 번역 텍스트 렌더러 (무문자 베이스 → 출력)
    preview     검수 — 상자(crop/source/text) 그림
    fonts       글꼴 캐시 (설정의 family/weight → 파일 매핑)

사용은 `typelet <sub> ...`. 프로젝트 루트는 typelet.config.json 이 정한다.
"""

__version__ = "0.1.0"
