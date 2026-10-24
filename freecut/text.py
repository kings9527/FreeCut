"""Text / subtitle rendering.

Field names follow ``MaterialText`` in ``videoeditor.dll``: the app has no
``bold`` / ``italic`` booleans - it stores ``bold_width`` and
``italic_degree`` - and the outline is ``border_color`` / ``border_width`` /
``border_alpha``.  ``shadow_*`` and ``background_*`` are flat on the material.

Font size is canvas-relative: ``font_size * canvas_height / 100`` reproduces
the default title size on a 1080p canvas (the shipped ``article_video``
template uses 15 for a caption and 19.95 for a title).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFont

FONT_DIRS = [
    os.path.expandvars(r"%WINDIR%\Fonts"),
    os.path.expanduser(r"~\AppData\Local\Microsoft\Windows\Fonts"),
]

_FALLBACK_FONTS = [
    "msyh.ttc",      # Microsoft YaHei
    "msyh.ttf",
    "msyhbd.ttc",
    "simhei.ttf",
    "simsun.ttc",
    "arial.ttf",
    "DejaVuSans.ttf",
    "NotoSansCJK-Regular.ttc",
]

_FONT_CACHE: Dict[Tuple[str, int], ImageFont.FreeTypeFont] = {}


# --------------------------------------------------------------------------- #
# fonts
# --------------------------------------------------------------------------- #
def _resolve_font_path(name: str) -> Optional[str]:
    if name and os.path.isfile(name):
        return name
    if not name:
        return None
    for d in FONT_DIRS:
        if not os.path.isdir(d):
            continue
        for ext in (".ttf", ".ttc", ".otf"):
            cand = os.path.join(d, name if name.lower().endswith(ext) else name + ext)
            if os.path.isfile(cand):
                return cand
    return None


def get_font(name: str, size: int) -> ImageFont.FreeTypeFont:
    size = max(1, int(size))
    key = (name or "", size)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    candidates = []
    resolved = _resolve_font_path(name)
    if resolved:
        candidates.append(resolved)
    for fb in _FALLBACK_FONTS:
        p = _resolve_font_path(fb)
        if p:
            candidates.append(p)
    for p in candidates:
        try:
            font = ImageFont.truetype(p, size)
            _FONT_CACHE[key] = font
            return font
        except Exception:
            continue
    font = ImageFont.load_default()
    _FONT_CACHE[key] = font  # type: ignore[assignment]
    return font


def _hex_to_rgb(value: str, default=(255, 255, 255)) -> Tuple[int, int, int]:
    v = (value or "").strip().lstrip("#")
    if len(v) == 8:  # the app may write AARRGGBB
        v = v[:6]
    if len(v) != 6:
        return default
    try:
        return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16))
    except ValueError:
        return default


def _shear(img: Image.Image, degree: float, pivot_y: int) -> Image.Image:
    """Fake an italic by shearing the rendered glyph bitmap."""
    if abs(degree) < 0.01:
        return img
    w, h = img.size
    pad = int(abs(degree) / 90.0 * h) + 2
    out = Image.new("RGBA", (w + pad * 2, h), (0, 0, 0, 0))
    shear = -degree / 90.0
    for y in range(h):
        dx = int(shear * (pivot_y - y))
        out.alpha_composite(img.crop((0, y, w, y + 1)), (pad + dx, y))
    return out


# --------------------------------------------------------------------------- #
# rendered layer
# --------------------------------------------------------------------------- #
@dataclass
class TextLayer:
    """An RGBA image plus the normalised point it is centred on."""

    image: np.ndarray
    anchor: Tuple[float, float]

    @property
    def size(self) -> Tuple[int, int]:
        return self.image.shape[1], self.image.shape[0]


def _is_cjk(ch: str) -> bool:
    return "\u4e00" <= ch <= "\u9fff"


def _layout_chars(lines, font, dr, letter_spacing: float, stroke_w: int):
    """Per-character layout with a shared token index for karaoke highlighting.

    A CJK character is its own token; a run of non-CJK characters (a word) is
    one token - the same split :func:`freecut.subtitles.split_words` uses, so the
    ``words[]`` array lines up with what is drawn.
    """
    char_lines: List[List[Tuple[str, int, float]]] = []
    widths: List[float] = []
    heights: List[float] = []
    token = 0
    for line in lines:
        items: List[Tuple[str, int, float]] = []
        in_run = False
        width = 0.0
        for ch in line:
            if ch.isspace():
                adv = dr.textlength(ch, font=font)
                items.append((ch, -1, adv))
                width += adv
                in_run = False
                continue
            if _is_cjk(ch):
                tok = token
                token += 1
                in_run = False
            else:
                if in_run:
                    tok = token - 1
                else:
                    tok = token
                    token += 1
                    in_run = True
            adv = dr.textlength(ch, font=font) + letter_spacing
            items.append((ch, tok, adv))
            width += adv
        char_lines.append(items)
        widths.append(width)
        box = dr.textbbox((0, 0), line or " ", font=font, stroke_width=stroke_w)
        heights.append(box[3] - box[1])
    return char_lines, widths, heights


class TextRenderer:
    """Renders text materials to RGBA layers, cached per (content, style)."""

    def __init__(self, canvas_w: int, canvas_h: int, supersample: int = 2) -> None:
        self.canvas_w = int(canvas_w)
        self.canvas_h = int(canvas_h)
        self.supersample = max(1, int(supersample))
        self._cache: Dict[tuple, np.ndarray] = {}

    def _px(self, font_size: float) -> int:
        return max(4, int(round(float(font_size) * self.canvas_h / 100.0)))

    def render(self, mat, anchor=(0.0, 0.0), active_word: Optional[int] = None) -> TextLayer:
        words = mat.get("words") or []
        style = (
            str(mat.get("content", "") or ""),
            str(mat.get("font_name", "")),
            round(float(mat.get("font_size", 15.0) or 15.0), 3),
            str(mat.get("text_color", "#FFFFFF")),
            round(float(mat.get("bold_width", 0.0) or 0.0), 3),
            round(float(mat.get("italic_degree", 0.0) or 0.0), 2),
            bool(mat.get("underline")),
            round(float(mat.get("underline_width", 0.0) or 0.0), 3),
            str(mat.get("border_color", "#000000")),
            round(float(mat.get("border_width", 0.0) or 0.0), 3),
            round(float(mat.get("border_alpha", 1.0) or 1.0), 3),
            str(mat.get("shadow_color", "#000000")),
            round(float(mat.get("shadow_alpha", 0.0) or 0.0), 3),
            round(float(mat.get("shadow_distance", 0.0) or 0.0), 3),
            round(float(mat.get("shadow_angle", 45.0) or 45.0), 1),
            str(mat.get("background_color", "")),
            round(float(mat.get("background_alpha", 0.0) or 0.0), 3),
            round(float(mat.get("background_round_radius", 0.0) or 0.0), 3),
            int(mat.get("alignment", 1) or 1),
            bool(mat.get("vertical")),
            round(float(mat.get("line_spacing", 0.02) or 0.02), 3),
            round(float(mat.get("letter_spacing", 0.0) or 0.0), 3),
            str(mat.get("highlight_color", "")) if words else "",
            active_word if words else None,
        )
        img = self._cache.get(style)
        if img is None:
            img = self._render_plain(style[0], mat, active_word=style[-1])
            if len(self._cache) > 256:
                self._cache.clear()
            self._cache[style] = img
        return TextLayer(image=img, anchor=(float(anchor[0]), float(anchor[1])))

    # -- drawing ------------------------------------------------------------
    def _render_plain(self, content: str, mat, active_word: Optional[int] = None) -> np.ndarray:
        ss = self.supersample
        px = self._px(float(mat.get("font_size", 15.0) or 15.0)) * ss
        font = get_font(str(mat.get("font_name", "")), px)
        color = _hex_to_rgb(str(mat.get("text_color", "#FFFFFF")))
        align = int(mat.get("alignment", 1) or 1)
        vertical = bool(mat.get("vertical"))
        line_spacing = float(mat.get("line_spacing", 0.02) or 0.02) * px
        letter_spacing = float(mat.get("letter_spacing", 0.0) or 0.0) * px
        bold_px = float(mat.get("bold_width", 0.0) or 0.0) * px * 0.06
        italic = float(mat.get("italic_degree", 0.0) or 0.0)
        border_px = float(mat.get("border_width", 0.0) or 0.0) * px
        border_color = _hex_to_rgb(str(mat.get("border_color", "#000000")))
        border_alpha = float(mat.get("border_alpha", 1.0) or 1.0)
        shadow_alpha = float(mat.get("shadow_alpha", 0.0) or 0.0)
        shadow_dist = float(mat.get("shadow_distance", 0.0) or 0.0) * px
        angle = float(mat.get("shadow_angle", 45.0) or 45.0)
        shadow_color = _hex_to_rgb(str(mat.get("shadow_color", "#000000")))
        bg_color = str(mat.get("background_color", "") or "")
        bg_alpha = float(mat.get("background_alpha", 0.0) or 0.0)
        radius = float(mat.get("background_round_radius", 0.0) or 0.0) * px

        if vertical:
            lines = [c for c in content] if content else [""]
        else:
            lines = content.split("\n") if content else [""]

        dummy = Image.new("RGBA", (8, 8))
        dr = ImageDraw.Draw(dummy)
        stroke_w = int(round(max(border_px, bold_px)))
        words_active = bool(mat.get("words"))
        highlight = _hex_to_rgb(str(mat.get("highlight_color", "#FFE066")), (255, 224, 102))
        # highlight_mode 是剪映草稿里的开关：0 = 不高亮（普通文本的默认值），
        # 非 0 才逐字着色。不读这个字段的话，任何带词级时间戳的文本
        # 都会被默认高亮色染掉第一个词（第 82 轮修正）。
        highlight_on = int(mat.get("highlight_mode") or 0) != 0

        char_lines = None
        widths, heights = [], []
        if words_active and not vertical:
            char_lines, widths, heights = _layout_chars(lines, font, dr, letter_spacing, stroke_w)
        else:
            for line in lines:
                box = dr.textbbox((0, 0), line or " ", font=font, stroke_width=stroke_w)
                extra = int(letter_spacing * max(0, len(line) - 1))
                widths.append(box[2] - box[0] + extra)
                heights.append(box[3] - box[1])
        text_w = max(widths) if widths else 1
        line_h = max(heights) if heights else px
        pad = int(px * 0.28 + stroke_w + abs(shadow_dist))
        box_w = int(text_w + pad * 2)
        box_h = int(len(lines) * line_h + (len(lines) - 1) * line_spacing + pad * 2)

        layer = Image.new("RGBA", (max(1, box_w), max(1, box_h)), (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)

        if bg_color and bg_alpha > 0:
            draw.rounded_rectangle(
                [0, 0, box_w - 1, box_h - 1],
                radius=int(max(0, radius)),
                fill=_hex_to_rgb(bg_color, (0, 0, 0)) + (int(round(255 * min(1.0, max(0.0, bg_alpha)))),),
            )

        dy = int(round(shadow_dist * np.sin(np.deg2rad(angle))))
        dx = int(round(shadow_dist * np.cos(np.deg2rad(angle))))
        stroke_fill = border_color + (int(round(255 * min(1.0, border_alpha))),) if stroke_w else None
        # 「空心字」：text_color 写成 #RRGGBB00（alpha=0）时正文不填充，只留描边。
        # _hex_to_rgb 会把 8 位裁成前 6 位，所以透明度必须在这里单独判。
        hollow = (str(mat.get("text_color") or "").strip().lstrip("#")[-2:].lower() == "00"
                  and stroke_w > 0)
        shadow_fill = shadow_color + (int(round(255 * min(1.0, shadow_alpha))),)

        y = pad
        for i, line in enumerate(lines):
            w = widths[i]
            x = pad if align == 0 else (box_w - pad - w if align == 2 else (box_w - w) / 2.0)
            if shadow_alpha > 0 and shadow_dist > 0.1:
                # 有字距时必须逐字画阴影：整行画出来的影子不带字距，
                # 和正文错位，看上去像重影（第 80 轮的实际缺陷）
                if letter_spacing > 0.5:
                    # 正文在下面也是逐字画的（char_lines 分支或字距分支），
                    # 阴影必须用同一套推进量，否则错位看起来就是重影
                    scx = x + dx
                    if char_lines is not None:
                        for ch, _token, advance in char_lines[i]:
                            draw.text(
                                (scx, y + dy), ch, font=font, fill=shadow_fill,
                                stroke_width=stroke_w,
                                stroke_fill=shadow_fill if stroke_w else None,
                            )
                            scx += advance
                    else:
                        for ch in line:
                            draw.text(
                                (scx, y + dy), ch, font=font, fill=shadow_fill,
                                stroke_width=stroke_w,
                                stroke_fill=shadow_fill if stroke_w else None,
                            )
                            scx += dr.textlength(ch, font=font) + letter_spacing
                else:
                    draw.text(
                        (x + dx, y + dy), line, font=font, fill=shadow_fill,
                        stroke_width=stroke_w, stroke_fill=shadow_fill if stroke_w else None,
                    )
            if char_lines is not None:
                cx = x
                for ch, token, advance in char_lines[i]:
                    if ch.strip():
                        col = (highlight if highlight_on else color) if (
                            active_word is not None and token == active_word) else color
                        draw.text((cx, y), ch, font=font,
                                  fill=col + (0 if hollow else 255,),
                                  stroke_width=stroke_w, stroke_fill=stroke_fill)
                    cx += advance
            elif letter_spacing > 0.5:
                cx = x
                for ch in line:
                    draw.text((cx, y), ch, font=font, fill=color + (0 if hollow else 255,),
                              stroke_width=stroke_w, stroke_fill=stroke_fill)
                    cx += dr.textlength(ch, font=font) + letter_spacing
            else:
                draw.text(
                    (x, y), line, font=font, fill=color + (0 if hollow else 255,),
                    stroke_width=stroke_w, stroke_fill=stroke_fill,
                )
            if mat.get("underline"):
                uy = y + line_h + stroke_w * 0.15
                lw = max(1, int(round(float(mat.get("underline_width", 0.0) or 0.0) * px)))
                draw.line([(x, uy), (x + w, uy)], fill=color + (255,), width=max(1, lw))
            y += line_h + line_spacing

        if abs(italic) > 0.01:
            layer = _shear(layer, italic, pivot_y=layer.size[1])

        arr = np.asarray(layer)
        if ss > 1:
            h, w = arr.shape[:2]
            arr = np.asarray(
                Image.fromarray(arr, "RGBA").resize((max(1, w // ss), max(1, h // ss)), Image.LANCZOS)
            )
        # Auto-fit: JianyingPro shrinks a caption that would overflow the canvas
        # (``line_max_width`` / ``force_apply_line_max_width``).  We scale the
        # whole layer down instead of re-flowing, which keeps the authored look.
        max_w = int(self.canvas_w * 0.94)
        max_h = int(self.canvas_h * 0.92)
        h, w = arr.shape[:2]
        if w > max_w or h > max_h:
            k = min(max_w / float(w), max_h / float(h))
            arr = np.asarray(
                Image.fromarray(arr, "RGBA").resize(
                    (max(1, int(w * k)), max(1, int(h * k))), Image.LANCZOS
                )
            )
        a = float(mat.get("text_alpha", 1.0) or 1.0) * float(mat.get("global_alpha", 1.0) or 1.0)
        if a < 1.0:
            arr = arr.copy()
            arr[..., 3] = (arr[..., 3].astype(np.float32) * max(0.0, min(1.0, a))).astype(np.uint8)
        return arr
