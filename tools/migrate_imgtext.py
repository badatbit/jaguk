# -*- coding: utf-8 -*-
"""구 imgtext 원장(translation/images/image_text.json) → lettering.json 이식.

구 파이프라인에서 사람 손으로 다듬은 crop/text 상자·번역·스타일을 새 원장으로
가져온다 — OCR 시드보다 구 원장이 항상 우선한다.

정책:
  - route1/route2 는 ignore 규칙(사용자 제외) — 건너뜀.
  - parts/parts.tga.png 는 신 원장의 menu 스타일 45행(crop_size/pad 신방식)을
    유지하고, 구 st60(같은 메뉴) 행은 안 가져온다. 나머지 행은 구 것으로 교체.
  - 그 외 파일: 신 원장 행(OCR 시드·수정본 포함) 전부를 구 행으로 교체.
  - 구 행이 참조하는 스타일(st*)과 post(레이어 합성)도 함께 이식.
  - 좌표는 구 행의 canvas 그대로 둔다 — 네이티브(1024) 좌표인 파일은 이후
    `jaguk recompose` 가 재조합 좌표로 변환한다.

사용: python tools/migrate_imgtext.py [l10n-repo-root]
"""

import argparse
import json
from pathlib import Path

SKIP_FILES = {"parts/route1.tga.png", "parts/route2.tga.png"}
KEEP_NEW_STYLE = {"parts/parts.tga.png": ("menu", "st60")}
#                 파일: (유지할 신 스타일, 안 가져올 구 스타일)


def main() -> int:
    ap = argparse.ArgumentParser(description="구 imgtext 원장 → lettering.json 이식")
    ap.add_argument("root", nargs="?", default=".",
                    help="furaiki3-l10n 레포 루트 (기본: 현재 디렉토리)")
    root = Path(ap.parse_args().root)
    old_path = root / "translation/images/image_text.json"
    new_path = root / "data-image/lettering.json"
    old = json.loads(old_path.read_text(encoding="utf-8"))
    new = json.loads(new_path.read_text(encoding="utf-8"))

    port_files = sorted({r["file"] for r in old["rows"]} - SKIP_FILES)

    kept, removed = [], []
    for r in new.get("rows", []):
        f = r.get("file")
        if f not in port_files:
            kept.append(r)
        elif f in KEEP_NEW_STYLE and r.get("style") == KEEP_NEW_STYLE[f][0]:
            kept.append(r)
        else:
            removed.append(r)

    imported = []
    for r in old["rows"]:
        f = r["file"]
        if f in SKIP_FILES:
            continue
        if f in KEEP_NEW_STYLE and r.get("style") == KEEP_NEW_STYLE[f][1]:
            continue
        imported.append(r)

    clash = {r["box_id"] for r in imported} & {r.get("box_id") for r in kept}
    if clash:
        print(f"box_id 충돌 {len(clash)}개: {sorted(clash)[:10]} — 중단")
        return 1

    new["rows"] = kept + imported

    used = {r.get("style") for r in imported if r.get("style")}
    have = {s["name"] for s in new.get("styles", [])}
    added_styles = [s for s in old.get("styles", [])
                    if s["name"] in used and s["name"] not in have]
    new.setdefault("styles", []).extend(added_styles)
    missing = used - have - {s["name"] for s in added_styles}
    if missing:
        print(f"★ 구 원장에 정의 없는 스타일 참조: {sorted(missing)}")

    old_posts = [p for p in (old.get("post") or []) if p["file"] in port_files]
    new_posts = new.get("post") or []
    new_posts_files = {p["file"] for p in new_posts}
    new["post"] = new_posts + [p for p in old_posts
                               if p["file"] not in new_posts_files]

    new_path.write_text(json.dumps(new, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    print(f"이식 {len(imported)}행 (제거 {len(removed)}행, 유지 {len(kept)}행) / "
          f"스타일 +{len(added_styles)} / post +{len(old_posts)}")
    for f in port_files:
        n = sum(1 for r in imported if r["file"] == f)
        print(f"  {f:40} {n:>3}행")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
