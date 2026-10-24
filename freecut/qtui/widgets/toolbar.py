"""时间线工具栏（HANDOFF §5.6 那一排）。

剪映专业版时间线上面有一条 24 个按钮的工具栏。这里按同样的顺序摆一遍：

    + / 选择工具▾ / 撤销 / 重做 / 分割 / 删除 / 复制 / 粘贴 / 镜像 / 裁剪 / 倒放 / 定格 /
    音量 / 降噪 / 变速 / 动画 / 识别字幕 / 主轨磁吸 / 自动吸附 / 联动 / 预览轴 / 导出

云端做不到的（``AI特效``）和没有实现的（``录音``）**不摆按钮**——这是 HANDOFF §10 的诚实规则。
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QActionGroup, QKeySequence
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QMenu, QToolButton,
                               QWidget)

from ... import theme
from ..icons import qicon

#: (键, 文字, 提示, 类型)  —— 顺序就是界面顺序，测试照着比（TestUiParity）
TOOLBAR_ORDER: List[tuple] = [
    ("add", "+", "导入素材（也可直接拖进窗口）", "action"),
    ("select", "选择工具", "选择 / 剪切 / 剃刀工具", "menu"),
    ("undo", "撤销", "撤销（Ctrl+Z）", "action"),
    ("redo", "重做", "重做（Ctrl+Y）", "action"),
    ("split", "分割", "在播放头分割（Ctrl+K）", "action"),
    ("delete", "删除", "删除选中片段（Delete）", "action"),
    ("copy", "复制", "复制片段（Ctrl+C）", "action"),
    ("paste", "粘贴", "粘贴到播放头（Ctrl+V）", "action"),
    ("mirror", "镜像", "左右镜像选中片段", "action"),
    ("crop", "裁剪", "打开画面页的裁剪", "action"),
    ("reverse", "倒放", "倒放选中片段（要重新编码）", "action"),
    ("freeze", "定格", "把播放头这一帧定格 2 秒", "action"),
    ("volume", "音量", "打开音频页", "action"),
    ("denoise", "降噪", "打开音频页的降噪", "action"),
    ("speed", "变速", "打开变速页", "action"),
    ("animation", "动画", "打开动画页", "action"),
    ("subtitle", "识别字幕", "离线语音识别（Whisper）", "action"),
    ("magnetic", "主轨磁吸", "加到主轨时吸附到前一段末尾", "toggle"),
    ("snap", "自动吸附", "拖动时吸附到播放头与片段边缘", "toggle"),
    ("link", "联动", "分离出来的音频跟随视频一起移动", "toggle"),
    ("playhead_axis", "预览轴", "鼠标在时间线上移动时预览该处画面", "toggle"),
    ("export", "导出", "导出成片（Ctrl+E）", "action"),
]

#: 需要"选中片段"才可用的按钮
NEEDS_SEGMENT = ("split", "delete", "copy", "mirror", "crop", "reverse", "freeze",
                 "volume", "denoise", "speed", "animation")


class TimelineToolbar(QWidget):
    """时间线工具条。所有动作都转给 ``MainWindow``/``Bridge``，自身不实现业务。"""

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.setObjectName("TimelineToolBar")
        self.window = window
        self.bridge = window.bridge
        self.buttons: dict = {}
        self.order = [entry[0] for entry in TOOLBAR_ORDER]

        box = QHBoxLayout(self)
        box.setContentsMargins(6, 2, 6, 2)
        box.setSpacing(3)
        for key, text, tip, kind in TOOLBAR_ORDER:
            if key == "export":
                box.addStretch(1)
            btn = QToolButton(self)
            btn.setObjectName("TLTool")
            btn.setText(text)
            btn.setToolTip(tip)
            btn.setFixedHeight(26)
            if kind == "toggle":
                btn.setCheckable(True)
            if kind == "disabled":
                btn.setEnabled(False)
            if kind == "menu":
                btn.setPopupMode(QToolButton.InstantPopup)
                btn.setMenu(self._tool_menu())
            box.addWidget(btn)
            self.buttons[key] = btn
        self._wire()
        self.bridge.selectionChanged.connect(lambda _s: self.update_states())
        self.bridge.edited.connect(self.update_states)
        self.update_states()

    # -- 绑定 ------------------------------------------------------------- #
    def _tool_menu(self) -> QMenu:
        """选择 / 切割两种工具（剪映的左键行为随工具变）。"""
        menu = QMenu(self)
        self.tool_group = QActionGroup(menu)
        self.tool_group.setExclusive(True)
        self.tool_actions = {}
        for key, name, tip, shortcut in (
            ("select", "选择工具", "点选 / Ctrl+点加选 / 空白处拖出框选", "A"),
            ("razor", "切割工具", "点一下就把那个位置的片段切开", "C"),
        ):
            action = menu.addAction(name)
            action.setCheckable(True)
            action.setToolTip(f"{tip}（{shortcut}）")
            action.setShortcut(QKeySequence(shortcut))
            action.setShortcutContext(Qt.ApplicationShortcut)
            action.triggered.connect(lambda _checked=False, k=key: self.set_tool(k))
            self.tool_group.addAction(action)
            self.tool_actions[key] = action
        self.tool_actions["select"].setChecked(True)
        return menu

    def set_tool(self, name: str) -> None:
        """切换时间线工具（选择 / 切割），工具栏按钮与光标跟着变。"""
        timeline = getattr(self.window, "timeline", None)
        if timeline is not None:
            timeline.set_tool(name)
        if name in getattr(self, "tool_actions", {}):
            self.tool_actions[name].setChecked(True)
        self.update_states()

    def _wire(self) -> None:
        b = self.bridge
        w = self.window
        self.buttons["add"].clicked.connect(self._import)
        self.buttons["undo"].clicked.connect(w.undo)
        self.buttons["redo"].clicked.connect(w.redo)
        self.buttons["split"].clicked.connect(w.split_at_playhead)
        self.buttons["delete"].clicked.connect(w.delete_selected)
        self.buttons["copy"].clicked.connect(b.copy_selected)
        self.buttons["paste"].clicked.connect(lambda: b.paste())
        self.buttons["mirror"].clicked.connect(lambda: b.mirror("h"))
        self.buttons["crop"].clicked.connect(lambda: w.focus_inspector("画面", "裁剪"))
        self.buttons["reverse"].clicked.connect(lambda: b.reverse_selected())
        self.buttons["freeze"].clicked.connect(lambda: b.freeze_selected())
        self.buttons["volume"].clicked.connect(lambda: w.focus_inspector("音频", "音量"))
        self.buttons["denoise"].clicked.connect(lambda: w.focus_inspector("音频", "降噪"))
        self.buttons["speed"].clicked.connect(lambda: w.focus_inspector("变速", "变速"))
        self.buttons["animation"].clicked.connect(lambda: w.focus_inspector("动画", "动画"))
        self.buttons["subtitle"].clicked.connect(w.auto_subtitles)
        self.buttons["export"].clicked.connect(w.open_export_dialog)
        # 四个开关：状态存在 MainWindow 上，时间线读它
        self.buttons["magnetic"].setChecked(True)
        self.buttons["snap"].setChecked(True)
        self.buttons["magnetic"].toggled.connect(lambda on: w.set_option("magnetic", on))
        self.buttons["snap"].toggled.connect(lambda on: w.set_option("snap", on))
        self.buttons["link"].toggled.connect(lambda on: w.set_option("link", on))
        self.buttons["playhead_axis"].toggled.connect(
            lambda on: w.set_option("playhead_axis", on))

    def sync_from_project(self) -> None:
        """按工程里的开关状态刷新工具栏（载入工程后调用）。

        「主轨磁吸」是草稿级开关（剪映 DRAFT_SET_MAIN_TRACK_ADSORB_ACTION），
        存进工程文件，所以打开别的工程时要跟着它变。
        """
        value = bool(getattr(self.window.bridge.project, "main_track_adsorb", True))
        button = self.buttons.get("magnetic")
        if button is not None and button.isChecked() != value:
            button.blockSignals(True)
            button.setChecked(value)
            button.blockSignals(False)
        self.window.options["magnetic"] = value

    def _import(self) -> None:
        self.window.bin.import_media()

    # -- 状态 ------------------------------------------------------------- #
    def update_states(self) -> None:
        has_seg = self.bridge.selected is not None
        for key in NEEDS_SEGMENT:
            btn = self.buttons.get(key)
            if btn is None:
                continue
            btn.setEnabled(has_seg)
            if not has_seg:
                btn.setToolTip(f"{btn.toolTip().split('（需先选中片段）')[0]}（需先选中片段）")
        self.buttons["paste"].setEnabled(self.bridge.has_clipboard())
        self.buttons["undo"].setEnabled(bool(self.bridge._undo))
        self.buttons["redo"].setEnabled(bool(self.bridge._redo))

    # -- 测试/自检 --------------------------------------------------------- #
    def labels(self) -> List[str]:
        return [self.buttons[key].text() for key in self.order]

    def enabled_map(self) -> dict:
        return {key: self.buttons[key].isEnabled() for key in self.order}
