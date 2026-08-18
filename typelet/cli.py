# -*- coding: utf-8 -*-
"""typelet 명령행 — 추출 · 지우기 · 주입 파이프라인.

    init        새 프로젝트 뼈대 (typelet.config.json + 디렉토리 + 빈 원장)
    extract     Windows OCR 로 원문·좌표 추출 (--seed 로 원장에 씨앗 행)
    erase       글자 영역을 지워 무문자 베이스 생성 (→ base)
    render      원장 기반 번역 렌더 (무문자 베이스 → out)
                --on-original  원본 위 덧구움 → preview/ko-on-original
    preview     상자 그림 (→ preview/boxes)
    status      원장 진행 상황 요약
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    # 한국어·기호 출력이 cp949 콘솔에서 깨지지 않게 — CLI 진입점에서만 만진다.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        prog="typelet",
        description="이미지 텍스트 로컬라이징 — 추출(OCR) · 지우기 · 주입(렌더)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="새 프로젝트 뼈대 생성")
    p.add_argument("directory", nargs="?", default=".",
                   help="프로젝트 디렉토리 (기본: 현재 위치)")

    p = sub.add_parser("extract", help="OCR 추출")
    p.add_argument("--only", default="", help="상대 경로 부분일치 필터")
    p.add_argument("--seed", action="store_true",
                   help="결과를 원장 씨앗 행으로 추가 (중복은 건너뜀)")
    p.add_argument("--lang", default="", help="OCR 언어 태그 (기본: 설정값)")
    p.add_argument("--backend", default="",
                   choices=("", "auto", "windows", "tesseract", "easyocr"),
                   help="OCR 백엔드 (기본: 설정 ocr_backend, auto=플랫폼 자동)")
    p.add_argument("--out", default="",
                   help="raw JSON 저장 경로 (프로젝트 루트 기준)")

    p = sub.add_parser("erase", help="무문자 베이스 생성 (→ base)")
    p.add_argument("--only", default="")
    p.add_argument("--method", default="auto",
                   choices=("auto", "inpaint", "median", "alpha"))
    p.add_argument("--pad", type=int, default=2, help="상자 여백 px (기본 2)")
    p.add_argument("--force", action="store_true",
                   help="이미 있는 베이스(손질본일 수 있음)도 덮어쓴다")

    p = sub.add_parser("render", help="번역 렌더 (→ out)")
    p.add_argument("--status", action="append",
                   help="렌더링할 status. 반복 지정 가능 (기본: render_ready)")
    p.add_argument("--only", default="", help="파일명 부분일치 필터")
    p.add_argument("--list", action="store_true", help="대상만 나열")
    p.add_argument("--on-original", action="store_true",
                   help="원본 위에 덧구움 → preview/ko-on-original")

    p = sub.add_parser("preview", help="상자 그림 (→ preview/boxes)")
    p.add_argument("--only", default="")
    p.add_argument("--scale", type=int, default=2,
                   help="확대 배율 — 상자만 커지고 라벨 글자는 그대로라 라벨 자리가 넓어진다")

    sub.add_parser("status", help="원장 진행 상황 요약")

    args = parser.parse_args(argv)

    if args.cmd == "init":
        from . import config
        path = config.init(Path(args.directory))
        print(f"프로젝트 생성: {path.parent}")
        print("다음 단계: originals/ 에 원본을 넣고 `typelet extract --seed`")
        return 0

    from . import config
    try:
        project = config.load()
    except FileNotFoundError as e:
        print(e, file=sys.stderr)
        return 2

    if args.cmd == "extract":
        from . import ocr
        return ocr.run(project, only=args.only, seed=args.seed,
                       lang=args.lang or None, out=args.out,
                       backend=args.backend)
    if args.cmd == "erase":
        from . import erase
        return erase.run(project, only=args.only, method=args.method,
                         pad=args.pad, force=args.force)
    if args.cmd == "render":
        from . import render
        return render.run(
            project,
            statuses=set(args.status) if args.status else None,
            only=args.only, list_only=args.list, on_original=args.on_original,
        )
    if args.cmd == "preview":
        from . import preview
        return preview.run(project, only=args.only, scale=args.scale)
    if args.cmd == "status":
        from . import ledger as ledgermod
        data = ledgermod.load(project)
        rows = ledgermod.rows(data)
        by_status = Counter((r.get("status") or "(없음)") for r in rows)
        files = {r.get("file") for r in rows}
        print(f"원장: {project.ledger_path}")
        print(f"행 {len(rows)}개 / 파일 {len(files)}개 / "
              f"스타일 {len(data.get('styles', []))}개")
        for status, count in by_status.most_common():
            print(f"  {status:20} {count}")
        untranslated = sum(1 for r in rows if not (r.get("ko") or "").strip())
        print(f"  (ko 비어 있음)       {untranslated}")
        return 0
    parser.error(f"알 수 없는 서브커맨드 {args.cmd!r}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
