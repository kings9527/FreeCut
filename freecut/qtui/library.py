"""素材库的二级分类与网格布局（HANDOFF §5.3）。

剪映的转场/特效面板是「左侧二级分类 + 右侧缩略图网格」。我们的条目是本地引擎，
分组只是为了照着剪映的分类习惯摆，不声称与剪映的某一条同名同画面。

**拖拽**：剪映里转场/特效是直接拖到时间线片段上的。这里给网格一个自定义 mime
（``application/x-freecut-library``），时间线认这个格式就把效果/转场套到"落点所在的片段"。
"""

from __future__ import annotations

import json

from PySide6.QtCore import QMimeData, QSize, Qt
from PySide6.QtWidgets import QAbstractItemView, QListView, QListWidget

#: 素材库拖到时间线用的 mime 类型（内容是 JSON：[{kind, value}, ...]）
LIBRARY_MIME = "application/x-freecut-library"

#: 转场引擎 → 所属二级分类（一个引擎可以进多个分类）
TRANSITION_GROUPS: dict = {
    "热门": ["cross_dissolve", "fade_black", "zoom_push"],
    "基础": ["split_in_corner", "diagonal_in", "split_in_horizontal", "split_in_vertical", "split_horizontal", "split_vertical", "wipe_radial", "circle", "zoom_out", "wipe_corner", "blinds_diagonal", "wipe_bar", "slide_diagonal", "zoom_center", "wipe_clock", "circle_in", "wipe_diagonal_reverse", "blinds_vertical", "wipe_left", "wipe_right", "wipe_up", "wipe_down", "wipe_diagonal",
             "square", "blinds"],
    "运镜": ["slide_left", "slide_right", "slide_up", "slide_down", "zoom_push"],
    "幻灯片": ["slide_left", "slide_right", "page_turn"],
    "光效": ["fade_white", "fade_black"],
    "扭曲": ["mosaic_random", "mosaic_dissolve", "pixel_dissolve", "page_turn"],
}

#: 程序化特效 → 所属二级分类
EFFECT_GROUPS: dict = {
    "基础": ["blur", "mosaic", "mirror"],
    "光效": ["glow", "leak", "flash", "sparkle", "breathe", "pulse"],
    "风格化": ["duotone", "film", "crt", "rgb_split", "glitch", "shake"],
    "修复": ["smooth", "auto_matting"],
}


def groups_of(kind: str, table: dict) -> list:
    return [name for name, kinds in table.items() if kind in kinds]


def kinds_in(group: str, table: dict) -> list:
    """给定分类名（``全部`` 表示不筛），返回该分类下的条目顺序。"""
    if not group or group == "全部":
        return []
    return list(table.get(group, []))


class LibraryGrid(QListWidget):
    """可以拖出去的网格：拖的是 ``{kind, value}``，时间线按落点套到片段上。"""

    def __init__(self, parent=None, library_kind: str = "") -> None:
        super().__init__(parent)
        self.library_kind = library_kind
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragOnly)
        self.setDefaultDropAction(Qt.CopyAction)

    def mimeData(self, items):                          # noqa: N802
        data = QMimeData()
        payload = []
        for item in items:
            value = item.data(Qt.UserRole)
            if value:
                payload.append({"kind": self.library_kind, "value": str(value)})
        if payload:
            data.setData(LIBRARY_MIME, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        return data


def make_grid(parent, group_filter: bool = False, icon_size: QSize = QSize(80, 45),
              library_kind: str = "") -> "LibraryGrid":
    """图标网格：两列，**每格都有名字**，并且可以拖到时间线上。

    用户实测报过「特效/转场没有名字」：根因是上一版开了
    ``setUniformItemSizes(True)``，而缩略图是懒加载的——第一次布局时图标还是空的，
    格子高度被按"没有图标"算死，之后文字和图标都挤不下。这里关掉它。
    """
    view = LibraryGrid(parent, library_kind=library_kind)
    view.setViewMode(QListView.IconMode)
    view.setResizeMode(QListView.Adjust)
    view.setMovement(QListView.Static)
    view.setIconSize(icon_size)
    # 格子高度要放得下「图标 + 一到两行文字」
    view.setGridSize(QSize(icon_size.width() + 36, icon_size.height() + 46))
    view.setWordWrap(True)
    view.setTextElideMode(Qt.ElideRight)
    view.setSpacing(2)
    view.setUniformItemSizes(False)
    view.setSelectionMode(QListWidget.SingleSelection)
    # Qt 的坑：setMovement(Static) 会把 dragDropMode 改成 DropOnly（拖动被关掉），
    # 所以**配置完之后**再设一次"只拖不受"（实测：不设的话网格拖不出去）
    view.setDragEnabled(True)
    view.setAcceptDrops(False)
    view.setDragDropMode(QAbstractItemView.DragOnly)
    return view


def mark_applied(item, applied: bool) -> None:
    """在条目文字前打勾：让用户一眼看出"这个已经用上了"。"""
    base = item.data(Qt.UserRole + 1)
    if not base:
        base = str(item.text()).replace("✓ ", "", 1)
        item.setData(Qt.UserRole + 1, base)
    item.setText(f"✓ {base}" if applied else str(base))
