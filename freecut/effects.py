"""Procedural video effects (特效).

JianyingPro ships its 特效 as downloadable shader packages referenced by
``resource_id``; we cannot redistribute those, so FreeCut implements an
**equivalent set of its own**: every effect here is plain numpy maths on the
composited layer, deterministic for a given frame time, and free to use.

They are applied in *canvas space* after the segment has been warped, so things
like glitch/shake/mosaic look the way an editor expects (they act on the frame,
not on the source clip).

Effects are attached as ``materials[]`` of type ``effect``::

    {"type": "effect", "kind": "glitch", "params": {"strength": 0.5}}

and are **not** representable in a 剪映 draft (it wants its own resource ids), so
the draft exporter drops them with a warning.
"""

from __future__ import annotations

import math
from typing import Callable, Dict, List, Sequence, Tuple

import numpy as np

Layer = Tuple[np.ndarray, np.ndarray]      # (rgb float32 HxWx3, alpha HxW)

#: kind -> (中文名, 默认参数, 说明)
EFFECT_SPEC: Dict[str, dict] = {
    "auto_matting": {
        "label": "智能抠像（近似）",
        "params": {"threshold": 0.18, "softness": 0.10, "feather": 2, "edge": 4},
        "hint": "按四边中位色当背景来抠；不做人像识别",
    },
    "smooth": {
        "label": "磨皮（近似）",
        "params": {"radius": 6, "strength": 0.45, "protect": 0.35},
        "hint": "只抹平低对比区域；不做人脸识别，对静物同样有效",
    },
    "sparkle": {
        "label": "星光",
        "params": {"density": 0.004, "size": 2, "strength": 0.9},
        "hint": "随机亮点，每 1/30 秒换一批位置",
    },
    "crt": {
        "label": "CRT 扫描线",
        "params": {"lines": 180, "depth": 0.25, "shift": 1},
        "hint": "横纹 + 轻微色边，监控画面感",
    },
    "duotone": {
        "label": "双色调",
        "params": {"amount": 1.0},
        "hint": "暗部/亮部两色之间插值",
    },
    "glow": {
        "label": "光晕",
        "params": {"threshold": 0.62, "strength": 0.6, "radius": 18},
        "hint": "高光外溢（会员光效的平价替代）",
    },
    "leak": {
        "label": "漏光",
        "params": {"strength": 0.45, "speed": 0.25, "hue": 0.08},
        "hint": "一角暖光带，随时间漂移",
    },
    "rgb_split": {
        "label": "色散",
        "params": {"amount": 4},
        "hint": "R/B 通道错开，边缘彩边",
    },
    "breathe": {
        "label": "呼吸",
        "params": {"depth": 0.06, "period_s": 3.0},
        "hint": "缓慢缩放，适合静态画面",
    },
    "flash": {
        "label": "闪白",
        "params": {"strength": 0.8, "period_s": 0.5},
        "hint": "按周期闪白（舞台/卡点常用）",
    },
    "glitch": {
        "label": "故障",
        "params": {"strength": 0.35, "block": 18},
        "hint": "RGB 错位 + 横向块抖动",
    },
    "shake": {
        "label": "抖动",
        "params": {"strength": 0.02},
        "hint": "整帧随机位移（以画幅比例计）",
    },
    "blur": {
        "label": "模糊",
        "params": {"radius": 8},
        "hint": "方框模糊，半径以像素计",
    },
    "mirror": {
        "label": "镜像",
        "params": {"axis": 0.0},
        "hint": "左右对称（axis=1 为上下）",
    },
    "film": {
        "label": "老电影",
        "params": {"grain": 0.08, "flicker": 0.05},
        "hint": "颗粒 + 闪烁 + 轻微褪色",
    },
    "mosaic": {
        "label": "马赛克",
        "params": {"block": 16},
        "hint": "像素块化",
    },
    "pulse": {
        "label": "脉冲缩放",
        "params": {"amount": 0.06, "period_s": 0.8},
        "hint": "以画面中心做呼吸式缩放",
    },
}


def _rng(t_us: int, salt: int = 0) -> np.random.Generator:
    """Deterministic per-frame randomness (30 fps quantisation)."""
    return np.random.default_rng((int(t_us // 33_333) + 1) * 7919 + salt)


def _clamp01(x: np.ndarray) -> np.ndarray:
    return np.clip(x, 0.0, 1.0, out=x)


# --------------------------------------------------------------------------- #
# individual effects: (rgb, alpha, t_us, params) -> (rgb, alpha)
# --------------------------------------------------------------------------- #
def _flash(rgb, alpha, t_us, p):
    period = max(0.05, float(p.get("period_s", 0.5)))
    strength = float(np.clip(float(p.get("strength", 0.8)), 0.0, 1.0))
    phase = (t_us / 1_000_000.0) / period
    pulse = max(0.0, math.sin(2.0 * math.pi * phase))
    k = strength * (pulse ** 3)
    if k <= 0.0:
        return rgb, alpha
    return _clamp01(rgb + (1.0 - rgb) * k), alpha


def _glitch(rgb, alpha, t_us, p):
    strength = float(np.clip(float(p.get("strength", 0.35)), 0.0, 1.0))
    block = max(2, int(p.get("block", 18)))
    rng = _rng(t_us, 1)
    h, w = rgb.shape[:2]
    out = rgb.copy()
    # RGB channel shift
    shift = int(round(strength * w * 0.02))
    if shift:
        out[..., 0] = np.roll(rgb[..., 0], shift, axis=1)
        out[..., 2] = np.roll(rgb[..., 2], -shift, axis=1)
    # random horizontal block displacement
    bands = max(1, h // block)
    for band in range(bands):
        if rng.random() > strength:
            continue
        y0 = band * block
        y1 = min(h, y0 + block)
        dx = int(rng.integers(-w // 20, w // 20 + 1))
        if dx:
            out[y0:y1] = np.roll(out[y0:y1], dx, axis=1)
    return _clamp01(out), alpha


def _shake(rgb, alpha, t_us, p):
    strength = float(np.clip(float(p.get("strength", 0.02)), 0.0, 0.2))
    rng = _rng(t_us, 2)
    h, w = rgb.shape[:2]
    dx = int(rng.integers(-1, 2) * strength * w)
    dy = int(rng.integers(-1, 2) * strength * h)
    if dx == 0 and dy == 0:
        return rgb, alpha
    return (np.roll(np.roll(rgb, dy, axis=0), dx, axis=1),
            np.roll(np.roll(alpha, dy, axis=0), dx, axis=1))


def _box_blur(img: np.ndarray, radius: int) -> np.ndarray:
    """Moving-average blur along both axes (separated, O(n) per axis)."""
    if radius <= 0:
        return img
    out = np.asarray(img, dtype=np.float32)
    for axis in (0, 1):
        n = out.shape[axis]
        pad = [(0, 0)] * out.ndim
        pad[axis] = (radius, radius)
        padded = np.pad(out, pad, mode="edge")
        # prefix sums need a leading zero so a window of 2r+1 elements is
        # C[i + 2r + 1] - C[i]
        zeros_shape = list(padded.shape)
        zeros_shape[axis] = 1
        csum = np.concatenate(
            [np.zeros(zeros_shape, dtype=np.float64),
             np.cumsum(padded, axis=axis, dtype=np.float64)],
            axis=axis,
        )
        # window for output i is padded[i : i + 2r + 1]; prefix sums need one
        # extra slot at the front, so index hi/lo explicitly instead of slicing
        # to the end (which loses the last N-1 outputs)
        hi_slice = [slice(None)] * out.ndim
        lo_slice = [slice(None)] * out.ndim
        hi_slice[axis] = slice(2 * radius + 1, 2 * radius + 1 + n)
        lo_slice[axis] = slice(0, n)
        hi = csum[tuple(hi_slice)]
        lo = csum[tuple(lo_slice)]
        out = ((hi - lo) / float(2 * radius + 1)).astype(np.float32)
    return out


def _blur(rgb, alpha, t_us, p):
    radius = max(0, int(p.get("radius", 8)))
    if radius == 0:
        return rgb, alpha
    return _clamp01(_box_blur(rgb, radius)), _box_blur(alpha, radius)



def _glow(rgb, alpha, t_us, p):
    """光晕（bloom）：只把高亮部分提出来模糊后叠回去。会员光效的平价替代。"""
    threshold = float(np.clip(float(p.get("threshold", 0.62)), 0.0, 0.95))
    strength = float(np.clip(float(p.get("strength", 0.6)), 0.0, 1.5))
    radius = max(1, int(p.get("radius", 18)))
    luma = (rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114)[..., None]
    mask = _clamp01((luma - threshold) / max(1e-3, 1.0 - threshold))
    bright = rgb * mask
    bloom = _box_blur(bright, radius)
    return _clamp01(rgb + bloom * strength), alpha


def _leak(rgb, alpha, t_us, p):
    """漏光：一角起的暖色光带，随时间缓慢移动。"""
    strength = float(np.clip(float(p.get("strength", 0.45)), 0.0, 1.0))
    speed = float(p.get("speed", 0.25))
    hue = float(np.clip(float(p.get("hue", 0.08)), -0.5, 0.5))
    h, w = rgb.shape[:2]
    yy = np.linspace(0.0, 1.0, h, dtype=np.float32)[:, None]
    xx = np.linspace(0.0, 1.0, w, dtype=np.float32)[None, :]
    phase = (t_us / 1_000_000.0) * speed
    cx = 0.15 + 0.7 * (0.5 + 0.5 * math.sin(2.0 * math.pi * phase))
    cy = 0.15 + 0.2 * math.cos(2.0 * math.pi * phase * 0.7)
    dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    band = _clamp01(1.0 - dist / 0.75) ** 2
    tint = np.array([1.0, 0.75 + hue, 0.45 + hue * 0.5], dtype=np.float32)
    glow = band[..., None] * tint[None, None, :] * strength
    return _clamp01(rgb + glow), alpha


def _rgb_split(rgb, alpha, t_us, p):
    """色散：R/B 通道左右错开，边缘出现彩边。"""
    amount = max(0, int(p.get("amount", 4)))
    if amount == 0:
        return rgb, alpha
    out = rgb.copy()
    out[..., 0] = np.roll(rgb[..., 0], amount, axis=1)
    out[..., 2] = np.roll(rgb[..., 2], -amount, axis=1)
    return _clamp01(out), alpha


def _breathe(rgb, alpha, t_us, p):
    """呼吸缩放：整帧以中心为轴缓慢放大缩小。"""
    depth = float(np.clip(float(p.get("depth", 0.06)), 0.0, 0.4))
    period = max(0.2, float(p.get("period_s", 3.0)))
    phase = (t_us / 1_000_000.0) / period
    scale = 1.0 + depth * math.sin(2.0 * math.pi * phase)
    h, w = rgb.shape[:2]
    scale = max(0.5, scale)
    # 用最近邻 + 裁剪模拟缩放（比插值快，视觉上够用）
    nw, nh = max(1, int(w / scale)), max(1, int(h / scale))
    x0, y0 = (w - nw) // 2, (h - nh) // 2
    crop = rgb[y0:y0 + nh, x0:x0 + nw]
    ys = (np.linspace(0, nh - 1, h)).astype(np.int32)
    xs = (np.linspace(0, nw - 1, w)).astype(np.int32)
    out = crop[ys][:, xs]
    return _clamp01(out), alpha


def _sparkle(rgb, alpha, t_us, p):
    """星光粒子：按时间种子撒亮点，带一点衰减光晕。"""
    density = float(np.clip(float(p.get("density", 0.004)), 0.0, 0.05))
    size = max(1, int(p.get("size", 2)))
    strength = float(np.clip(float(p.get("strength", 0.9)), 0.0, 1.0))
    h, w = rgb.shape[:2]
    count = max(1, int(density * h * w))
    rng = _rng(t_us // 33_000, 11)          # 每 1/30 秒换一批位置
    ys = rng.integers(0, h, size=count)
    xs = rng.integers(0, w, size=count)
    dots = np.zeros((h, w), dtype=np.float32)
    for dy in range(-size, size + 1):
        for dx in range(-size, size + 1):
            wy = np.clip(ys + dy, 0, h - 1)
            wx = np.clip(xs + dx, 0, w - 1)
            falloff = 1.0 - (abs(dy) + abs(dx)) / (2.0 * size + 1.0)
            dots[wy, wx] = np.maximum(dots[wy, wx], falloff)
    glow = _box_blur(dots[..., None].repeat(3, axis=2), max(1, size * 2))
    add = (dots[..., None] * 0.9 + glow * 0.6) * strength
    return _clamp01(rgb + add), alpha


def _crt(rgb, alpha, t_us, p):
    """扫描线 + 轻微色边：老显示器/监控画面感。"""
    lines = max(2, int(p.get("lines", 180)))
    depth = float(np.clip(float(p.get("depth", 0.25)), 0.0, 1.0))
    shift = max(0, int(p.get("shift", 1)))
    h, w = rgb.shape[:2]
    rows = np.arange(h, dtype=np.float32)
    # 每条扫描线的像素周期；至少 2px，否则在某些尺寸下 period=1，
    # cos(2π·整数) 恒等于 1 —— 效果就会完全消失（第一版踩过）
    period_px = max(2.0, 2.0 * h / max(1, lines))
    scan = 0.5 + 0.5 * np.cos(2.0 * np.pi * rows / period_px)
    scan = scan[:, None, None]
    out = rgb * (1.0 - depth * (1.0 - scan))
    if shift:
        out = out.copy()
        out[..., 0] = np.roll(out[..., 0], shift, axis=1)
        out[..., 2] = np.roll(out[..., 2], -shift, axis=1)
    return _clamp01(out), alpha


def _duotone(rgb, alpha, t_us, p):
    """双色调：按亮度在两色之间插值（暗部色 → 亮部色）。"""
    dark = np.asarray(p.get("dark", (0.05, 0.08, 0.18)), dtype=np.float32)
    light = np.asarray(p.get("light", (1.0, 0.82, 0.45)), dtype=np.float32)
    amount = float(np.clip(float(p.get("amount", 1.0)), 0.0, 1.0))
    luma = (rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114)[..., None]
    graded = dark[None, None, :] + (light - dark)[None, None, :] * luma
    return _clamp01(rgb * (1.0 - amount) + graded * amount), alpha


def _smooth(rgb, alpha, t_us, p):
    """磨皮（近似）：只抹平**低对比区域**，边缘和五官保持清晰。

    这不是人脸识别版的美颜——它不知道脸在哪，只是"平坦区域变细腻、
    边缘不动"。所以它对人像和静物都有效，也不会把眼睛磨成一片。
    """
    radius = max(1, int(p.get("radius", 6)))
    strength = float(np.clip(float(p.get("strength", 0.45)), 0.0, 1.0))
    protect = float(np.clip(float(p.get("protect", 0.35)), 0.01, 0.95))
    blurred = _box_blur(rgb, radius)
    # 取值范围判断（双边滤波的简化版）：和邻域均值差得多的当边缘，保留；
    # 差得少的当噪点/纹理，抹平。第一版用梯度判断，结果把噪点也当成边缘了。
    delta = np.abs(rgb - blurred).mean(axis=2, keepdims=True)
    weight = strength * np.clip(1.0 - (delta / max(1e-3, protect)) ** 2, 0.0, 1.0)
    return _clamp01(rgb * (1.0 - weight) + blurred * weight), alpha


def _auto_matting(rgb, alpha, t_us, p):
    """智能抠像（近似）：从画面四边猜背景色，把相近的像素抠掉。

    剪映的"智能抠像"是人像分割模型；这里没有模型，做的是**自适应背景键控**：
    取边框像素的中位色当背景，按颜色距离生成 alpha。对被摄主体居中、
    背景相对均匀的素材（口播、开箱、纯色/虚化背景）够用；
    主体贴边、背景复杂的素材会很糟——所以它叫"近似"。
    """
    threshold = float(np.clip(float(p.get("threshold", 0.18)), 0.01, 1.0))
    softness = float(np.clip(float(p.get("softness", 0.10)), 0.005, 0.6))
    feather = max(0, int(p.get("feather", 2)))
    edge = max(1, int(p.get("edge", 4)))

    h, w = rgb.shape[:2]
    edge = min(edge, max(1, min(h, w) // 8))
    border = np.concatenate([
        rgb[:edge].reshape(-1, 3), rgb[-edge:].reshape(-1, 3),
        rgb[:, :edge].reshape(-1, 3), rgb[:, -edge:].reshape(-1, 3),
    ])
    key = np.median(border, axis=0)
    dist = np.sqrt(((rgb - key[None, None, :]) ** 2).sum(axis=2))
    lo = max(1e-3, threshold)
    hi = lo + max(1e-3, softness)
    keep = np.clip((dist - lo) / (hi - lo), 0.0, 1.0)
    out_alpha = alpha * keep
    if feather > 0:
        out_alpha = _box_blur(out_alpha, feather)
    return rgb, np.clip(out_alpha, 0.0, 1.0)

def _mirror(rgb, alpha, t_us, p):
    vertical = bool(p.get("axis", 0.0))
    out = rgb.copy()
    a = alpha.copy()
    if vertical:
        h = out.shape[0]
        out[h // 2:] = out[: h - h // 2][::-1]
        a[h // 2:] = a[: h - h // 2][::-1]
    else:
        w = out.shape[1]
        out[:, w // 2:] = out[:, : w - w // 2][:, ::-1]
        a[:, w // 2:] = a[:, : w - w // 2][:, ::-1]
    return out, a


def _film(rgb, alpha, t_us, p):
    grain = float(np.clip(float(p.get("grain", 0.08)), 0.0, 0.5))
    flicker = float(np.clip(float(p.get("flicker", 0.05)), 0.0, 0.5))
    rng = _rng(t_us, 3)
    # 轻度褪色（抬黑 + 降饱和），再叠颗粒与整帧亮度抖动
    luma = (rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114)[..., None]
    out = rgb * 0.82 + luma * 0.18
    out = out * 0.94 + 0.06
    noise = rng.normal(0.0, grain, size=out.shape[:2] + (1,)).astype(np.float32)
    out = out + noise
    out = out * (1.0 + rng.uniform(-flicker, flicker))
    return _clamp01(out), alpha


def _mosaic(rgb, alpha, t_us, p):
    block = max(2, int(p.get("block", 16)))
    h, w = rgb.shape[:2]
    if block <= 1 or h < block or w < block:
        return rgb, alpha
    hh, ww = h // block * block, w // block * block
    small = rgb[:hh, :ww].reshape(hh // block, block, ww // block, block, 3).mean(axis=(1, 3))
    up = np.repeat(np.repeat(small, block, axis=0), block, axis=1).astype(np.float32)
    out = rgb.copy()
    out[:hh, :ww] = up
    return out, alpha


def _pulse(rgb, alpha, t_us, p):
    amount = float(np.clip(float(p.get("amount", 0.06)), 0.0, 0.5))
    period = max(0.05, float(p.get("period_s", 0.8)))
    phase = (t_us / 1_000_000.0) / period
    k = 1.0 + amount * math.sin(2.0 * math.pi * phase)
    h, w = rgb.shape[:2]
    ch, cw = h / 2.0, w / 2.0
    ys = np.clip(((np.arange(h) - ch) / k + ch).astype(np.int32), 0, h - 1)
    xs = np.clip(((np.arange(w) - cw) / k + cw).astype(np.int32), 0, w - 1)
    return rgb[ys][:, xs], alpha[ys][:, xs]


_FUNCS: Dict[str, Callable] = {
    "auto_matting": _auto_matting,
    "smooth": _smooth,
    "sparkle": _sparkle,
    "crt": _crt,
    "duotone": _duotone,
    "glow": _glow,
    "leak": _leak,
    "rgb_split": _rgb_split,
    "breathe": _breathe,
    "flash": _flash,
    "glitch": _glitch,
    "shake": _shake,
    "blur": _blur,
    "mirror": _mirror,
    "film": _film,
    "mosaic": _mosaic,
    "pulse": _pulse,
}


def available() -> List[str]:
    return list(EFFECT_SPEC)


def label(kind: str) -> str:
    return str(EFFECT_SPEC.get(kind, {}).get("label", kind))


def apply_effect(kind: str, rgb: np.ndarray, alpha: np.ndarray, t_us: int,
                 params: dict) -> Layer:
    fn = _FUNCS.get(kind)
    if fn is None:
        return rgb, alpha
    return fn(rgb, alpha, int(t_us), dict(params or {}))


def apply_chain(effects: Sequence[dict], rgb: np.ndarray, alpha: np.ndarray,
                t_us: int) -> Layer:
    """Apply every attached effect in order."""
    out_rgb, out_a = rgb, alpha
    for eff in effects or []:
        kind = str(eff.get("kind") or "")
        out_rgb, out_a = apply_effect(kind, out_rgb, out_a, t_us, eff.get("params") or {})
    return out_rgb, out_a
