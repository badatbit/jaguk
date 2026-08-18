# -*- coding: utf-8 -*-
"""주입 — 원장 기반 번역 텍스트 렌더러 (furaiki3-l10n imgtext.render 이식).

행 좌표는 text 상자이고, 상자 안 정렬은 스타일의 `text_align` 이 정한다.

정렬 규약:
- `text_align` = [가로][세로], 가로 l/m/r · 세로 t/m/b. (`tm` 처럼 세로가
  앞에 온 표기는 자동으로 뒤집어 읽는다.)
- 가로는 **펜 원점** 기준: l = 펜이 상자 왼변, m = 어드밴스 폭 가운데,
  r = 펜 끝이 상자 오른변. 사이드베어링은 글꼴 것을 그대로 둔다.
- 세로는 **글꼴 메트릭 텍스트 상자**(어센트 꼭대기~디센트 바닥) 기준:
  같은 스타일·같은 상자면 글자 구성(받침·디센더 유무)과 무관하게
  **baseline 이 항상 같은 자리**다.

run: `run_id` 가 같은 행들은 한 상자를 공유하는 한 줄이다. 원장 순서대로
각 행을 제 스타일로 재서 이어 그리고, 합친 폭을 상자 안에서 정렬한다.

행 opacity(= 그 자리가 알파를 얼마나 먹었나)의 해석은 주입되는 자리의
베이스가 정한다 — 베이스 불투명 = 알파가 이미 구워진 사본이므로 잉크
RGB 만 ×f 로 칠하고 알파 채널은 유지(draw_rgb_ink), 베이스 투명 = 런타임에
알파를 먹는 스프라이트이므로 잉크에 알파를 실어 저장.

alpha_clear: crop 영역 알파를 비우고 text 상자에 커버리지 알파로 새긴 뒤,
crop−text 꼬리(장식)를 원본에서 잰 간격대로 새 글자 뒤에 붙인다.

base_root 의 무문자 이미지는 읽기만 한다. 합성 결과만 output_root 에
저장한다. `--on-original` 은 베이스를 원본으로 바꿔 preview 트리에 덧구움
비교본을 만든다.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from . import ledger as ledgermod
from .config import Project
from .fonts import FontCache

# 효과 표기 중 "그냥 글자"로 취급하는 것들. pixel_palette_estimate 는 OCR 이
# 색을 추정했다는 메모일 뿐 시각 효과가 아니다.
PLAIN_EFFECTS = {"", "none", "hard_outline", "pixel_palette_estimate"}

# 합성 이탤릭 기울기 (이탤릭 글꼴 파일이 없을 때 전단 변형으로 만든다).
ITALIC_SLANT = 0.2

# 슈퍼샘플 배율 — 글자 레이어를 4x 로 그려 한 번에 축소한다. 테두리(래스터
# 팽창)가 사선·원형 획에서 계단식으로 끊기는 문제의 근본 해법. 좌표·크기·
# 그림자를 전부 4x 로 스케일해 그리므로 배치는 1x 와 동일하고(¼px 정밀),
# 축소 후 AA 만 매끈해진다. 베이스에 직접 쓰는 alpha_clear·rgb_ink 는 글자
# 마스크만 4x 로 만들어 축소해 쓴다.
SS = 4

SHADOW_RE = re.compile(
    r"drop_shadow\(dx=(-?\d+),dy=(-?\d+),blur=([0-9.]+)"
    r"(?:,color=(#[0-9A-Fa-f]{8}))?\)"
)
ROTATE_RE = re.compile(r"rotate\(angle=(-?[0-9.]+)\)")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_path(root: Path, relative: str) -> Path:
    candidate = (root / Path(*relative.split("/"))).resolve()
    resolved_root = root.resolve()
    if candidate != resolved_root and resolved_root not in candidate.parents:
        raise ValueError(f"허용된 이미지 트리 밖의 경로입니다: {relative}")
    return candidate


class SkipRow(Exception):
    """행을 건너뛰되 이유를 모아 보고한다 (데이터가 아직 안 정리된 경우)."""


@dataclass
class RowSpec:
    box_id: str
    file: str
    run_id: str
    text: str
    box: tuple[int, int, int, int]          # text x, y, w, h
    crop: tuple[int, int, int, int] | None
    align: tuple[str, str]                  # (가로 l/m/r, 세로 t/m/b)
    family: str
    weight: int
    size: int
    fill: tuple[int, int, int, int]
    outline: tuple[int, int, int, int]
    outline_w: int
    effect: str
    canvas: tuple[int, int]
    slant: float = 0.0                      # 0 이 아니면 baseline 기준 전단
    vertical: bool = False                  # 세로쓰기 (한 글자씩 아래로)
    distribute: bool = False                # 글자를 상자 폭에 균등 분배
    flow: list | None = None                # 여러 줄 상자 — 어절 단위 자동 줄바꿈
    ss: int = 1                             # 이 스펙이 몇 배로 스케일됐나 (슈퍼샘플)


def parse_box(row: dict[str, str], prefix: str, box_id: str,
              wh=("w", "h")) -> tuple[int, int, int, int] | None:
    values = [row.get(k, "").strip() for k in
              (f"{prefix}x", f"{prefix}y", f"{prefix}{wh[0]}", f"{prefix}{wh[1]}")]
    if not any(values):
        return None
    if not all(values):
        raise ValueError(f"{box_id}: {prefix}* 좌표가 일부만 있습니다: {values}")
    x, y, w, h = map(int, values)
    if w <= 0 or h <= 0:
        raise ValueError(f"{box_id}: {prefix}* 크기가 0 이하입니다.")
    return x, y, w, h


def parse_color(value: str, alpha: int, field: str, box_id: str
                ) -> tuple[int, int, int, int]:
    value = value.strip()
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
        raise ValueError(f"{box_id}: {field}는 #RRGGBB 형식이어야 합니다: {value!r}")
    r, g, b = (int(value[i:i + 2], 16) for i in (1, 3, 5))
    return r, g, b, alpha


def parse_rgba8(value: str, box_id: str) -> tuple[int, int, int, int]:
    if not re.fullmatch(r"#[0-9A-Fa-f]{8}", value):
        raise ValueError(f"{box_id}: #RRGGBBAA 형식이 아닙니다: {value!r}")
    return tuple(int(value[i:i + 2], 16) for i in range(1, 9, 2))


def norm_align(value: str, box_id: str) -> tuple[str, str]:
    value = value.strip()
    if not value:
        raise SkipRow(f"{box_id}: 스타일에 text_align 이 없습니다")
    if len(value) == 2 and value[0] in "tb" and value[1] in "lmr":
        value = value[1] + value[0]  # 'tm' 처럼 세로가 앞인 표기
    if len(value) != 2 or value[0] not in "lmr" or value[1] not in "tmb":
        raise ValueError(f"{box_id}: 지원하지 않는 text_align {value!r}")
    return value[0], value[1]


def select_rows(flat_rows: list[dict], statuses: set[str], only: str
                ) -> list[dict]:
    return [
        row
        for row in flat_rows
        if row.get("status") in statuses
        and row.get("ko_text", "").strip()
        and (not only or only.lower() in row.get("file", "").lower())
    ]


def resolve(row: dict[str, str], styles: dict[str, dict]) -> RowSpec:
    box_id = row.get("box_id") or row.get("element_id") or "?"
    style_name = row.get("style", "").strip()
    if not style_name:
        raise SkipRow(f"{box_id}: style 이 비어 있습니다")
    if style_name not in styles:
        raise ValueError(f"{box_id}: 원장에 없는 스타일 {style_name!r}")
    style = styles[style_name]

    effect = (style.get("effect") or "").strip()

    box = parse_box(row, "text_", box_id)
    if box is None:
        raise SkipRow(f"{box_id}: text 상자가 없습니다")
    crop = parse_box(row, "crop_", box_id)

    # 스타일 offset — 상자 좌표(원문 유래)는 두고 스타일이 그리기 원점을
    # 옮긴다. 행마다 상자에 굽지 않고 스타일 한 곳에 원리로 기록하기 위한 것.
    off_x = int(str(style.get("offset_x") or "").strip() or 0)
    off_y = int(str(style.get("offset_y") or "").strip() or 0)
    if off_x or off_y:
        box = (box[0] + off_x, box[1] + off_y, box[2], box[3])

    align = norm_align(style.get("text_align", ""), box_id)

    family = (style.get("font_family_ko") or "").strip()
    weight_s = str(style.get("font_weight") or "").strip()
    size_s = str(style.get("font_size_px") or "").strip()
    if not family or not weight_s or not size_s:
        raise SkipRow(f"{box_id}: 스타일 {style_name} 의 글꼴 정의가 불완전합니다")

    opacity_s = (row.get("opacity") or "").strip() or "FF"
    if not re.fullmatch(r"[0-9A-Fa-f]{2}", opacity_s):
        raise ValueError(f"{box_id}: opacity 는 두 자리 hex 여야 합니다: {opacity_s!r}")
    opacity = int(opacity_s, 16)

    fill = parse_color(style.get("fill_rgb", ""), opacity, "fill_rgb", box_id)
    outline_value = (style.get("outline_rgb") or "").strip()
    outline_w = int(str(style.get("outline_weight_px") or "").strip() or 0)
    if outline_w and not outline_value:
        raise ValueError(f"{box_id}: outline_weight_px 만 있고 outline_rgb 가 없습니다")
    if outline_value and re.fullmatch(r"#[0-9A-Fa-f]{8}", outline_value):
        # #RRGGBBAA — 반투명 외곽선 (행 opacity 와 곱해진다)
        r_, g_, b_, a_ = (int(outline_value[i:i + 2], 16) for i in (1, 3, 5, 7))
        outline = (r_, g_, b_, a_ * opacity // 255)
    elif outline_value and re.fullmatch(r"#[0-9A-Fa-f]{6}", outline_value):
        outline = parse_color(outline_value, opacity, "outline_rgb", box_id)
    elif outline_w:
        # 두께가 있는데 색이 못 쓸 값이면 데이터 오류다.
        raise ValueError(f"{box_id}: outline_rgb 형식 오류: {outline_value!r}")
    else:
        # 두께 0 이면 outline 은 안 쓰인다 — OCR 잔재 색값이 남아 있어도 무시.
        outline = (0, 0, 0, 0)

    canvas_w = int(row.get("canvas_w", "").strip() or 0)
    canvas_h = int(row.get("canvas_h", "").strip() or 0)
    if not canvas_w or not canvas_h:
        raise ValueError(f"{box_id}: canvas 크기가 없습니다")

    font_style = (style.get("font_style") or "").strip()
    if font_style not in ("", "regular", "italic"):
        raise ValueError(f"{box_id}: 지원하지 않는 font_style {font_style!r}")
    vertical = (style.get("orientation") or "").strip() == "vertical"
    distribute = bool(style.get("distribute"))
    slant = ITALIC_SLANT if font_style == "italic" else 0.0
    if slant and outline_w:
        raise ValueError(
            f"{box_id}: italic 과 outline 동시 사용은 아직 지원하지 않습니다"
        )

    return RowSpec(
        box_id=box_id,
        file=row["file"],
        run_id=(row.get("run_id") or "").strip(),
        # 앞뒤 공백 보존 — run 구분자(' / ') 같은 멤버는 공백이 곧 내용이다.
        text=row["ko_text"],
        box=box,
        crop=crop,
        align=align,
        family=family,
        weight=int(weight_s),
        size=int(size_s),
        fill=fill,
        outline=outline,
        outline_w=outline_w,
        effect=effect,
        canvas=(canvas_w, canvas_h),
        slant=slant,
        vertical=vertical,
        distribute=distribute,
    )


_PROBE = ImageDraw.Draw(Image.new("L", (1, 1), 0))


def advance(text: str, font: ImageFont.FreeTypeFont) -> float:
    return _PROBE.textlength(text, font=font)


def _scale_effect(effect: str, s: int) -> str:
    """effect 문자열의 픽셀 인자만 s 배 — drop_shadow(dx,dy,blur). rotate 는 각도라 불변."""
    m = SHADOW_RE.fullmatch(effect)
    if not m:
        return effect
    dx, dy, blur = int(m.group(1)) * s, int(m.group(2)) * s, float(m.group(3)) * s
    color = f",color={m.group(4)}" if m.group(4) else ""
    return f"drop_shadow(dx={dx},dy={dy},blur={blur}{color})"


def scale_spec(spec: RowSpec, s: int) -> RowSpec:
    """스펙을 s 배 좌표계로 — 슈퍼샘플 레이어에 그릴 때 쓴다."""
    from dataclasses import replace

    def rect(r):
        return tuple(v * s for v in r) if r else r

    return replace(
        spec,
        box=rect(spec.box),
        crop=rect(spec.crop),
        size=spec.size * s,
        outline_w=spec.outline_w * s,
        canvas=(spec.canvas[0] * s, spec.canvas[1] * s),
        effect=_scale_effect(spec.effect, s),
        flow=[[v * s for v in b] for b in spec.flow] if spec.flow else None,
        ss=spec.ss * s,
    )


def metric_bounds(font: ImageFont.FreeTypeFont, stroke: int
                  ) -> tuple[int, int]:
    """세로 정렬용 (top, bottom) — baseline 기준 글꼴 메트릭 텍스트 상자.

    문자열 잉크가 아니라 글꼴의 어센트/디센트를 쓴다 — 같은 스타일이면
    어떤 문자열이든 같은 값이라 목록의 baseline 이 흔들리지 않는다.
    """
    ascent, descent = font.getmetrics()
    return -ascent - stroke, descent + stroke


def pen_and_baseline(
    box: tuple[int, int, int, int],
    align: tuple[str, str],
    adv: float,
    top: int,
    bottom: int,
) -> tuple[float, float]:
    """상자·정렬·어드밴스·잉크 상하한(baseline 기준)에서 펜 위치를 정한다."""
    bx, by, bw, bh = box
    h, v = align
    if h == "l":
        x = bx
    elif h == "m":
        x = bx + (bw - adv) / 2
    else:  # r
        x = bx + bw - adv
    if v == "t":
        y = by - top
    elif v == "b":
        y = by + bh - bottom
    else:  # m
        y = by + bh / 2 - (top + bottom) / 2
    return x, y


def snap(position: tuple[float, float]) -> tuple[int, int]:
    """정수 픽셀에 스냅 — 소수점 좌표는 서브픽셀 AA 로 획이 뭉개진다."""
    return round(position[0]), round(position[1])


def text_mask(
    size: tuple[int, int],
    position: tuple[float, float],
    spec: RowSpec,
    font: ImageFont.FreeTypeFont,
) -> Image.Image:
    """전체 캔버스 좌표의 글자 마스크. slant 는 baseline 을 축으로 전단한다."""
    position = snap(position)
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).text(
        position,
        spec.text,
        font=font,
        fill=255,
        anchor="ls",
        stroke_width=spec.outline_w,
        stroke_fill=255 if spec.outline_w else None,
    )
    if spec.slant:
        k = spec.slant
        baseline = position[1]
        # 출력 (x,y) ← 입력 (x + k·(y−baseline), y): baseline 위가 오른쪽으로 기운다.
        mask = mask.transform(
            size,
            Image.Transform.AFFINE,
            (1, k, -k * baseline, 0, 1, 0),
            resample=Image.Resampling.BILINEAR,
        )
    return mask


def stamp(layer: Image.Image, mask: Image.Image,
          color: tuple[int, int, int, int]) -> None:
    if color[3] != 255:
        mask = mask.point(lambda alpha: alpha * color[3] // 255)
    tint = Image.new("RGBA", layer.size, color[:3] + (0,))
    tint.putalpha(mask)
    layer.alpha_composite(tint)


def draw_plain(
    layer: Image.Image,
    position: tuple[float, float],
    spec: RowSpec,
    font: ImageFont.FreeTypeFont,
) -> None:
    if spec.slant:
        stamp(layer, text_mask(layer.size, position, spec, font), spec.fill)
        return
    ImageDraw.Draw(layer).text(
        snap(position),
        spec.text,
        font=font,
        fill=spec.fill,
        anchor="ls",
        stroke_width=spec.outline_w,
        stroke_fill=spec.outline if spec.outline_w else None,
    )


def draw_shadow(
    layer: Image.Image,
    position: tuple[float, float],
    spec: RowSpec,
    font: ImageFont.FreeTypeFont,
    dx: int,
    dy: int,
    blur: float,
    color: tuple[int, int, int, int],
) -> None:
    mask = text_mask(
        layer.size, (position[0] + dx, position[1] + dy), spec, font
    )
    if blur > 0:
        mask = mask.filter(ImageFilter.GaussianBlur(blur))
    stamp(layer, mask, color)


def draw_rotated(
    layer: Image.Image,
    spec: RowSpec,
    font: ImageFont.FreeTypeFont,
    angle: float,
) -> None:
    if spec.align != ("m", "m"):
        raise ValueError(f"{spec.box_id}: rotate effect 는 text_align mm 만 지원합니다")
    bbox = _PROBE.textbbox((0, 0), spec.text, font=font,
                           stroke_width=spec.outline_w)
    padding = max(4, spec.outline_w + 2)
    width = bbox[2] - bbox[0] + padding * 2
    height = bbox[3] - bbox[1] + padding * 2
    local = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    ImageDraw.Draw(local).text(
        (padding - bbox[0], padding - bbox[1]),
        spec.text,
        font=font,
        fill=spec.fill,
        stroke_width=spec.outline_w,
        stroke_fill=spec.outline if spec.outline_w else None,
    )
    rotated = local.rotate(
        angle,
        resample=Image.Resampling.BICUBIC,
        expand=True,
    )
    bx, by, bw, bh = spec.box
    destination = (
        round(bx + bw / 2 - rotated.width / 2),
        round(by + bh / 2 - rotated.height / 2),
    )
    layer.alpha_composite(rotated, destination)


def text_mask_ss(size, position, spec: RowSpec, fonts) -> Image.Image:
    """1x 캔버스용 글자 마스크를 SS 배로 그려 축소 — 사선 AA 가 매끈해진다."""
    font4 = fonts.get_key(spec.family, spec.weight, spec.size * SS)
    spec4 = scale_spec(spec, SS)
    x, y = snap(position)
    big = (size[0] * SS, size[1] * SS)
    mask = text_mask(big, (x * SS, y * SS), spec4, font4)
    return mask.resize(size, Image.Resampling.LANCZOS)


def draw_alpha_clear(
    image: Image.Image,
    original: Image.Image,
    spec: RowSpec,
    fonts,
) -> None:
    """crop 알파를 비우고 text 상자에 새기고, crop−text 꼬리를 옮겨 붙인다."""
    import numpy as np

    if spec.crop is None:
        raise ValueError(f"{spec.box_id}: alpha_clear 에는 crop 상자가 필요합니다")
    cx, cy, cw, ch = spec.crop
    tx, ty, tw, th = spec.box
    if (cx, cy, ch) != (tx, ty, th):
        raise ValueError(
            f"{spec.box_id}: alpha_clear 는 crop 과 text 의 x·y·h 가 같아야 합니다"
        )
    if cx + cw > image.width or cy + ch > image.height:
        raise ValueError(f"{spec.box_id}: crop 영역이 이미지 밖입니다")

    crop_box = (cx, cy, cx + cw, cy + ch)
    region_alpha = original.getchannel("A").crop(crop_box)

    # 꼬리(crop 에서 text 를 뺀 오른쪽)와 원래 간격을 원본에서 잰다.
    tail = None
    gap = 0
    if cw > tw:
        arr = np.asarray(region_alpha)
        cols = np.where((arr > 0).any(0))[0]
        text_cols = cols[cols < tw]
        tail_cols = cols[cols >= tw]
        if len(tail_cols):
            if not len(text_cols):
                raise ValueError(f"{spec.box_id}: text 상자 안에 원본 잉크가 없습니다")
            gap = int(tail_cols.min()) - int(text_cols.max()) - 1
            tail_left = cx + int(tail_cols.min())
            tail_right = cx + int(tail_cols.max()) + 1
            tail = (
                original.crop((tail_left, cy, tail_right, cy + ch)),
                tail_right - tail_left,
            )

    # 번역 글자판 (전체 캔버스 좌표로 그린 뒤 crop 영역만 쓴다) — 마스크는 SS 배
    font = fonts.get(spec)
    top, bottom = metric_bounds(font, spec.outline_w)
    adv = advance(spec.text, font)
    x, y = pen_and_baseline(spec.box, spec.align, adv, top, bottom)
    glyph_mask = text_mask_ss(image.size, (x, y), spec, fonts)
    glyph_region = glyph_mask.crop(crop_box)
    garr = np.asarray(glyph_region)
    gcols = np.where((garr > 0).any(0))[0]
    if not len(gcols):
        raise ValueError(f"{spec.box_id}: 렌더된 글자가 crop 영역에 없습니다")
    ink_right = cx + int(gcols.max())

    tail_x = None
    if tail is not None:
        tail_image, tail_w = tail
        tail_x = ink_right + 1 + gap
        if tail_x + tail_w > cx + cw:
            raise ValueError(
                f"{spec.box_id}: 글자가 길어 꼬리가 crop 을 벗어납니다 "
                f"(잉크 끝 {ink_right}, 꼬리 폭 {tail_w}, crop 끝 {cx + cw - 1})"
            )
    elif int(gcols.max()) >= cw:
        raise ValueError(f"{spec.box_id}: 글자가 crop 폭을 벗어납니다")

    # 알파 = 렌더된 커버리지 그대로. RGB 는 crop 전체를 글자색으로 통일한다 —
    # 알파 0 픽셀에 다른 색이 남으면 런타임 보간 때 가장자리로 배어 나온다.
    red, green, blue, alpha = image.split()
    alpha.paste(glyph_region, crop_box)
    for channel, value in zip((red, green, blue), spec.fill[:3]):
        channel.paste(value, crop_box)
    image.paste(Image.merge("RGBA", (red, green, blue, alpha)))

    if tail is not None:
        # 마스크 없이 사각형째 덮는다. 꼬리 알파를 마스크로 쓰면 α=0 바탕과
        # 섞여 결과 알파가 α²/255 로 꺾인다.
        image.paste(tail_image, (tail_x, cy))


def mean_alpha(image: Image.Image, box: tuple[int, int, int, int]) -> float:
    x, y, w, h = box
    region = image.getchannel("A").crop((x, y, x + w, y + h))
    histogram = region.histogram()
    total = sum(histogram)
    return sum(v * n for v, n in enumerate(histogram)) / total if total else 0.0


def draw_rgb_ink(
    image: Image.Image,
    spec: RowSpec,
    fonts,
) -> None:
    """알파가 이미 먹여진 사본에 글씨 쓰기 — RGB 만 칠하고 알파는 유지한다.

    원본 사본의 픽셀은 (원색×f, 판의 알파) 꼴이다. 그래서 잉크도 같은 색을
    ×f(행 opacity) 해 RGB 채널에만 블렌드하고, 알파 채널은 판 것을 그대로 둔다.
    """
    if spec.effect not in PLAIN_EFFECTS:
        raise ValueError(
            f"{spec.box_id}: 알파 사본 잉크에는 effect 를 지원하지 않습니다"
        )
    f = spec.fill[3] / 255
    ink = tuple(int(c * f) for c in spec.fill[:3])
    font = fonts.get(spec)
    top, bottom = metric_bounds(font, spec.outline_w)
    adv = advance(spec.text, font)
    position = pen_and_baseline(spec.box, spec.align, adv, top, bottom)
    mask = text_mask_ss(image.size, position, spec, fonts)
    red, green, blue, alpha = image.split()
    channels = [
        Image.composite(Image.new("L", image.size, value), channel, mask)
        for channel, value in zip((red, green, blue), ink)
    ]
    image.paste(Image.merge("RGBA", (*channels, alpha)))


def draw_vertical(layer: Image.Image, spec: RowSpec,
                  font: ImageFont.FreeTypeFont) -> None:
    """세로쓰기 — 글자를 위에서 아래로 쌓는다.

      글자 피치 = size + 6   /   단어 간격 = 피치 / 4 (공백 묶음 하나당)
      각 글자는 **잉크 상단**을 피치 격자에 맞춘다
    text_align 의 가로(l/m/r)는 열의 x, 세로(t/m/b)는 쌓기 시작점.
    """
    if spec.effect not in PLAIN_EFFECTS:
        raise ValueError(f"{spec.box_id}: 세로쓰기는 effect 를 지원하지 않습니다")
    import re as _re
    bx, by, bw, bh = spec.box
    size = spec.size
    pitch = size + 6 * spec.ss          # '+6' 은 1x 규격 — 슈퍼샘플 배율만큼
    gap = pitch // 4
    parts = [t for t in _re.split(r"(\s+)", spec.text) if t]
    total = sum(gap if t.isspace() else pitch * len(t) for t in parts)
    h, v = spec.align
    cx = {"l": bx + size / 2, "m": bx + bw / 2, "r": bx + bw - size / 2}[h]
    if v == "t":
        y = by
    elif v == "b":
        y = by + bh - total
    else:
        y = by + (bh - total) / 2
    d = ImageDraw.Draw(layer)
    for t in parts:
        if t.isspace():
            y += gap
            continue
        for ch in t:
            bbox = _PROBE.textbbox((0, 0), ch, font=font, anchor="ms",
                                   stroke_width=spec.outline_w)
            d.text(snap((cx, y - bbox[1])), ch, font=font, fill=spec.fill,
                   anchor="ms",
                   stroke_width=spec.outline_w,
                   stroke_fill=spec.outline if spec.outline_w else None)
            y += pitch


def draw_distributed(layer: Image.Image, spec: RowSpec,
                     font: ImageFont.FreeTypeFont) -> None:
    """글자를 상자 폭에 균등 분배 — 첫 글자는 왼변, 끝 글자는 오른변에 붙고
    사이 간격이 똑같다 (자간을 넓혀 로고 폭에 맞춘 원문 재현).
    세로는 text_align 의 세로 코드(t/m/b)를 따른다."""
    if spec.effect not in PLAIN_EFFECTS:
        raise ValueError(f"{spec.box_id}: 균등 분배는 effect 를 지원하지 않습니다")
    chars = [c for c in spec.text if not c.isspace()]
    if not chars:
        return
    bx, by, bw, bh = spec.box
    # 잉크 기준 균등 — 첫 글자 잉크가 왼변, 끝 글자 잉크가 오른변에 붙고,
    # 글자 **잉크 사이의 빈 공간**이 전부 같다. textbbox 는 래스터와 1~2px
    # 어긋나므로, 글자마다 실제로 래스터한 잉크 경계로 잰다.
    import numpy as _np
    pad = spec.size
    rasters = []
    for c in chars:
        tmp = Image.new("L", (spec.size * 3, spec.size * 3), 0)
        ImageDraw.Draw(tmp).text((pad, pad * 2), c, font=font, fill=255,
                                 anchor="ls",
                                 stroke_width=spec.outline_w,
                                 stroke_fill=255 if spec.outline_w else None)
        arr = _np.asarray(tmp)
        ys, xs = _np.where(arr > 16)
        # (펜 기준 왼쪽 오프셋, 잉크 폭, 잉크 위, 잉크 아래) — baseline 기준
        rasters.append((int(xs.min()) - pad, int(xs.max() - xs.min() + 1),
                        int(ys.min()) - pad * 2, int(ys.max()) - pad * 2))
    # 세로는 잉크가 아니라 글꼴 메트릭 상자 기준
    top, bottom = metric_bounds(font, spec.outline_w)
    v = spec.align[1]
    if v == "t":
        base = by - top
    elif v == "b":
        base = by + bh - bottom
    else:
        base = by + bh / 2 - (top + bottom) / 2
    total_ink = sum(r[1] for r in rasters)
    gap = (bw - total_ink) / (len(chars) - 1) if len(chars) > 1 else 0
    cursor = float(bx)
    d = ImageDraw.Draw(layer)
    for ch, (lsb, w, _t, _b) in zip(chars, rasters):
        d.text(snap((round(cursor) - lsb, base)), ch, font=font,
               fill=spec.fill, anchor="ls",
               stroke_width=spec.outline_w,
               stroke_fill=spec.outline if spec.outline_w else None)
        cursor += w + gap


def draw_flow(layer: Image.Image, spec: RowSpec,
              font: ImageFont.FreeTypeFont) -> None:
    """여러 줄 상자에 어절 단위 자동 줄바꿈 (단어 중간은 안 자른다).

    번역문 하나를 flow 의 줄 상자들에 차례로 채운다 — 번역이 바뀌어도
    상자는 그대로고 줄바꿈만 다시 계산된다. 넘치면 오류로 알린다.
    """
    if spec.effect not in PLAIN_EFFECTS:
        raise ValueError(f"{spec.box_id}: flow 는 effect 를 지원하지 않습니다")
    boxes = spec.flow
    tokens = spec.text.split()
    lines: list[str] = []
    cur = ""
    for tok in tokens:
        trial = (cur + " " + tok) if cur else tok
        if not cur or advance(trial, font) <= boxes[min(len(lines), len(boxes) - 1)][2]:
            cur = trial
        else:
            lines.append(cur)
            cur = tok
    if cur:
        lines.append(cur)
    if len(lines) > len(boxes):
        raise ValueError(
            f"{spec.box_id}: 줄바꿈 결과 {len(lines)}줄 — 상자 {len(boxes)}개 초과"
        )
    d = ImageDraw.Draw(layer)
    top, bottom = metric_bounds(font, spec.outline_w)
    for line, box in zip(lines, boxes):
        adv = advance(line, font)
        position = pen_and_baseline(tuple(box), spec.align, adv, top, bottom)
        d.text(snap(position), line, font=font, fill=spec.fill, anchor="ls",
               stroke_width=spec.outline_w,
               stroke_fill=spec.outline if spec.outline_w else None)


def render_single(layer: Image.Image, spec: RowSpec,
                  fonts: FontCache) -> None:
    font = fonts.get(spec)
    if spec.vertical:
        draw_vertical(layer, spec, font)
        return
    if spec.distribute:
        draw_distributed(layer, spec, font)
        return
    if spec.flow:
        draw_flow(layer, spec, font)
        return
    top, bottom = metric_bounds(font, spec.outline_w)
    adv = advance(spec.text, font)
    position = pen_and_baseline(spec.box, spec.align, adv, top, bottom)

    rotate_match = ROTATE_RE.fullmatch(spec.effect)
    shadow_match = SHADOW_RE.fullmatch(spec.effect)
    if rotate_match:
        draw_rotated(layer, spec, font, float(rotate_match.group(1)))
        return
    if shadow_match:
        dx, dy = int(shadow_match.group(1)), int(shadow_match.group(2))
        blur = float(shadow_match.group(3))
        color = (
            parse_rgba8(shadow_match.group(4), spec.box_id)
            if shadow_match.group(4)
            else spec.outline
        )
        draw_shadow(layer, position, spec, font, dx, dy, blur, color)
    elif spec.effect not in PLAIN_EFFECTS:
        raise ValueError(f"{spec.box_id}: 지원하지 않는 effect {spec.effect!r}")
    draw_plain(layer, position, spec, font)


def render_run(layer: Image.Image, members: list[RowSpec],
               fonts: FontCache) -> None:
    """한 상자를 공유하는 run — 멤버를 제 스타일로 재서 이어 그린다."""
    first = members[0]
    for member in members[1:]:
        if member.box != first.box:
            raise ValueError(
                f"run {first.run_id}: text 상자가 서로 다릅니다 "
                f"({first.box_id} vs {member.box_id})"
            )
        if member.align != first.align:
            raise ValueError(f"run {first.run_id}: text_align 이 서로 다릅니다")
    for member in members:
        if member.effect not in PLAIN_EFFECTS and not SHADOW_RE.fullmatch(member.effect):
            raise ValueError(
                f"run {first.run_id}: run 멤버는 effect 없음/그림자만 지원합니다 "
                f"({member.box_id}: {member.effect!r})"
            )

    parts = []
    top = 10 ** 9
    bottom = -(10 ** 9)
    total = 0.0
    for member in members:
        font = fonts.get(member)
        m_top, m_bottom = metric_bounds(font, member.outline_w)
        adv = advance(member.text, font)
        parts.append((member, font, adv))
        top = min(top, m_top)
        bottom = max(bottom, m_bottom)
        total += adv

    # 그림자 먼저 전부 깔고 글자를 얹는다 (원문도 그림자가 글자 밑이다).
    x0, y = pen_and_baseline(first.box, first.align, total, top, bottom)
    x = x0
    for member, font, adv in parts:
        shadow_match = SHADOW_RE.fullmatch(member.effect)
        if shadow_match:
            dx, dy = int(shadow_match.group(1)), int(shadow_match.group(2))
            blur = float(shadow_match.group(3))
            color = (
                parse_rgba8(shadow_match.group(4), member.box_id)
                if shadow_match.group(4)
                else member.outline
            )
            draw_shadow(layer, (x, y), member, font, dx, dy, blur, color)
        x += adv
    x = x0
    for member, font, adv in parts:
        draw_plain(layer, (x, y), member, font)
        x += adv


def validate_untouched(
    source: Image.Image,
    text_layer: Image.Image,
    output: Image.Image,
) -> None:
    if source.size != text_layer.size or source.size != output.size:
        raise RuntimeError("베이스, 텍스트 레이어, 출력 이미지의 크기가 다릅니다.")
    untouched_mask = text_layer.getchannel("A").point(
        lambda alpha: 255 if alpha == 0 else 0
    )
    diff = ImageChops.difference(source, output)
    if any(
        ImageChops.multiply(channel, untouched_mask).getbbox() is not None
        for channel in diff.split()
    ):
        raise RuntimeError("텍스트가 없는 영역에서 베이스 픽셀이 변경되었습니다.")


def apply_post(project: Project, source: Image.Image, output: Image.Image,
               posts: list[dict]) -> None:
    """파일 단위 후처리 (원장 최상위 "post" 목록).

    overlay: 글자를 그린 뒤 layer(무문자 트리의 RGBA 파일)를 알파 합성한다 —
    지도류에서 도로·해안 선화가 라벨 **위**층인 경우다. 글자 밖에서는
    베이스와 같은 픽셀이라 항등이다.
    선택 키 "alpha"(0~255, 기본 255)는 레이어의 알파 채널에 곱해 세기를 줄인다.
    선택 키 "alpha_white"는 순수 흰색(RGB 255,255,255) 픽셀만 따로 세기를 준다
    — 밝은 면은 살리고 어두운 외곽선만 눌러 글자 가독성을 얻는다.
    """
    for post in posts:
        if post["op"] != "overlay":
            raise ValueError(f"모르는 post op: {post['op']}")
        layer_path = safe_path(project.base_root, post["layer"])
        if not layer_path.exists():
            raise FileNotFoundError(f"post overlay 레이어가 없습니다: {layer_path}")
        layer = Image.open(layer_path).convert("RGBA")
        if layer.size != output.size:
            raise RuntimeError(
                f"overlay 레이어 크기 {layer.size} != 캔버스 {output.size}")
        alpha = post.get("alpha", 255)
        alpha_white = post.get("alpha_white")
        for name, v in (("alpha", alpha), ("alpha_white", alpha_white)):
            if v is not None and not (isinstance(v, int) and 0 <= v <= 255):
                raise ValueError(f"post overlay {name} 은 0~255 정수여야 한다: {v!r}")
        if alpha < 255 or alpha_white is not None:
            src_a = layer.getchannel("A")
            scaled = src_a.point(lambda a: a * alpha // 255)
            if alpha_white is not None:
                r, g, b, _ = layer.split()
                hard = [c.point(lambda v: 255 if v == 255 else 0) for c in (r, g, b)]
                white = ImageChops.multiply(ImageChops.multiply(hard[0], hard[1]), hard[2])
                white_a = src_a.point(lambda a: a * alpha_white // 255)
                scaled = Image.composite(white_a, scaled, white)
            layer.putalpha(scaled)
        output.alpha_composite(layer)


def render_file(
    project: Project,
    relative: str,
    specs: list[RowSpec],
    base_root: Path | None = None,
    output_root: Path | None = None,
    posts: list[dict] | None = None,
) -> Path:
    source_path = safe_path(base_root or project.base_root, relative)
    original_path = safe_path(project.original_root, relative)
    output_path = safe_path(output_root or project.output_root, relative)
    if not source_path.exists():
        raise FileNotFoundError(
            f"베이스가 없습니다: {source_path} — `typelet erase` 로 만들거나 "
            "손질본을 두세요."
        )

    source_bytes = source_path.read_bytes()
    source = Image.open(BytesIO(source_bytes)).convert("RGBA")

    original = None
    if any(spec.effect == "alpha_clear" for spec in specs):
        if not original_path.exists():
            raise FileNotFoundError(f"원본 이미지가 없습니다: {original_path}")
        original = Image.open(original_path).convert("RGBA")
        if original.size != source.size:
            raise RuntimeError(
                f"{relative}: 원본 크기 {original.size} != 베이스 크기 {source.size}"
            )

    for spec in specs:
        if spec.canvas != source.size:
            raise RuntimeError(
                f"{spec.box_id}: canvas {spec.canvas} != 베이스 {source.size}"
            )

    # 글자 레이어는 SS 배로 그려 한 번에 축소한다 (베이스 직접 기록 경로 제외)
    text_layer4 = Image.new(
        "RGBA", (source.width * SS, source.height * SS), (0, 0, 0, 0))
    fonts = FontCache(project)

    runs: dict[str, list[RowSpec]] = defaultdict(list)
    singles: list[RowSpec] = []
    for spec in specs:
        if spec.run_id:
            runs[spec.run_id].append(spec)
        else:
            singles.append(spec)

    for spec in singles:
        if spec.effect == "alpha_clear":
            draw_alpha_clear(source, original, spec, fonts)
        elif spec.fill[3] < 255 and mean_alpha(source, spec.box) > 128:
            # opacity 가 걸린 행 + 불투명 베이스 = 알파 먹은 사본에 주입.
            # 베이스가 투명한 스프라이트는 아래 일반 경로에서 잉크에 알파를
            # 실어 저장한다.
            draw_rgb_ink(source, spec, fonts)
        else:
            render_single(text_layer4, scale_spec(spec, SS), fonts)
    for members in runs.values():
        render_run(text_layer4, [scale_spec(m, SS) for m in members], fonts)

    text_layer = text_layer4.resize(source.size, Image.Resampling.LANCZOS)
    output = Image.alpha_composite(source, text_layer)
    validate_untouched(source, text_layer, output)
    if posts:
        apply_post(project, source, output, posts)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output.save(output_path)

    if sha256(source_path.read_bytes()) != sha256(source_bytes):
        raise RuntimeError(f"베이스 파일이 변경되었습니다: {source_path}")
    return output_path


def check_runs_complete(selected: list[dict], all_rows: list[dict]) -> None:
    """run 의 일부만 필터를 통과하면 반쪽 줄이 그려지므로 막는다."""
    full: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in all_rows:
        run_id = (row.get("run_id") or "").strip()
        if run_id:
            full[(row["file"], run_id)].add(row["box_id"])
    picked: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in selected:
        run_id = (row.get("run_id") or "").strip()
        if run_id:
            picked[(row["file"], run_id)].add(row["box_id"])
    for key, ids in picked.items():
        missing = full[key] - ids
        if missing:
            raise RuntimeError(
                f"run {key[1]} ({key[0]}) 의 멤버 일부가 필터에 걸러졌습니다: "
                f"빠진 행 {sorted(missing)} — status/번역을 맞추고 다시 실행"
            )


def run(project: Project, statuses: set[str] | None = None, only: str = "",
        list_only: bool = False, on_original: bool = False) -> int:
    statuses = statuses or {"render_ready"}
    data = ledgermod.load(project)
    styles = ledgermod.styles_map(data)
    all_rows = ledgermod.flat_rows(data)
    rows = select_rows(all_rows, statuses, only)
    check_runs_complete(rows, all_rows)

    flows = {r["box_id"]: r["flow"] for r in ledgermod.rows(data) if r.get("flow")}
    grouped: dict[str, list[RowSpec]] = defaultdict(list)
    skipped: list[str] = []
    for row in rows:
        try:
            rs = resolve(row, styles)
        except SkipRow as reason:
            skipped.append(str(reason))
            continue
        rs.flow = flows.get(rs.box_id)
        grouped[rs.file].append(rs)

    if not grouped and not skipped:
        print(f"렌더링 대상 없음: status={sorted(statuses)}, only={only!r}")
        return 1

    base_root = project.original_root if on_original else None
    output_root = (project.preview_root / "ko-on-original") if on_original else None
    if list_only:
        for relative, file_specs in sorted(grouped.items()):
            print(f"{relative}: {len(file_specs)}개 텍스트")
    else:
        posts_by_file: dict[str, list[dict]] = defaultdict(list)
        for post in data.get("post", []):
            posts_by_file[post["file"]].append(post)
        for relative, file_specs in sorted(grouped.items()):
            output_path = render_file(project, relative, file_specs, base_root,
                                      output_root, posts_by_file.get(relative))
            print(f"saved {output_path} ({len(file_specs)}개 텍스트)")

    if skipped:
        print(f"\n건너뜀 {len(skipped)}행 (데이터 미정리):")
        for reason in skipped:
            print(f"  - {reason}")
    return 0
