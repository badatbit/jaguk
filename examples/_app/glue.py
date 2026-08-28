# -*- coding: utf-8 -*-
"""브라우저(Pyodide)에서 typelet 렌더를 **그대로** 돌리는 접착 코드.

별도 렌더러가 아니다 — /lib 에 실린 typelet 패키지(레포와 동일 소스)의
resolve/compose_file 을 그대로 호출한다. 파이프라인·jaguk GUI 와 같은 코드
경로라 산출도 같다. 번역 수정(edits)은 렌더 직전 메모리에서만 얹는다."""
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, "/lib")
from typelet import config as tconfig, ledger as ledgermod, render  # noqa: E402

PROJECT = tconfig.load_path(Path("/proj/typelet.config.json"))
DATA = ledgermod.load(PROJECT)
STYLES = ledgermod.styles_map(DATA)
FLOWS = {r["box_id"]: r["flow"] for r in ledgermod.rows(DATA) if r.get("flow")}


def _apply_edits(all_rows, edits):
    """{box_id: "ko"} (구 형식) 또는 {box_id: {ko?, box?, crop?}} 를 얹는다.

    box/crop 은 [x, y, w, h] — 원장 좌표계의 상자 오버라이드."""
    for r in all_rows:
        e = edits.get(r.get("box_id"))
        if e is None:
            continue
        if isinstance(e, str):
            r["ko_text"] = e
            continue
        if "ko" in e:
            r["ko_text"] = e["ko"]
        for key, prefix in (("box", "text_"), ("crop", "crop_")):
            rect = e.get(key)
            if isinstance(rect, list) and len(rect) == 4:
                for name, value in zip(("x", "y", "w", "h"), rect):
                    r[prefix + name] = str(int(value))


def render_png(rel, edits_json):
    """rel 파일을 현재 원장 + edits 로 합성한 PNG 바이트 (GUI 미리보기 경로).

    overlay 그룹 멤버는 그룹 base(원본)를 밑판으로 깔아 돌려준다."""
    edits = json.loads(edits_json or "{}")
    all_rows = ledgermod.flat_rows(DATA)
    _apply_edits(all_rows, edits)
    sel = [r for r in all_rows
           if r.get("file") == rel
           and r.get("status") in ("render_ready", "todo")
           and (r.get("ko_text") or "").strip()]
    specs = []
    for r in sel:
        try:
            s = render.resolve(r, STYLES)
        except render.SkipRow:
            continue
        s.flow = FLOWS.get(s.box_id)
        specs.append(s)
    if not specs:
        raise RuntimeError("렌더할 행이 없습니다: " + rel)
    posts = [p for p in DATA.get("post", []) if p.get("file") == rel]
    out, _, _ = render.compose_file(PROJECT, rel, specs,
                                    posts=posts or None, data=DATA)

    grp = ledgermod.overlay_group_for(DATA, rel)
    if grp and rel != grp[1]:
        base_path = PROJECT.original_root / Path(*grp[1].split("/"))
        if base_path.exists():
            from PIL import Image
            under = Image.open(base_path).convert("RGBA")
            canvas = Image.new(
                "RGBA",
                (max(under.width, out.width), max(under.height, out.height)),
                (0, 0, 0, 0))
            canvas.alpha_composite(under)
            canvas.alpha_composite(out)
            out = canvas

    buf = io.BytesIO()
    out.save(buf, "PNG")
    return buf.getvalue()
