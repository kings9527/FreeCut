"""特效 / 转场的缩略预览图。

两张图都不是"示意画"，而是**拿真实算法跑在一张合成底图上**：

* 特效 → ``effects.apply_effect(kind, …)``
* 转场 → ``render._transition_mask(kind, u)`` 在 u=0.5 处合成两张不同的底图

所以缩略图能反映这个特效/转场实际长什么样（至少能看出方向、色调、颗粒）。
"""

from __future__ import annotations

from typing import Dict, Tuple

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap

_CACHE: Dict[Tuple[str, str, int], QPixmap] = {}


def _base(size: Tuple[int, int], warm: bool = True) -> np.ndarray:
    """合成底图：斜向渐变 + 一个圆 + 两条斜线，够看出特效的差别。"""
    height, width = size
    yy = np.linspace(0.0, 1.0, height, dtype=np.float32)[:, None]
    xx = np.linspace(0.0, 1.0, width, dtype=np.float32)[None, :]
    ramp = np.clip(0.25 + 0.75 * (xx * 0.7 + yy * 0.3), 0.0, 1.0)
    if warm:
        rgb = np.stack([ramp, ramp * 0.75 + 0.15, 0.25 + ramp * 0.4], axis=-1)
    else:
        rgb = np.stack([0.20 + ramp * 0.3, ramp * 0.9, 0.35 + ramp * 0.6], axis=-1)
    # 圆
    cy, cx = height * 0.45, width * 0.34
    radius = min(height, width) * 0.22
    mask = ((yy * height - cy) ** 2 + (xx * width - cx) ** 2) <= radius ** 2
    rgb[mask] = np.array([0.95, 0.55, 0.25], dtype=np.float32)
    # 斜线
    line = np.abs((xx * width + yy * height) - (width * 0.45 + height * 0.5)) < 1.2
    rgb[line] = np.array([0.98, 0.98, 0.99], dtype=np.float32)
    return np.clip(rgb, 0.0, 1.0).astype(np.float32)


def _to_pixmap(rgb: np.ndarray) -> QPixmap:
    array = np.ascontiguousarray((np.clip(rgb, 0.0, 1.0) * 255).astype(np.uint8))
    height, width = array.shape[:2]
    image = QImage(array.data, width, height, array.strides[0], QImage.Format_RGB888).copy()
    return QPixmap.fromImage(image)


def effect_pixmap(kind: str, width: int = 72, height: int = 40) -> QPixmap:
    """把特效真的跑一遍在这张小底图上。"""
    key = ("effect", kind, width)
    if key in _CACHE:
        return _CACHE[key]
    from .. import effects

    rgb = _base((height, width))
    alpha = np.ones((height, width), dtype=np.float32)
    try:
        params = dict(effects.EFFECT_SPEC.get(kind, {}).get("params", {}))
        out = effects.apply_effect(kind, rgb.copy(), alpha.copy(), 300_000, params)
        if isinstance(out, tuple):
            out = out[0]
        rgb = np.asarray(out, dtype=np.float32)
        if rgb.max() > 1.5:
            rgb = rgb / 255.0
    except Exception:                                  # noqa: BLE001
        pass
    pixmap = _to_pixmap(rgb)
    _CACHE[key] = pixmap
    return pixmap


def transition_pixmap(kind: str, width: int = 72, height: int = 40) -> QPixmap:
    """在 u=0.5 处把 A/B 两张底图按真实蒙版合成。"""
    key = ("transition", kind, width)
    if key in _CACHE:
        return _CACHE[key]
    from ..render import _transition_mask

    first = _base((height, width), warm=True)
    second = _base((height, width), warm=False)
    try:
        mask = np.asarray(_transition_mask(kind, 0.5, width, height), dtype=np.float32)
        if mask.ndim == 2:
            mask = mask[..., None]
        rgb = second * mask + first * (1.0 - mask)
    except Exception:                                  # noqa: BLE001
        rgb = second * 0.5 + first * 0.5
    pixmap = _to_pixmap(rgb)
    _CACHE[key] = pixmap
    return pixmap
