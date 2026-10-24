"""界面共用的中文标签表。

原先把 ``ENGINE_LABELS`` 放在 ``freecut/gui.py``（Tk 版）里，Qt 版跨模块 import
会造成循环依赖，而且让「Qt 版依赖 Tk 版」——这不合理。这里单独放一份。
"""

from __future__ import annotations

#: 转场引擎 → 中文名（Tk 版与 Qt 版共用；引擎清单以 freecut.render.TRANSITION_KINDS 为准）
ENGINE_LABELS = {
    "split_in_corner": "角部收拢",
    "diagonal_in": "对角收拢",
    "split_in_horizontal": "上下收拢",
    "split_in_vertical": "左右收拢",
    "split_horizontal": "上下分屏",
    "split_vertical": "左右分屏",
    "wipe_radial": "圆形擦除",
    "zoom_out": "中心收拢",
    "wipe_corner": "角部擦除",
    "blinds_diagonal": "斜百叶",
    "wipe_bar": "横扫亮带",
    "slide_diagonal": "对角滑动",
    "mosaic_random": "随机马赛克",
    "zoom_center": "中心缩放",
    "wipe_clock": "顺时针擦除",
    "wipe_diagonal_reverse": "反向对角擦除",
    "blinds_vertical": "竖百叶",
    "circle_in": "圆形展开",
    "cross_dissolve": "叠化", "fade_black": "闪黑", "fade_white": "闪白",
    "wipe_left": "向左擦除", "wipe_right": "向右擦除",
    "wipe_up": "向上擦除", "wipe_down": "向下擦除",
    "wipe_diagonal": "斜向擦除", "square": "方形展开", "blinds": "百叶窗",
    "slide_left": "向左滑动", "slide_right": "向右滑动",
    "slide_up": "向上滑动", "slide_down": "向下滑动",
    "circle": "圆形", "zoom_push": "缩放推", "mosaic_dissolve": "像素化",
    "page_turn": "翻页", "pixel_dissolve": "像素溶解",
}
