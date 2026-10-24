"""Segment animations (入场 / 出场 / 循环).

JianyingPro keeps animations in ``materials.material_animations[]``::

    {"id": "...", "type": "sticker_animation",
     "animations": [{"name": "渐显", "start": 0, "duration": 500000, ...}]}

The *resource* for each animation is a downloaded package, so the metadata the
app ships only carries names and ids.  FreeCut therefore implements a curated
vocabulary of the common animations itself and maps the app's names onto it,
which keeps drafts meaningful in both directions.

Every animation produces a transform/alpha offset for the segment at a given
local time; the renderer composes that with the segment's own keyframes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

#: app animation name -> our implementation, per animation *type*
#: (the app reuses the same name for the in and the out variant, e.g. 放大)
ANIMATION_KINDS = {
    # 入场 (type "in")
    "渐显": "fade_in",
    "放大": "zoom_in",
    "缩小": "zoom_out",
    "旋转": "rotate_in",
    "向右滑动": "slide_in_right",
    "向左滑动": "slide_in_left",
    "向上滑动": "slide_in_up",
    "向下滑动": "slide_in_down",
    "弹入": "pop_in",
    "向右擦除": "wipe_in_right",
    "向左擦除": "wipe_in_left",
    # 出场 (type "out")
    "渐隐": "fade_out",
    "渐隐 II": "fade_out",
    # 循环 (type "loop", text only)
    "闪烁": "blink",
    "摇摆": "sway",
    "旋转 II": "spin_loop",
}

#: 剪映 614 个动画名里，很多是"方向 + 动作"的组合。用关键词兜底比逐个枚举省事，
#: 也让新版本新增的名字不至于直接失效（先试精确名，再试规则）。
#: 顺序有意义：先匹配更具体的词（"甩入" 比 "入" 具体）。
NAME_RULES = (
    ("甩入", {"上": "slide_in_up", "下": "slide_in_down",
              "右上": "slide_in_right", "右下": "slide_in_right",
              "左上": "slide_in_left", "左下": "slide_in_left",
              "右": "slide_in_right", "左": "slide_in_left",
              "": "slide_in_down"}),
    ("甩出", {"上": "slide_out_up", "下": "slide_out_down",
              "右": "slide_out_right", "左": "slide_out_left",
              "": "slide_out_down"}),
    ("转入", {"上": "slide_in_up", "下": "slide_in_down",
              "右": "slide_in_right", "左": "slide_in_left",
              "": "rotate_in"}),
    ("转出", {"上": "slide_out_up", "下": "slide_out_down",
              "右": "slide_out_right", "左": "slide_out_left",
              "": "fade_out"}),
    ("翻入", {"": "rotate_in", "上": "slide_in_up", "下": "slide_in_down",
              "右": "slide_in_right", "左": "slide_in_left"}),
    ("翻出", {"": "fade_out"}),
    ("开幕", {"": "zoom_in"}),
    ("闭幕", {"": "zoom_out_end"}),
    ("抖", {"": "sway"}),
    ("震", {"": "sway"}),
    ("弹", {"": "pop_in"}),
    ("跳", {"": "pop_in"}),
    ("闪现", {"": "flash_in"}),
    ("渐显", {"": "fade_in"}),
    ("浮现", {"": "fade_in"}),
    ("淡入", {"": "fade_in"}),
    ("渐隐", {"": "fade_out"}),
    ("淡出", {"": "fade_out"}),
    ("擦除", {"右": "wipe_in_right", "左": "wipe_in_left", "": "wipe_in_right"}),
    ("旋转", {"": "rotate_in"}),
    ("缩放", {"": "zoom_in"}),
    ("放大", {"": "zoom_in"}),
    ("缩小", {"": "zoom_out"}),
    # 第二批：从 614 个名字里归纳出来的剩余常见动作
    ("斜切", {"": "wipe_in_right"}),
    ("雨刷", {"": "wipe_in_right"}),
    ("侧滑", {"右": "slide_in_right", "左": "slide_in_left", "": "slide_in_right"}),
    ("滑动", {"右": "slide_in_right", "左": "slide_in_left",
              "上": "slide_in_up", "下": "slide_in_down", "": "slide_in_right"}),
    ("滚动", {"上": "slide_in_up", "下": "slide_in_down", "": "slide_in_down"}),
    ("旋转", {"": "rotate_in"}),
    ("翻转", {"": "rotate_in"}),
    ("镜像", {"": "rotate_in"}),
    ("展开", {"": "zoom_in"}),
    ("扩大", {"": "zoom_in"}),
    ("收缩", {"": "zoom_out"}),
    ("闪", {"": "flash_in"}),
    ("闪入", {"": "flash_in"}),
    ("切闪", {"": "flash_in"}),
    ("分屏", {"": "zoom_in"}),
    ("分割", {"": "zoom_in"}),
    ("横移", {"": "slide_in_right"}),
    ("竖移", {"": "slide_in_up"}),
    ("钟摆", {"": "sway"}),
)


def match_rules(name: str, anim_type: str = "") -> Optional[str]:
    """按关键词把不认识的动画名归到已有引擎上。"""
    if not name:
        return None
    out = (anim_type or "").lower() == "out"
    for keyword, table in NAME_RULES:
        if keyword not in name:
            continue
        for direction, kind in table.items():
            if direction and direction not in name:
                continue
            if out and "in" in kind:
                mirrored = kind.replace("_in", "_out")
                if mirrored in ANIMATION_KINDS.values():
                    return mirrored
            return kind
    return None


#: out variants reuse in names, so they are resolved by the animation type
OUT_KINDS = {
    "渐隐": "fade_out",
    "放大": "zoom_out_end",
    "缩小": "zoom_out_end",
    "向右滑动": "slide_out_right",
    "向左滑动": "slide_out_left",
    "向上滑动": "slide_out_up",
    "向下滑动": "slide_out_down",
    "向右擦除": "wipe_out_right",
    "向左擦除": "wipe_out_left",
}

LOOP_KINDS = {
    "闪烁": "blink",
    "摇摆": "sway",
    "旋转": "spin_loop",
    "呼吸": "breathe",
}

#: our kind -> (app name, animation type)
KIND_TO_NAME = {
    "fade_in": ("渐显", "in"),
    "slide_in_right": ("向右滑动", "in"),
    "slide_in_left": ("向左滑动", "in"),
    "slide_in_up": ("向上滑动", "in"),
    "slide_in_down": ("向下滑动", "in"),
    "zoom_in": ("放大", "in"),
    "zoom_out": ("缩小", "in"),
    "rotate_in": ("旋转", "in"),
    "pop_in": ("弹入", "in"),
    "wipe_in_right": ("向右擦除", "in"),
    "wipe_in_left": ("向左擦除", "in"),
    "fade_out": ("渐隐", "out"),
    "slide_out_left": ("向左滑动", "out"),
    "slide_out_right": ("向右滑动", "out"),
    "slide_out_up": ("向上滑动", "out"),
    "slide_out_down": ("向下滑动", "out"),
    "zoom_out_end": ("缩小", "out"),
    "wipe_out_right": ("向右擦除", "out"),
    "wipe_out_left": ("向左擦除", "out"),
    "breathe": ("呼吸", "loop"),
    "sway": ("摇摆", "loop"),
    "blink": ("闪烁", "loop"),
    "spin_loop": ("旋转", "loop"),
}

#: which side of the segment the animation runs on
IN_ANIMATIONS = {
    "fade_in", "slide_in_right", "slide_in_left", "slide_in_up", "slide_in_down",
    "zoom_in", "zoom_out", "rotate_in", "pop_in", "wipe_in_right", "wipe_in_left",
}
OUT_ANIMATIONS = {
    "fade_out", "slide_out_left", "slide_out_right", "slide_out_up", "slide_out_down",
    "zoom_out_end", "wipe_out_right", "wipe_out_left",
}
LOOP_ANIMATIONS = {"breathe", "sway", "blink", "spin_loop"}


def resolve_kind(name: str, anim_type: str = "") -> Optional[str]:
    """Map an animation name + type onto our implementation."""
    if not name:
        return None
    t = (anim_type or "").lower()
    if t == "loop":
        return LOOP_KINDS.get(name) or ANIMATION_KINDS.get(name) or match_rules(name, anim_type)
    if t == "out":
        return (OUT_KINDS.get(name) or ANIMATION_KINDS.get(name)
                or match_rules(name, anim_type))
    if name in ANIMATION_KINDS:
        return ANIMATION_KINDS[name]
    for key, kind in ANIMATION_KINDS.items():
        if key and key in name:
            return kind
    exact = OUT_KINDS.get(name) or LOOP_KINDS.get(name)
    if exact:
        return exact
    return match_rules(name, anim_type)


def classify(name: str, anim_type: str = "") -> tuple:
    """Return ``(kind, anim_type)`` for a name that may be an app name or a kind.

    ``add_animation`` accepts both vocabularies, so it needs to know which one it
    got: ``渐隐`` is an *out* animation even though it is spelled like the other
    names, and ``fade_out`` implies its type.
    """
    if name in KIND_TO_NAME:
        app_name, implied = KIND_TO_NAME[name]
        return name, (anim_type or implied)
    for kind, (app_name, implied) in KIND_TO_NAME.items():
        if app_name == name:
            return kind, (anim_type or implied)
    kind = resolve_kind(name, anim_type)
    if kind is None:
        return None, (anim_type or "in")
    if kind in OUT_ANIMATIONS:
        implied = "out"
    elif kind in LOOP_ANIMATIONS:
        implied = "loop"
    else:
        implied = "in"
    return kind, (anim_type or implied)


@dataclass
class AnimState:
    """Transform/alpha deltas contributed by one animation at a given instant."""

    alpha: float = 1.0
    dx: float = 0.0        # normalised canvas offset
    dy: float = 0.0
    scale: float = 1.0
    rotation: float = 0.0  # degrees
    tint: float = 0.0      # 0..1 white flash

    def compose(self, other: "AnimState") -> "AnimState":
        return AnimState(
            alpha=self.alpha * other.alpha,
            dx=self.dx + other.dx,
            dy=self.dy + other.dy,
            scale=self.scale * other.scale,
            rotation=self.rotation + other.rotation,
            tint=min(1.0, self.tint + other.tint),
        )


def _ease_out(u: float) -> float:
    u = min(1.0, max(0.0, u))
    return 1.0 - (1.0 - u) ** 3


def _ease_in(u: float) -> float:
    u = min(1.0, max(0.0, u))
    return u * u * u


def evaluate(kind: str, u: float, amount: float = 1.0) -> AnimState:
    """Return the state of one animation at progress ``u`` in [0, 1]."""
    u = min(1.0, max(0.0, float(u)))
    a = max(0.0, float(amount))

    if kind == "fade_in":
        return AnimState(alpha=max(0.0, u) if a >= 1 else 1.0 - a * (1.0 - u))
    if kind == "fade_out":
        return AnimState(alpha=1.0 - u)
    if kind == "slide_in_right" or kind == "wipe_in_right":
        return AnimState(dx=-0.6 * a * (1.0 - _ease_out(u)), alpha=min(1.0, 0.2 + u))
    if kind == "slide_in_left" or kind == "wipe_in_left":
        return AnimState(dx=0.6 * a * (1.0 - _ease_out(u)), alpha=min(1.0, 0.2 + u))
    if kind == "slide_in_up":
        return AnimState(dy=-0.6 * a * (1.0 - _ease_out(u)), alpha=min(1.0, 0.2 + u))
    if kind == "slide_in_down":
        return AnimState(dy=0.6 * a * (1.0 - _ease_out(u)), alpha=min(1.0, 0.2 + u))
    if kind == "slide_out_left" or kind == "wipe_out_left":
        return AnimState(dx=-0.6 * a * _ease_in(u))
    if kind == "slide_out_right" or kind == "wipe_out_right":
        return AnimState(dx=0.6 * a * _ease_in(u))
    if kind == "slide_out_up":
        return AnimState(dy=-0.6 * a * _ease_in(u))
    if kind == "slide_out_down":
        return AnimState(dy=0.6 * a * _ease_in(u))
    if kind == "spin_loop":
        return AnimState(rotation=360.0 * u)
    if kind == "zoom_in":
        return AnimState(scale=1.0 - 0.5 * a * (1.0 - _ease_out(u)), alpha=min(1.0, 0.3 + u))
    if kind == "zoom_out":
        return AnimState(scale=1.0 + 0.5 * a * (1.0 - _ease_out(u)), alpha=min(1.0, 0.3 + u))
    if kind == "zoom_out_end":
        return AnimState(scale=1.0 - 0.6 * a * _ease_in(u), alpha=1.0 - 0.8 * u)
    if kind == "rotate_in":
        return AnimState(rotation=-90.0 * a * (1.0 - _ease_out(u)), alpha=min(1.0, 0.2 + u))
    if kind == "pop_in":
        # overshoot then settle
        s = 1.0 + 0.35 * a * math.sin(math.pi * min(1.0, u)) * (1.0 - u)
        return AnimState(scale=0.6 + 0.4 * _ease_out(u) + (s - 1.0), alpha=min(1.0, u * 1.6))
    if kind == "flash_in":
        return AnimState(alpha=min(1.0, u * 1.5), tint=max(0.0, 1.0 - u) * 0.8)
    if kind == "breathe":
        s = 1.0 + 0.04 * a * math.sin(2.0 * math.pi * u)
        return AnimState(scale=s)
    if kind == "sway":
        return AnimState(rotation=6.0 * a * math.sin(2.0 * math.pi * u))
    if kind == "blink":
        return AnimState(alpha=0.5 + 0.5 * abs(math.cos(math.pi * u)))
    return AnimState()


def animations_at(segment_animations, local_us: int, duration_us: int) -> AnimState:
    """Compose every animation attached to a segment at a local instant."""
    state = AnimState()
    for entry in segment_animations or []:
        kind = resolve_kind(str(entry.get("name") or entry.get("kind") or ""),
                            str(entry.get("type") or entry.get("anim_type") or ""))
        if kind is None:
            continue
        start = int(entry.get("start", 0) or 0)
        dur = int(entry.get("duration", 0) or 0)
        amount = float(entry.get("amount", 1.0) or 1.0)
        if kind in LOOP_ANIMATIONS:
            if duration_us <= 0:
                continue
            u = (local_us % duration_us) / float(duration_us)
        else:
            if dur <= 0:
                dur = min(500_000, max(1, duration_us)) if duration_us else 500_000
            if local_us < start:
                continue
            u = (local_us - start) / float(dur)
            if u > 1.0:
                continue
        state = state.compose(evaluate(kind, u, amount))
    return state
