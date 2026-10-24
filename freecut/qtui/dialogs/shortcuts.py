"""快捷键一览。

列表**从菜单里现取**，不手写——手写的一张表很快就会与真实快捷键脱节。
只有几项"不是 QAction"的手势（拖拽、滚轮）才写死在下面。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QKeySequence
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPushButton,
                               QScrollArea, QVBoxLayout, QWidget)

from ... import theme

#: 不是 QAction、只能写在文档里的手势
GESTURES = [
    ("拖动片段", "移动位置"),
    ("拖动片段两端", "裁剪入 / 出点"),
    ("Ctrl + 滚轮", "缩放时间线"),
    ("单击时间线上方标尺", "移动播放头"),
    ("双击左栏素材", "加到时间线（播放头处）"),
    ("从左侧拖入时间线", "在落点位置生成片段"),
]


def collect_shortcuts(window) -> list:
    """从窗口菜单里收集「快捷键 → 动作名」，按菜单顺序。"""
    rows = []
    for top in window.menuBar().actions():
        menu = top.menu()
        if menu is None:
            continue
        group = top.text().replace("&", "")
        for action in menu.actions():
            if action.isSeparator():
                continue
            key = action.shortcut()
            if key.isEmpty():
                continue
            rows.append((group, key.toString(QKeySequence.NativeText),
                         action.text().replace("&", "")))
    return rows


class ShortcutsDialog(QDialog):
    """按 ? 弹出的快捷键表。"""

    def __init__(self, window) -> None:
        super().__init__(window)
        self.setWindowTitle("快捷键")
        self.setModal(True)
        self.resize(520, 560)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        title = QLabel("快捷键", self)
        title.setObjectName("PanelTitle")
        layout.addWidget(title)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        inner = QWidget(self)
        box = QVBoxLayout(inner)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(2)

        rows = collect_shortcuts(window)
        self.row_count = len(rows)
        current_group = None
        for group, key, text in rows:
            if group != current_group:
                current_group = group
                header = QLabel(group, inner)
                header.setStyleSheet(f"color:{theme.ACCENT}; font-weight:600; margin-top:8px;")
                box.addWidget(header)
            box.addLayout(self._row(inner, key, text))

        header = QLabel("鼠标手势", inner)
        header.setStyleSheet(f"color:{theme.ACCENT}; font-weight:600; margin-top:12px;")
        box.addWidget(header)
        for gesture, meaning in GESTURES:
            box.addLayout(self._row(inner, gesture, meaning))

        box.addStretch(1)
        scroll.setWidget(inner)
        layout.addWidget(scroll, 1)

        close = QPushButton("知道了", self)
        close.clicked.connect(self.accept)
        layout.addWidget(close, 0, Qt.AlignRight)

    @staticmethod
    def _row(parent: QWidget, key: str, text: str) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 1, 0, 1)
        key_label = QLabel(key, parent)
        key_label.setFont(QFont("Consolas", 9))
        key_label.setStyleSheet(f"color:{theme.ACCENT}; background:{theme.BG_INPUT};"
                                " border-radius:3px; padding:1px 6px;")
        key_label.setMinimumWidth(110)
        row.addWidget(key_label)
        row.addWidget(QLabel(text, parent), 1)
        return row
