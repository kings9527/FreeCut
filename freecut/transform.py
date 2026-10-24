"""Geometry helpers: fitting a source into the canvas and compositing layers.

The placement model mirrors the draft schema's ``segment.clip``::

    {"alpha": 1.0, "rotation": 0.0,
     "scale": {"x": 1.0, "y": 1.0},
     "transform": {"x": 0.0, "y": 0.0},
     "flip": {"horizontal": false, "vertical": false}}

``scale`` of 1.0 means "fit the source inside the canvas" (contain), and
``transform`` is a normalised offset where 1.0 equals one canvas width/height.
"""

from __future__ import annotations

import os
from typing import Optional, Tuple

import numpy as np

#: OpenCV is a fast path only (7.8 ms vs 346 ms for the numpy sampler at 720p),
#: so it stays optional: set ``FREECUT_NO_CV2=1`` to force the numpy path.
#: It is imported **lazily** — the import alone costs ~400 ms, which used to be
#: paid before the first window was mapped.
_cv2 = None
_cv2_tried = False


def _get_cv2():
    """Import OpenCV on first use so the GUI window can appear sooner."""
    global _cv2, _cv2_tried
    if _cv2_tried:
        return _cv2
    _cv2_tried = True
    if os.environ.get("FREECUT_NO_CV2"):
        return None
    try:  # pragma: no cover - depends on the environment
        import cv2 as _mod  # type: ignore

        _cv2 = _mod
    except Exception:
        _cv2 = None
    return _cv2


def _warp_bilinear(src: np.ndarray, matrix: np.ndarray, width: int, height: int) -> np.ndarray:
    """Inverse-map bilinear warp, the pure-numpy stand-in for ``cv2.warpAffine``."""
    m = np.vstack([matrix, [0.0, 0.0, 1.0]])
    try:
        inv = np.linalg.inv(m)[:2]
    except np.linalg.LinAlgError:
        return np.zeros((height, width) + src.shape[2:], dtype=np.float32)

    ys, xs = np.mgrid[0:height, 0:width].astype(np.float32)
    sx = inv[0, 0] * xs + inv[0, 1] * ys + inv[0, 2]
    sy = inv[1, 0] * xs + inv[1, 1] * ys + inv[1, 2]

    sh, sw = src.shape[:2]
    valid = (sx >= 0) & (sx <= sw - 1) & (sy >= 0) & (sy <= sh - 1)
    sx = np.clip(sx, 0, sw - 1)
    sy = np.clip(sy, 0, sh - 1)

    x0 = np.floor(sx).astype(np.int32)
    y0 = np.floor(sy).astype(np.int32)
    x1 = np.minimum(x0 + 1, sw - 1)
    y1 = np.minimum(y0 + 1, sh - 1)
    fx = (sx - x0)[..., None]
    fy = (sy - y0)[..., None]

    src32 = src.astype(np.float32)
    if src32.ndim == 2:
        src32 = src32[..., None]
    top = src32[y0, x0] * (1 - fx) + src32[y0, x1] * fx
    bot = src32[y1, x0] * (1 - fx) + src32[y1, x1] * fx
    out = top * (1 - fy) + bot * fy
    out[~valid] = 0.0
    if src.ndim == 2:
        return out[..., 0]
    return out


def contain_scale(src_w: int, src_h: int, dst_w: int, dst_h: int) -> float:
    if src_w <= 0 or src_h <= 0:
        return 1.0
    return min(dst_w / float(src_w), dst_h / float(src_h))


def cover_scale(src_w: int, src_h: int, dst_w: int, dst_h: int) -> float:
    if src_w <= 0 or src_h <= 0:
        return 1.0
    return max(dst_w / float(src_w), dst_h / float(src_h))


def build_affine(
    src_w: int,
    src_h: int,
    canvas_w: int,
    canvas_h: int,
    *,
    scale_x: float = 1.0,
    scale_y: float = 1.0,
    rotation: float = 0.0,
    tx: float = 0.0,
    ty: float = 0.0,
    flip_h: bool = False,
    flip_v: bool = False,
    crop_box: Optional[Tuple[int, int, int, int]] = None,
) -> np.ndarray:
    """Return a 2x3 affine matrix mapping source pixels to canvas pixels.

    ``crop_box`` = (x, y, w, h) crops the source first (the draft's ``crop``
    polygon is already normalised by the importer).
    """
    if crop_box:
        cx, cy, cw, ch = crop_box
    else:
        cx, cy, cw, ch = 0, 0, src_w, src_h
    cw = max(1, int(cw))
    ch = max(1, int(ch))

    base = contain_scale(cw, ch, canvas_w, canvas_h)
    sx = base * float(scale_x)
    sy = base * float(scale_y)
    if flip_h:
        sx = -sx
    if flip_v:
        sy = -sy

    # centre of the cropped source
    scx = cx + cw / 2.0
    scy = cy + ch / 2.0
    # destination centre
    dcx = canvas_w / 2.0 + float(tx) * canvas_w / 2.0
    dcy = canvas_h / 2.0 - float(ty) * canvas_h / 2.0  # +y is up in the app

    theta = np.deg2rad(float(rotation))
    cos_t, sin_t = np.cos(theta), np.sin(theta)
    a = np.array(
        [
            [sx * cos_t, -sy * sin_t, dcx - (sx * cos_t) * scx + (sy * sin_t) * scy],
            [sx * sin_t, sy * cos_t, dcy - (sx * sin_t) * scx - (sy * cos_t) * scy],
        ],
        dtype=np.float32,
    )
    return a


def warp_layer(
    src_rgb: np.ndarray,
    src_alpha: Optional[np.ndarray],
    matrix: np.ndarray,
    canvas_w: int,
    canvas_h: int,
    global_alpha: float = 1.0,
) -> Tuple[np.ndarray, np.ndarray]:
    """Warp a layer into canvas space; returns (rgb float32, alpha float32)."""
    if src_alpha is None:
        a = np.ones(src_rgb.shape[:2], dtype=np.float32)
    else:
        a = src_alpha.astype(np.float32)
        if a.max() > 1.0:
            a = a / 255.0

    cv = _get_cv2()
    if cv is not None:
        rgb = cv.warpAffine(
            src_rgb.astype(np.float32),
            matrix,
            (canvas_w, canvas_h),
            flags=cv.INTER_LINEAR,
            borderMode=cv.BORDER_CONSTANT,
            borderValue=(0.0, 0.0, 0.0),
        )
        alpha = cv.warpAffine(
            a,
            matrix,
            (canvas_w, canvas_h),
            flags=cv.INTER_LINEAR,
            borderMode=cv.BORDER_CONSTANT,
            borderValue=0.0,
        )
    else:
        rgb = _warp_bilinear(src_rgb, matrix, canvas_w, canvas_h)
        alpha = _warp_bilinear(a, matrix, canvas_w, canvas_h)

    if global_alpha < 1.0:
        alpha = alpha * max(0.0, global_alpha)
    return rgb, alpha


def over(base: np.ndarray, layer: np.ndarray, alpha: np.ndarray, mode: str = "normal") -> np.ndarray:
    """Standard source-over composite in float space."""
    from . import color as color_mod

    # 快路径：alpha 全 1 / 全 0 时不必做三次全画幅运算（视频片段绝大多数是这两种）
    if alpha is not None:
        lo = float(alpha.min())
        hi = float(alpha.max())
        if hi <= 1e-6:
            return base
        if lo >= 0.999:
            if mode and mode not in ("normal", "over_lay_default"):
                return color_mod.blend(base, layer, mode)
            return layer if layer.shape == base.shape else base

    if mode and mode not in ("normal", "over_lay_default"):
        blended = color_mod.blend(base, layer, mode)
        a = alpha[..., None]
        out = blended * a
        out += base * (1.0 - a)
        return out
    # 普通混合：直接写回 base（canvas 是我们自己的缓冲），少一次全画幅分配
    # 1920x1080 float32 的一次分配 + 遍历 ≈ 25 ms（实测）
    a = alpha[..., None]
    if base.shape == layer.shape and base.dtype == np.float32:
        base *= (1.0 - a)
        base += layer * a
        return base
    out = layer * a
    out += base * (1.0 - a)
    return out


def add(base: np.ndarray, layer: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    return np.clip(base + layer * alpha[..., None], 0.0, 1.0)
