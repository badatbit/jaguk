# -*- coding: utf-8 -*-
"""검수 — 원장의 상자들을 원본 위에 그린다 (→ preview/boxes).

## 색·굵기
    마젠타 1px  crop     스프라이트 조각 범위
    시안   1px  source   원문 글자가 있던 범위 (ocr-box)
    흰색 점선   text     번역문을 앉힐 자리. 점선인 이유 — 관측값이 아니라
                         **우리가 정한 값**이다
    빨강   2px  text 가 crop 을 넘음 — **잘린다**
    흐린회색    crop 없이 text 만 있는 행의 text 상자

**상자 선은 전부 1px 이다.** 겹친 상자는 아예 가려지고, 그 사라짐 자체가
"위 상자와 좌표가 같다"는 신호다. 빨강(잘림)만 2px — 겹침이 아니라 경고다.

라벨은 모든 상자에 고유 id 를 **빠짐없이** 적는다. 상자를 다 그린 뒤에 라벨만
한꺼번에 올린다 (번갈아 그리면 뒤 상자가 앞 라벨을 덮는다).

`text_x,y` 는 언제나 좌상단이고, 정렬은 상자 **안에서의** 배치다 — 기준점으로
잘못 읽고 좌표를 옮기면 안 된다.
"""

from __future__ import annotations

import collections
import os

from PIL import Image, ImageDraw, ImageFont

from . import ledger as ledgermod
from .config import Project

MAGENTA = (255, 61, 174)
CYAN = (0, 200, 255)            # source — 원문 글자가 있던 자리
WHITE = (255, 255, 255)
RED = (255, 32, 32)
DIM = (106, 112, 128)


def dashed(d, box, color, width=1, on=4, off=3):
    """점선 사각형. text 상자는 **우리가 정한 값**이라 실선(관측된 것)과 구분한다."""
    x0, y0, x1, y1 = box

    def seg(a, b, horiz):
        p = a
        while p <= b:
            q = min(p + on - 1, b)
            if horiz:
                d.rectangle([p, y0, q, y0 + width - 1], fill=color)
                d.rectangle([p, y1 - width + 1, q, y1], fill=color)
            else:
                d.rectangle([x0, p, x0 + width - 1, q], fill=color)
                d.rectangle([x1 - width + 1, p, x1, q], fill=color)
            p += on + off
    seg(x0, x1, True)
    seg(y0, y1, False)


def place(taken, cands, w, h, W, H):
    """겹치지 않는 라벨 자리를 고른다. 다 막혔으면 가장 덜 겹치는 자리 — 생략하지 않는다."""
    def hit(r):
        return sum(1 for o in taken
                   if not (r[2] <= o[0] or o[2] <= r[0] or r[3] <= o[1] or o[3] <= r[1]))
    best = None
    for x, y in cands:
        x = max(0, min(x, W - w)); y = max(0, min(y, H - h))
        r = (x, y, x + w, y + h)
        n = hit(r)
        if n == 0:
            taken.append(r); return r
        if best is None or n < best[0]:
            best = (n, r)
    taken.append(best[1]); return best[1]


def label_font(sz):
    for p in (r"C:\Windows\Fonts\malgun.ttf", r"C:\Windows\Fonts\consola.ttf"):
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, sz)
            except OSError:
                pass
    return ImageFont.load_default()


def num(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def draw_labels(im, labels, f8):
    """라벨을 **마지막에 한꺼번에** 찍는다. 바탕은 반투명 — 밑이 비쳐야 한다.
    자리를 못 찾아 멀리 놓인 라벨은 가느다란 지시선으로 제 상자와 이어 준다."""
    W, H = im.size
    taken, placed = [], []
    for L in labels:
        placed.append((place(taken, L["cands"], L["w"], L["h"], W, H), L))
    ov = Image.new("RGBA", im.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    for bx, L in placed:                       # 지시선 먼저 — 라벨 바탕에 깔린다
        rx, ry = L["ref"]
        if not (bx[0] - 8 <= rx <= bx[2] + 8 and bx[1] - 8 <= ry <= bx[3] + 8):
            od.line([bx[0] + L["w"] // 2, bx[1] + L["h"] // 2, rx, ry],
                    fill=tuple(L["parts"][0][1]) + (120,), width=1)
    for bx, L in placed:
        od.rectangle([bx[0], bx[1], bx[0] + L["w"], bx[1] + L["h"]],
                     fill=(0, 0, 0, 200))
    im = Image.alpha_composite(im.convert("RGBA"), ov).convert("RGB")
    d = ImageDraw.Draw(im)
    for bx, L in placed:
        x = bx[0] + 2
        for txt, col in L["parts"]:
            d.text((x, bx[1] - 1), txt, fill=col, font=f8)
            x += 6 * (len(txt) + 1)
    return im


def cands_around(x0, y0, x1, y1, w, h):
    """상자 둘레의 라벨 후보 자리. 가까운 곳부터, 안 되면 점점 밖으로."""
    c = [(x0, y0 - h - 1), (x0, y1 + 2), (x1 + 3, y0), (x0 - w - 3, y0),
         (x1 - w, y0 - h - 1), (x1 - w, y1 + 2), (x0, y0), (x0, y1 - h)]
    for k in (14, 28, 44):                      # 촘촘한 데서는 멀리라도 놓는다
        c += [(x0, y0 - h - k), (x0, y1 + k), (x1 + k, y0), (x0 - w - k, y0)]
    return c


def run(project: Project, only: str = "", scale: int = 2) -> int:
    out = project.preview_root / "boxes"
    data = ledgermod.load(project)
    rows = ledgermod.flat_rows(data)
    by = collections.defaultdict(list)
    for r in rows:
        by[r["file"]].append(r)

    os.makedirs(out, exist_ok=True)
    f8 = label_font(10)
    n = 0
    for fn, rs in sorted(by.items()):
        if only and only.lower() not in fn.lower():
            continue
        src_path = project.original_root / fn
        if not src_path.exists():
            print(f"  {fn:34} 원본 없음")
            continue
        im = Image.open(src_path).convert("RGBA")
        bg = Image.new("RGBA", im.size, (24, 24, 32, 255))
        bg.alpha_composite(im)
        im = bg.convert("RGB")
        W0, H0 = im.size
        S = max(1, scale)
        if S != 1:
            im = im.resize((W0 * S, H0 * S), Image.NEAREST)
        d = ImageDraw.Draw(im)

        def RS(b):
            """원본 좌표 상자 → 확대한 그림에서의 [x0,y0,x1,y1]"""
            return [b[0] * S, b[1] * S, (b[0] + b[2]) * S - 1, (b[1] + b[3]) * S - 1]

        cut = 0
        labels = []                     # 상자를 다 그린 뒤에 한꺼번에 찍는다
        runs = set()                    # run 은 상자가 하나다 — 첫 행에서만 그린다
        for r in rs:
            rid = r.get("run_id") or ""
            mem = [q for q in rs if q.get("run_id") == rid] if rid else [r]
            if rid:
                if rid in runs:
                    continue
                runs.add(rid)
            c = [num(r["crop_" + k]) for k in ("x", "y", "w", "h")]
            t = [num(r["text_" + k]) for k in ("x", "y", "w", "h")]
            # ★ 여기서 정렬을 또 적용하면 안 된다 — text_x,y 는 이미 좌상단이다.
            if None not in c:
                C = RS(c)
                d.rectangle(C, outline=MAGENTA, width=1)
            # source 도 같이 그린다 — 원문이 어디 있었는지 보여야 배치를 판단한다
            o = [num(r["source_" + k]) for k in ("x", "y", "box_w", "box_h")]
            if None not in o:
                O = RS(o)
                d.rectangle(O, outline=CYAN, width=1)
                oid = r.get("ocr_id") or ""
                if oid:
                    labels.append({
                        "parts": [(oid, CYAN)], "w": 6 * len(oid) + 5, "h": 11,
                        "cands": cands_around(*O, 6 * len(oid) + 5, 11),
                        "ref": (O[2], O[1]), "pri": 2})
            if None in t:
                continue
            over = (None not in c) and not (
                t[0] >= c[0] and t[1] >= c[1]
                and t[0] + t[2] <= c[0] + c[2] and t[1] + t[3] <= c[1] + c[3])
            col = RED if over else (WHITE if None not in c else DIM)
            cut += bool(over)
            T = RS(t)
            dashed(d, T, col, 2 if over else 1)
            bid, sty = r.get("box_id") or "?", r["style"]
            if len(mem) > 1:            # run — 상자는 하나, 스타일은 여럿
                bid = f"{mem[0]['box_id']}+{len(mem) - 1}"
                sty = "run:" + "·".join(dict.fromkeys(q["style"] for q in mem))
            lab = f"{bid} {sty}"
            labels.append({
                "parts": [(bid, WHITE), (sty, col)],
                "w": 6 * len(lab) + 5, "h": 11,
                "cands": cands_around(*T, 6 * len(lab) + 5, 11),
                "ref": (T[0], T[1]), "pri": 0})     # 글자 자리가 가장 중요하다

        labels.sort(key=lambda L: L["pri"])     # 중요한 라벨이 좋은 자리를 먼저 집는다
        im = draw_labels(im, labels, f8)
        dst = out / fn
        dst.parent.mkdir(parents=True, exist_ok=True)
        im.save(dst)
        print(f"  {fn:30} {len(rs):>3}행  잘림 {cut}")
        n += 1
    print(f"\n{n}장 저장 -> {out}")
    return 0
