"""右键菜单（HANDOFF D3 缺的那一层）。

剪映的右键菜单按「点到了什么」分两种：片段上一套、空白处一套。
**每个动作都对应一个真的存在的能力**；做不到的项灰显并在提示里写明原因
（AI 抠像/音色这类要云端模型的），测试 ``TestUserPathContextMenu`` 会逐项核对
「菜单项是否可点」与「bridge 里是否真有这个方法」一致。

**踩过的坑（第 243 轮，用户实测报"右键菜单全都不能用"）**：
``menu.triggered.connect(handler.run)`` 里的 ``handler`` 如果只是局部变量，
Python 会把它回收，PySide 的连接跟着失效——菜单看起来正常，点下去什么也不发生。
现在 ``_Handler`` 是 QObject 且以菜单为 parent，连接不可能再被回收。
"""

from __future__ import annotations

from typing import Dict, Optional

from PySide6.QtCore import QObject, Slot
from PySide6.QtWidgets import QMenu

#: 片段菜单顺序（剪映桌面版从上到下）
SEGMENT_MENU = [
    ("split", "分割", "Ctrl+K"),
    ("delete", "删除", "Delete"),
    (None, None, None),
    ("copy", "复制", "Ctrl+C"),
    ("cut", "剪切", "Ctrl+X"),
    ("paste", "粘贴", "Ctrl+V"),
    ("duplicate", "复制到末尾", ""),
    (None, None, None),
    ("copy_attributes", "复制属性", ""),
    ("paste_attributes", "粘贴属性", ""),
    (None, None, None),
    ("detach_audio", "分离音频", ""),
    ("mirror_h", "水平镜像", ""),
    ("mirror_v", "垂直镜像", ""),
    ("crop", "裁剪", ""),
    ("reverse", "倒放", ""),
    ("freeze", "定格", ""),
    (None, None, None),
    ("speed", "变速", ""),
    ("animation", "动画", ""),
    ("volume", "音量", ""),
    ("denoise", "降噪", ""),
    (None, None, None),
    ("smart_matting", "智能抠像", ""),
    ("ai_voice", "AI 音色", ""),
]

#: 空白处菜单
EMPTY_MENU = [
    ("paste", "粘贴", "Ctrl+V"),
    ("split_all", "分割所有轨道", "Ctrl+K"),
    (None, None, None),
    ("new_text", "新建文本", ""),
    ("new_adjust", "新建调节图层", ""),
    (None, None, None),
    ("select_tool", "选择工具", "A"),
]

#: 依赖云端、本机做不到的项：直接**不生成菜单项**（第 4 轮按目标删除）
REMOVED_KEYS = {"smart_matting", "ai_voice"}

#: 明确做不到的项：键 → 原因（灰显，不隐藏）
UNAVAILABLE = {
    "smart_matting": "智能抠像要云端人像模型，离线版做不到（可用「色度抠图」近似）",
    "ai_voice": "AI 音色要云端声库，离线版只有系统 SAPI 音色",
}

#: 菜单里**真的有实现**的项。``TestUserPathContextMenu`` 用它核对
#: 「可点的菜单项 ≤ 实际能力」——菜单不能比能力多。
HANDLED_KEYS = {
    "split", "split_all", "delete", "copy", "cut", "paste", "duplicate",
    "copy_attributes", "paste_attributes", "detach_audio",
    "mirror_h", "mirror_v", "crop", "reverse", "freeze",
    "speed", "animation", "volume", "denoise",
    "new_text", "new_adjust",
}


def _action(menu: QMenu, key: str, text: str, shortcut: str, enabled: bool) -> None:
    action = menu.addAction(text + (f"\t{shortcut}" if shortcut else ""))
    action.setData(key)
    action.setEnabled(enabled)
    if key in UNAVAILABLE:
        action.setToolTip(UNAVAILABLE[key])
    menu._actions_by_key[key] = action  # type: ignore[attr-defined]


def build_context_menu(window, seg) -> QMenu:
    """按点击位置生成菜单。``window`` 是 ``MainWindow``。"""
    menu = QMenu(window)
    menu._actions_by_key = {}                 # type: ignore[attr-defined]
    bridge = window.bridge
    has_seg = seg is not None
    playhead_in_seg = bool(
        has_seg and seg.target_timerange.contains(int(bridge.playhead)))
    playhead_any = any(
        t.segment_at(int(bridge.playhead)) is not None for t in bridge.project.tracks)

    for key, text, shortcut in SEGMENT_MENU if has_seg else EMPTY_MENU:
        if key is None:
            menu.addSeparator()
            continue
        enabled = True
        if key in ("split",):
            enabled = playhead_in_seg
        elif key == "split_all":
            enabled = playhead_any
        elif key == "paste":
            enabled = bridge.has_clipboard()
        elif key == "paste_attributes":
            enabled = bridge.has_attributes()
        elif key in ("copy_attributes", "detach_audio"):
            enabled = has_seg
        if key in REMOVED_KEYS:
            continue
        if key in UNAVAILABLE:
            enabled = False
        _action(menu, key, text, shortcut, enabled)

    # handler 必须是菜单的**子对象**：只当局部变量的话会被 Python 回收，
    # PySide 的连接随之失效，菜单点了没反应（用户第 243 轮实测报的就是这个）
    handler = _Handler(window, seg, menu)
    menu.triggered.connect(handler.run)
    menu._handler = handler                     # type: ignore[attr-defined]
    return menu


class _Handler(QObject):
    """菜单动作 → 具体命令。集中在这里，测试可以直接调 ``run(action)``。"""

    def __init__(self, window, seg, parent=None) -> None:
        super().__init__(parent)
        self.window = window
        self.bridge = window.bridge
        self.seg = seg

    def _select_tool(self) -> None:
        """切回选择工具（配合框选：空白处拖出矩形即可多选）。"""
        timeline = getattr(self.window, "timeline", None)
        if timeline is not None and hasattr(timeline, "tool"):
            timeline.tool = "select"
        self.bridge.statusMessage.emit("选择工具：空白处拖出矩形可框选片段")

    @Slot(object)
    def run(self, action) -> None:
        key = action.data() if action is not None else ""
        if not key:
            return
        b = self.bridge
        w = self.window
        if self.seg is not None and b.selected is not self.seg:
            b.select(self.seg)
        table = {
            "select_tool": lambda: self._select_tool(),
            "split": w.split_at_playhead,
            "split_all": lambda: b.split_selected(),
            "delete": w.delete_selected,
            "copy": b.copy_selected,
            "cut": b.cut_selected,
            "paste": lambda: b.paste(),
            "duplicate": lambda: b.duplicate_selected(),
            "copy_attributes": b.copy_attributes,
            "paste_attributes": b.paste_attributes,
            "detach_audio": w.detach_audio,
            "mirror_h": lambda: b.mirror("h"),
            "mirror_v": lambda: b.mirror("v"),
            "crop": lambda: w.focus_inspector("画面", "裁剪"),
            "reverse": lambda: b.reverse_selected(),
            "freeze": lambda: b.freeze_selected(),
            "speed": lambda: w.focus_inspector("变速", "变速"),
            "animation": lambda: w.focus_inspector("动画", "动画"),
            "volume": lambda: w.focus_inspector("音频", "音量"),
            "denoise": lambda: w.focus_inspector("音频", "降噪"),
            "new_text": w.new_text_at_playhead,
            "new_adjust": w.new_adjust_at_playhead,
        }
        func = table.get(key)
        if func is not None:
            func()
