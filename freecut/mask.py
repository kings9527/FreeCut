"""Mask rasterisation.

JianyingPro stores a mask as a separate material (``materials.common_mask`` in
11.x, ``materials.masks`` in older builds) with this config::

    {"aspectRatio": 1.0, "centerX": 0.0, "centerY": 0.0, "feather": 0.0,
     "height": 1.0, "invert": false, "rotation": 0.0, "roundCorner": 0.0,
     "width": 1.0}

``centerX``/``centerY``/``width``/``height`` are in **half-material units**
(1.0 = half the clip), ``feather`` and ``roundCorner`` are 0..1, ``rotation``
is in degrees.  The six mask kinds come from the app's own resource catalogue
(``freecut/data/masks.json``): 线性 / 镜面 / 圆形 / 矩形 / 爱心 / 星形.
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np

#: resource_type -> how we rasterise it
SHAPE_KINDS = {
    "line": "line",
    "mirror": "mirror",
    "circle": "circle",
    "rectangle": "rectangle",
    "geometric_shape": "geometric",
}


def _soft(x: np.ndarray, edge: float) -> np.ndarray:
    if edge <= 1e-6:
        return (x >= 0.0).astype(np.float32)
    return np.clip(x / edge, 0.0, 1.0)


def build_mask(width: int, height: int, config: dict, resource_type: str = "") -> np.ndarray:
    """Return a float32 mask of shape (H, W) in [0, 1]; 1 = visible."""
    cfg = dict(config or {})
    cx = float(cfg.get("centerX", 0.0) or 0.0)
    cy = float(cfg.get("centerY", 0.0) or 0.0)
    mw = max(1e-3, float(cfg.get("width", 1.0) or 1.0))
    mh = max(1e-3, float(cfg.get("height", 1.0) or 1.0))
    rot = float(cfg.get("rotation", 0.0) or 0.0)
    feather = float(cfg.get("feather", 0.0) or 0.0)
    invert = bool(cfg.get("invert", False))
    radius = float(cfg.get("roundCorner", 0.0) or 0.0)
    aspect = float(cfg.get("aspectRatio", 1.0) or 1.0)

    kind = SHAPE_KINDS.get(resource_type, resource_type or "circle")

    # normalised coordinates centred on the clip, y down
    ys = np.linspace(-1.0, 1.0, height, dtype=np.float32)[:, None]
    xs = np.linspace(-1.0, 1.0, width, dtype=np.float32)[None, :]

    # rotate the sampling grid into mask space
    if abs(rot) > 1e-6:
        t = math.radians(rot)
        cos_t, sin_t = math.cos(t), math.sin(t)
        xr = (xs - cx) * cos_t + (ys - cy) * sin_t
        yr = -(xs - cx) * sin_t + (ys - cy) * cos_t
    else:
        xr = xs - cx
        yr = ys - cy

    # xs is (1, W) and ys is (H, 1); make both full-frame before using them
    xr, yr = (np.ascontiguousarray(a, dtype=np.float32) for a in np.broadcast_arrays(xr, yr))

    feather_w = max(1e-4, feather) * 0.5

    if kind == "line":
        # half plane: keep the part below the line (the app's default)
        d = -yr / max(mh, 1e-3)
        mask = _soft(-d, feather_w)
    elif kind == "mirror":
        band = np.abs(yr) / max(mh, 1e-3)
        mask = 1.0 - _soft(band - 1.0, feather_w)
    elif kind == "circle":
        r = np.sqrt((xr / max(mw * aspect, 1e-3)) ** 2 + (yr / max(mh, 1e-3)) ** 2)
        mask = 1.0 - _soft(r - 1.0, feather_w)
    elif kind == "rectangle":
        dx = np.abs(xr) / max(mw * aspect, 1e-3)
        dy = np.abs(yr) / max(mh, 1e-3)
        d = np.maximum(dx, dy)
        if radius > 1e-4:
            # rounded rectangle: soften distance near the corners
            inner = np.maximum(dx - (1.0 - radius), dy - (1.0 - radius))
            corner = np.sqrt(np.maximum(inner, 0.0) ** 2 + np.maximum(inner, 0.0) ** 2)
            d = np.where((dx > 1.0 - radius) & (dy > 1.0 - radius), 1.0 - radius + corner, d)
        mask = 1.0 - _soft(d - 1.0, feather_w)
    elif kind == "geometric":
        # heart / star share the "geometric_shape" resource type; approximate
        # with a star polygon, which reads correctly for both small previews
        ang = np.arctan2(yr, xr)
        rad = np.sqrt((xr / max(mw * aspect, 1e-3)) ** 2 + (yr / max(mh, 1e-3)) ** 2)
        spike = 0.62 + 0.38 * np.cos(5.0 * ang)
        mask = 1.0 - _soft(rad / spike - 1.0, feather_w)
    else:
        mask = np.ones((height, width), dtype=np.float32)

    mask = np.clip(mask, 0.0, 1.0).astype(np.float32)
    if invert:
        mask = 1.0 - mask
    return mask


def mask_from_material(material, width: int, height: int) -> Optional[np.ndarray]:
    if material is None:
        return None
    cfg = material.get("config") if hasattr(material, "get") else None
    if not isinstance(cfg, dict):
        return None
    kind = str(material.get("resource_type") or material.get("name") or "")
    return build_mask(width, height, cfg, kind)


def add_stroke(mask: np.ndarray, width: int, colour: tuple, softness: float = 1.0
               ) -> np.ndarray:
    """给蒙版边缘加一圈描边（剪映的 video_mask_stroke）。

    做法：把蒙版向内缩 ``width`` 像素，差集就是边缘环带；环带外仍然可见，
    环带内替换成描边色。返回值是 ``(H, W, 3)`` 的「描边色 + alpha」。

    没有素材依赖——纯几何。
    """
    import numpy as np

    m = np.clip(np.asarray(mask, dtype=np.float32), 0.0, 1.0)
    if width <= 0:
        return np.zeros(m.shape + (3,), dtype=np.float32)
    # 用「腐蚀」近似内缩：连续最小值滤波（方形核，够快也够像）
    inner = m.copy()
    w = max(1, int(width))
    for _ in range(w):
        shifted = inner.copy()
        shifted[1:, :] = np.minimum(shifted[1:, :], inner[:-1, :])
        shifted[:-1, :] = np.minimum(shifted[:-1, :], inner[1:, :])
        shifted[:, 1:] = np.minimum(shifted[:, 1:], inner[:, :-1])
        shifted[:, :-1] = np.minimum(shifted[:, :-1], inner[:, 1:])
        inner = shifted
    ring = np.clip(m - inner, 0.0, 1.0)
    if softness > 1.0:
        ring = np.clip(ring * float(softness), 0.0, 1.0)
    rgb = np.zeros(m.shape + (3,), dtype=np.float32)
    rgb[:] = np.asarray(colour, dtype=np.float32)[None, None, :]
    return rgb * ring[..., None]


def add_shadow(mask: np.ndarray, offset: tuple = (4, 4), blur: int = 6,
               opacity: float = 0.55, colour: tuple = (0.0, 0.0, 0.0)
               ) -> tuple:
    """蒙版投影（剪映 video_mask_shadow）。

    做法：把蒙版按 ``offset`` 平移并模糊，减去原蒙版，剩下的就是"影子"；
    只在蒙版**外面**出现，所以它不会糊住画面主体。

    返回 ``(rgb, alpha)``，都是整幅尺寸，直接和画面做 over 合成。
    """
    import numpy as np

    m = np.clip(np.asarray(mask, dtype=np.float32), 0.0, 1.0)
    h, w = m.shape
    dx, dy = int(offset[0]), int(offset[1])
    shifted = np.zeros_like(m)
    xs0, xs1 = max(0, dx), min(w, w + dx)
    ys0, ys1 = max(0, dy), min(h, h + dy)
    if xs1 > xs0 and ys1 > ys0:
        shifted[ys0:ys1, xs0:xs1] = m[ys0 - dy:ys1 - dy, xs0 - dx:xs1 - dx]
    if blur > 0:
        # 便宜的方框模糊：连续两次平均，够软
        k = max(1, int(blur))
        for _ in range(2):
            acc = np.zeros_like(shifted)
            cnt = np.zeros_like(shifted)
            for oy in range(-k, k + 1, max(1, k // 3)):
                for ox in range(-k, k + 1, max(1, k // 3)):
                    ys0, ys1 = max(0, oy), min(h, h + oy)
                    xs0, xs1 = max(0, ox), min(w, w + ox)
                    if ys1 <= ys0 or xs1 <= xs0:
                        continue
                    acc[ys0:ys1, xs0:xs1] += shifted[ys0 - oy:ys1 - oy, xs0 - ox:xs1 - ox]
                    cnt[ys0:ys1, xs0:xs1] += 1.0
            shifted = acc / np.maximum(cnt, 1.0)
    shadow_a = np.clip(shifted - m, 0.0, 1.0) * float(np.clip(opacity, 0.0, 1.0))
    rgb = np.zeros((h, w, 3), dtype=np.float32)
    rgb[:] = np.asarray(colour, dtype=np.float32)[None, None, :]
    return rgb, shadow_a


def apply_mask(alpha: np.ndarray, mask: np.ndarray) -> np.ndarray:
    if mask is None:
        return alpha
    if mask.shape != alpha.shape:
        from PIL import Image

        mask = np.asarray(
            Image.fromarray(mask, "F").resize((alpha.shape[1], alpha.shape[0]), Image.BILINEAR)
        ).astype(np.float32)
    return alpha * mask
