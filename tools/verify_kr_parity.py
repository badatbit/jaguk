# -*- coding: utf-8 -*-
"""이식 검증 — 새 파이프라인 렌더와 구 산출물(furaiki3-img-kr)의 픽셀 비교.

각 파일을 원장 스펙으로 합성(erased 베이스)하고, recompose 스펙이 있으면
게임 네이티브로 복원한 뒤, 구 파이프라인의 완성본과 픽셀 단위로 비교한다.

사용: python tools/verify_kr_parity.py [l10n-root] [kr-root] [only...]
"""

import argparse
import io
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from typelet import ledger as ledgermod          # noqa: E402
from typelet import recompose as recompmod       # noqa: E402
from typelet import render as rendermod          # noqa: E402
from typelet.config import load_path             # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(
        description="새 렌더 vs 구 산출물(kr 트리) 픽셀 비교")
    ap.add_argument("root", nargs="?", default=".",
                    help="jaguk 프로젝트 루트 (기본: 현재 디렉토리)")
    ap.add_argument("kr_root", nargs="?", default="../furaiki3-img-kr",
                    help="구 산출물 트리 (기본: 프로젝트 옆 furaiki3-img-kr)")
    ap.add_argument("only", nargs="*",
                    help="파일명 부분일치 필터 (여러 개 = OR)")
    args = ap.parse_args()
    root = Path(args.root)
    kr_root = Path(args.kr_root)
    only = args.only

    project = load_path(root / "jaguk.json")
    data = ledgermod.load(project)
    styles = ledgermod.styles_map(data)
    all_rows = ledgermod.flat_rows(data)
    terms = ledgermod.load_terms(project, data)
    if terms:
        ledgermod.apply_terms(all_rows, terms)
    specs_by_file = {s["file"]: s for s in (data.get("recompose") or [])}
    posts_by_file = defaultdict(list)
    for post in data.get("post") or []:
        posts_by_file[post["file"]].append(post)

    files = sorted({r["file"] for r in all_rows})
    if only:
        files = [f for f in files if any(o.lower() in f.lower() for o in only)]
    flows = {r["box_id"]: r["flow"]
             for r in ledgermod.rows(data) if r.get("flow")}

    for relative in files:
        kr_path = kr_root / Path(*relative.split("/"))
        if not kr_path.exists():
            print(f"{relative:42} kr 산출물 없음 — 건너뜀")
            continue
        # GUI 즉석 렌더와 같은 기준 — status 무관하게 ko 가 있으면 렌더
        # (no_inject 만 제외). 카탈로그(text-only) 전개 행도 이걸로 잡힌다.
        rows = [r for r in all_rows
                if r["file"] == relative
                and (r.get("status") or "") != "no_inject"
                and (r.get("ko_text") or "").strip()]
        file_specs, skipped = [], 0
        for row in rows:
            try:
                rs = rendermod.resolve(row, styles)
            except rendermod.SkipRow:
                skipped += 1
                continue
            rs.flow = flows.get(rs.box_id)
            file_specs.append(rs)
        if not file_specs:
            print(f"{relative:42} 렌더할 행 없음 (건너뜀 {skipped})")
            continue
        try:
            output, _, _ = rendermod.compose_file(
                project, relative, file_specs,
                posts=posts_by_file.get(relative))
        except Exception as error:
            print(f"{relative:42} ❌ 합성 실패: {error}")
            continue
        # 구 산출물이 네이티브 크기일 때만 복원해 좌표계를 맞춘다 —
        # 구 파이프라인도 재조합 크기로 낸 파일(1280 짜리 kr)이 있다
        kr = Image.open(kr_path).convert("RGBA")
        spec = specs_by_file.get(relative)
        if spec and list(output.size) == list(spec["to"]) \
                and list(kr.size) == list(spec["canvas"]):
            output = recompmod.restore(output, spec)
        if kr.size != output.size:
            print(f"{relative:42} ❌ 크기 불일치 신 {output.size} vs 구 {kr.size}")
            continue
        a = np.asarray(output).astype(int)
        b = np.asarray(kr).astype(int)
        diff = np.abs(a - b).max(axis=2)
        n = int((diff > 0).sum())
        total = diff.size
        note = f"(스킵 {skipped}행)" if skipped else ""
        if n == 0:
            print(f"{relative:42} ✅ 완전 일치 {note}")
        else:
            ys, xs = np.where(diff > 0)
            print(f"{relative:42} 차이 {n}px ({100 * n / total:.2f}%) "
                  f"max {int(diff.max())} bbox x{xs.min()}~{xs.max()} "
                  f"y{ys.min()}~{ys.max()} {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
