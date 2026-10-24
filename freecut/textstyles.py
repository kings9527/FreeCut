"""花字 / 文字模板 (styled caption presets).

JianyingPro ships 100+ 花字 presets; each one is just a bundle of
`materials.texts[]` style fields (colour, stroke, shadow, background plate,
letter spacing).  FreeCut keeps the same field names, so a preset is a plain
dict that gets written onto the text material.

Presets live in ``data/text_styles.json``; ``font_size`` there is **percent of
canvas height** (15 → 15%), matching the renderer's own convention.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Dict, List, Optional

_DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "text_styles.json")

#: fields a preset is allowed to touch (anything else in the file is ignored)
STYLE_FIELDS = (
    "font_name", "font_size", "text_color", "bold_width", "italic_degree",
    "underline", "underline_width", "border_color", "border_width", "border_alpha",
    "shadow_color", "shadow_alpha", "shadow_distance", "shadow_angle",
    "background_color", "background_alpha", "background_round_radius",
    "alignment", "vertical", "line_spacing", "letter_spacing", "highlight_color",
)


@lru_cache(maxsize=1)
def presets() -> List[Dict]:
    try:
        with open(_DATA, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    out = []
    for item in data.get("presets") or []:
        clean = {k: v for k, v in item.items() if k in STYLE_FIELDS or k in ("name", "group")}
        if clean.get("name"):
            out.append(clean)
    return out


def names() -> List[str]:
    return [p["name"] for p in presets()]


def find(name: str) -> Optional[Dict]:
    for item in presets():
        if item["name"] == name:
            return dict(item)
    return None


def groups() -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for item in presets():
        out.setdefault(str(item.get("group") or "其它"), []).append(item["name"])
    return out


#: 字距上限。第 81 轮修好了"阴影不跟随字距"的错位问题，
#: 但仍不鼓励极端值（0.3 以上在 1080p 上会显得字被拉开）。
SAFE_LETTER_SPACING = 0.4
_WARNED: set = set()


def apply(mat, name: str) -> bool:
    """Write the preset ``name`` onto a text material. Returns success."""
    preset = find(name)
    if not preset or mat is None:
        return False
    for key, value in preset.items():
        if key in ("name", "group"):
            continue
        # 过大的字距会让逐字绘制出现重影（第 80 轮实测），这里夹到安全范围
        if key == "letter_spacing" and isinstance(value, (int, float)):
            value = min(float(value), SAFE_LETTER_SPACING)
        mat[key] = value
    return True


def style_of(mat) -> str:
    """Best-effort reverse lookup so the UI can show the current preset."""
    if mat is None:
        return ""
    for preset in presets():
        if all(
            _same(mat.get(k), v)
            for k, v in preset.items()
            if k not in ("name", "group")
        ):
            return preset["name"]
    return ""


def _same(current, expected) -> bool:
    if isinstance(expected, bool) or isinstance(expected, str):
        return current == expected
    try:
        if current is None:
            return False
        return abs(float(current) - float(expected)) < 1e-4
    except (TypeError, ValueError):
        return False


def summary() -> str:
    return f"{len(presets())} 个文字模板 / {len(groups())} 组"
