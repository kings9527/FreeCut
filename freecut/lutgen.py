"""Generate FreeCut's own ``.cube`` LUTs — no 剪映 assets involved.

剪映的 1052 个滤镜靠云端下发载荷，我们拿不到，也不该拿。但**色调**这种东西
可以用数学描述：给一个 (r,g,b)→(r',g',b') 的函数，在 17³ 的网格上采样，
就是一张标准 Adobe ``.cube``。写出来之后 ``freecut.color.load_cube_lut``
和渲染器都能直接用，滤镜在自己这边就真的生效了。

这里的十个 look 是**自己调的参数**，不是任何软件内置预设的复制。
"""

from __future__ import annotations

import os
from typing import Callable, Dict, List, Tuple

import numpy as np

#: 网格精度。17 是 Adobe 系常用值：17³ = 4913 行，文件 ~120 KB。
GRID = 17


def _clamp(x: np.ndarray) -> np.ndarray:
    return np.clip(x, 0.0, 1.0)


def _luma(rgb: np.ndarray) -> np.ndarray:
    """Rec.709 亮度，形状与 rgb 的前三维一致（**不带**尾部单维）。

    带 `[..., None]` 会让 `out[..., 0] += ...` 触发广播错误——第一版就踩了。
    需要按通道用时显式加 `[..., None]`。
    """
    return rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114


def _mix(a: np.ndarray, b: np.ndarray, t: float) -> np.ndarray:
    return a * (1.0 - t) + b * t


def teal_orange(rgb: np.ndarray) -> np.ndarray:
    """青橙：暗部压青、亮部推橙，电影调色最常见的一支。"""
    luma = _luma(rgb)
    shadow = 1.0 - luma
    out = rgb.copy()
    out[..., 0] += 0.10 * luma - 0.05 * shadow
    out[..., 1] += 0.02 * luma
    out[..., 2] += 0.12 * shadow - 0.04 * luma
    return _clamp(out)


def morandi(rgb: np.ndarray) -> np.ndarray:
    """莫兰迪：降饱和、抬黑、轻微偏灰绿。"""
    luma = _luma(rgb)
    desat = _mix(rgb, np.repeat(luma[..., None], 3, axis=-1), 0.35)
    out = desat * 0.92 + 0.06
    out[..., 1] *= 1.02
    return _clamp(out)


def fade(rgb: np.ndarray) -> np.ndarray:
    """褪色：抬黑 + 压高光，像旧照片。"""
    out = rgb * 0.88 + 0.09
    return _clamp(out)


def cool(rgb: np.ndarray) -> np.ndarray:
    """冷调：整体偏蓝，色温往低走。"""
    out = rgb.copy()
    out[..., 0] *= 0.94
    out[..., 2] = _clamp(out[..., 2] * 1.10 + 0.02)
    return _clamp(out)


def warm(rgb: np.ndarray) -> np.ndarray:
    """暖调：整体偏黄橙。"""
    out = rgb.copy()
    out[..., 0] = _clamp(out[..., 0] * 1.08 + 0.02)
    out[..., 1] *= 1.02
    out[..., 2] *= 0.93
    return _clamp(out)


def mono(rgb: np.ndarray) -> np.ndarray:
    """黑白：Rec.709 亮度，保留一点 S 曲线对比。"""
    luma = _luma(rgb)
    s = _clamp(luma)
    s = s * s * (3.0 - 2.0 * s)          # smoothstep 提对比
    return _clamp(np.repeat(s[..., None], 3, axis=-1))


def punch(rgb: np.ndarray) -> np.ndarray:
    """高对比：S 曲线 + 提饱和。"""
    luma = _luma(rgb)
    contrasted = _clamp(rgb + (rgb - luma[..., None]) * 0.25)
    s = contrasted * contrasted * (3.0 - 2.0 * contrasted)
    return _clamp(s)


def film(rgb: np.ndarray) -> np.ndarray:
    """胶片：高光偏暖、暗部偏青，中间调略降饱和。"""
    luma = _luma(rgb)
    out = _mix(rgb, np.repeat(luma[..., None], 3, axis=-1), 0.12)
    out[..., 0] += 0.05 * luma
    out[..., 2] += 0.05 * (1.0 - luma)
    out = out * 0.95 + 0.03
    return _clamp(out)


def sepia(rgb: np.ndarray) -> np.ndarray:
    """复古棕褐。"""
    luma = _luma(rgb)
    tint = np.stack([luma * 1.07, luma * 0.94, luma * 0.74], axis=-1)
    return _clamp(_mix(rgb, tint, 0.85))


def neon(rgb: np.ndarray) -> np.ndarray:
    """霓虹：强饱和 + 提蓝紫。"""
    luma = _luma(rgb)
    boosted = _clamp(luma[..., None] + (rgb - luma[..., None]) * 1.6)
    boosted[..., 2] = _clamp(boosted[..., 2] * 1.08)
    boosted[..., 0] = _clamp(boosted[..., 0] * 0.98)
    return _clamp(boosted)


def cinematic(rgb: np.ndarray) -> np.ndarray:
    """电影感：压高光、抬暗部、轻微青黄分离。"""
    luma = _luma(rgb)
    out = rgb * 0.96 + 0.02
    # 注意：luma 带 [..., None] 时不能用 `out[..., 2] +=`，形状对不上（第 154 轮同款坑）
    out[..., 2] = out[..., 2] + 0.03 * (1.0 - luma)
    out[..., 0] = out[..., 0] + 0.02 * luma
    return _clamp(out)


def dark_mood(rgb: np.ndarray) -> np.ndarray:
    """暗调：整体压暗、保住高光细节。"""
    out = rgb ** 1.25
    return _clamp(out * 0.92)


def japanese(rgb: np.ndarray) -> np.ndarray:
    """日系：提亮、降饱和、偏青绿。"""
    luma = _luma(rgb)
    desat = _mix(rgb, np.repeat(luma[..., None], 3, axis=-1), 0.22)
    out = desat * 1.06 + 0.03
    out[..., 1] = _clamp(out[..., 1] * 1.02)
    return _clamp(out)


def cyber(rgb: np.ndarray) -> np.ndarray:
    """赛博：紫青双色，暗部压蓝、亮部推品红。"""
    luma = _luma(rgb)
    out = rgb.copy()
    out[..., 2] = _clamp(out[..., 2] * 1.12 + 0.03 * (1.0 - luma))
    out[..., 0] = _clamp(out[..., 0] * 1.05 + 0.04 * luma)
    out[..., 1] *= 0.94
    return _clamp(out)


def vintage(rgb: np.ndarray) -> np.ndarray:
    """复古：褪色 + 偏黄 + 低对比。"""
    luma = _luma(rgb)
    out = _mix(rgb, np.repeat(luma[..., None], 3, axis=-1), 0.18)
    out = out * 0.82 + 0.14
    out[..., 0] *= 1.03
    out[..., 2] *= 0.94
    return _clamp(out)


def nordic(rgb: np.ndarray) -> np.ndarray:
    """北欧：冷、低饱和、干净。"""
    luma = _luma(rgb)
    desat = _mix(rgb, np.repeat(luma[..., None], 3, axis=-1), 0.30)
    out = desat * 1.02 + 0.04
    out[..., 2] = _clamp(out[..., 2] * 1.05)
    return _clamp(out)


def sunset(rgb: np.ndarray) -> np.ndarray:
    """日落：暖橙 + 紫调阴影。"""
    luma = _luma(rgb)
    out = rgb.copy()
    out[..., 0] = _clamp(out[..., 0] * 1.06 + 0.05 * luma)
    out[..., 1] = _clamp(out[..., 1] * 1.01)
    out[..., 2] = _clamp(out[..., 2] * 0.95 + 0.04 * (1.0 - luma))
    return _clamp(out)


def forest(rgb: np.ndarray) -> np.ndarray:
    """森林：绿调偏移、压红。"""
    out = rgb.copy()
    out[..., 0] *= 0.92
    out[..., 1] = _clamp(out[..., 1] * 1.06 + 0.02)
    out[..., 2] *= 0.98
    return _clamp(out)


def high_key(rgb: np.ndarray) -> np.ndarray:
    """高调：整体提亮、对比降低。"""
    out = rgb * 0.78 + 0.22
    return _clamp(out)


def low_key(rgb: np.ndarray) -> np.ndarray:
    """低调：整体压暗、保留亮部。"""
    luma = _luma(rgb)
    out = rgb * 0.68
    out = out + 0.12 * (luma[..., None] ** 2)
    return _clamp(out)


#: 名字 → (函数, 中文标签)
LOOKS: Dict[str, Tuple[Callable[[np.ndarray], np.ndarray], str]] = {
    "teal_orange": (teal_orange, "青橙"),
    "morandi": (morandi, "莫兰迪"),
    "fade": (fade, "褪色"),
    "cool": (cool, "冷调"),
    "warm": (warm, "暖调"),
    "mono": (mono, "黑白"),
    "punch": (punch, "高对比"),
    "film": (film, "胶片"),
    "sepia": (sepia, "复古"),
    "neon": (neon, "霓虹"),
    "cinematic": (cinematic, "电影感"),
    "dark_mood": (dark_mood, "暗调"),
    "japanese": (japanese, "日系"),
    "cyber": (cyber, "赛博"),
    "vintage": (vintage, "复古褪色"),
    "nordic": (nordic, "北欧"),
    "sunset": (sunset, "日落"),
    "forest": (forest, "森林"),
    "high_key": (high_key, "高调"),
    "low_key": (low_key, "低调"),
}


def default_dir() -> str:
    """Where generated LUTs live (already in the LUT library search path)."""
    base = os.environ.get("FREECUT_LUT_OUT")
    if base:
        return base
    root = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(root, "FreeCut", "luts")


def bake(name: str, grid: int = GRID) -> np.ndarray:
    """Sample one look into an ``(N, N, N, 3)`` table."""
    if name not in LOOKS:
        raise KeyError(f"没有这个 look: {name}（可选 {', '.join(LOOKS)}）")
    fn = LOOKS[name][0]
    axis = np.linspace(0.0, 1.0, grid, dtype=np.float32)
    # .cube 的遍历顺序是 r 最慢、b 最快；这里按 (b, g, r) 生成再转置回来
    blue, green, red = np.meshgrid(axis, axis, axis, indexing="ij")
    rgb = np.stack([red, green, blue], axis=-1).astype(np.float32)
    return fn(rgb).astype(np.float32)


def write_cube(name: str, path: str, grid: int = GRID) -> str:
    """Write one look as an Adobe ``.cube`` file."""
    table = bake(name, grid)
    lines: List[str] = [
        f"# Generated by FreeCut ({name})",
        f"TITLE \"FreeCut {name}\"",
        f"LUT_3D_SIZE {grid}",
        "DOMAIN_MIN 0.0 0.0 0.0",
        "DOMAIN_MAX 1.0 1.0 1.0",
    ]
    flat = table.reshape(-1, 3)
    lines.extend(f"{r:.6f} {g:.6f} {b:.6f}" for r, g, b in flat)
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    return path


def write_all(directory: str, grid: int = GRID) -> List[str]:
    """Bake every look into ``directory``; returns the written paths."""
    written = []
    for name in LOOKS:
        written.append(write_cube(name, os.path.join(directory, f"{name}.cube"), grid))
    return written


def labels() -> Dict[str, str]:
    return {name: label for name, (_fn, label) in LOOKS.items()}
