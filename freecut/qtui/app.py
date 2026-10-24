"""Qt 版主窗口：剪映式的顶栏 + 工具条 + 三栏 + 时间线工具栏。

布局照剪映专业版（HANDOFF §5.1）：

    顶栏   ☰ 菜单 / 工程名 / 自动保存状态 ……… 比例 / 分辨率 / 导出 / 分享 / 通知
    工具条 新建 打开 保存 导入媒体 ……… 导出剪映草稿 导出
    三栏   左：类别栏 + 内容页   中：预览 + 播放条 + 时间线工具栏 + 时间线   右：属性
    状态栏 操作反馈（撤销提示、保存提示……）
"""

from __future__ import annotations

import os
import sys
import time
from typing import Optional

from PySide6.QtCore import QElapsedTimer, QTimer, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog, QHBoxLayout,
                               QLabel, QMainWindow, QMessageBox, QSplitter,
                               QToolBar, QToolButton, QVBoxLayout, QWidget)

from .. import theme
from .bridge import Bridge
from .menus import build_context_menu
from .theme_qss import stylesheet
from .timecode import format_timecode
from .widgets.bin_panel import BinPanel, Rail
from .widgets.inspector import Inspector
from .widgets.timeline import TimelineView
from .widgets.toolbar import TimelineToolbar
from .widgets.viewer import PlayerBar, Viewer

#: 转场引擎 → 中文名（与 Tk 版同一份表：ENGINE_LABELS 在 gui.py 里）
from ..gui import ENGINE_LABELS  # noqa: E402  （Tk 版模块级常量，纯数据）

#: 比例 → 画布尺寸（剪映「比例」下拉的常用几档）
RATIO_SIZES = {
    "16:9": (1280, 720), "9:16": (720, 1280), "1:1": (1080, 1080),
    "4:3": (960, 720), "3:4": (720, 960), "21:9": (1680, 720),
}
RESOLUTIONS = {"720p": 720, "1080p": 1080, "2K": 1440, "4K": 2160}


class MainWindow(QMainWindow):
    def __init__(self, project_path: Optional[str] = None):
        super().__init__()
        from .. import UI_BUILD

        self.build_tag = UI_BUILD
        self.setWindowTitle(f"FreeCut · Qt · {UI_BUILD}")
        self.resize(1460, 880)
        self.setMinimumSize(theme.MIN_WIDTH, theme.MIN_HEIGHT)
        self.setStyleSheet(stylesheet())

        self.bridge = Bridge()
        #: 预览音频（剪映的 AudioBackend 结构；见 freecut/audio_engine.py）
        from ..audio_engine import AudioEngine

        self.audio = AudioEngine(self.bridge)
        self._playing = False
        self._play_clock = QElapsedTimer()
        self._play_base = 0
        self._play_timer = QTimer(self)
        self._play_timer.setInterval(33)
        self._play_timer.timeout.connect(self._tick)
        #: 时间线开关（工具栏上的四个 toggle）
        self.options = {"magnetic": True, "snap": True, "link": True,
                        "playhead_axis": False}
        self._opened_at = time.time()
        self._last_autosave = 0.0
        QTimer.singleShot(0, self._recover_on_startup)
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(60_000)
        self._autosave_timer.timeout.connect(self.autosave)
        self._autosave_timer.start()

        # 后台先把音频设备打开：等用户点播放才开始打开的话，那 1 秒会算在"播放卡顿"里
        # （bench 实测 maxstall 1.4s → 现在点播放是瞬开）
        QTimer.singleShot(400, self._prepare_audio)

        self._build_ui()
        self._build_menu()
        if project_path and os.path.isfile(project_path):
            self.bridge.load(project_path)
        self._refresh_all()

    # -- 布局 ------------------------------------------------------------- #
    def _build_ui(self) -> None:
        central = QWidget(self)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(self._title_bar())
        outer.addWidget(self._tool_bar())

        self.splitter = QSplitter(Qt.Horizontal, central)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(3)

        # 左：类别栏 + 内容页
        left = QWidget(self.splitter)
        left_box = QHBoxLayout(left)
        left_box.setContentsMargins(0, 0, 0, 0)
        left_box.setSpacing(0)
        self.rail = Rail(left)
        self.bin = BinPanel(self.bridge, left)
        left_box.addWidget(self.rail)
        left_box.addWidget(self.bin, 1)

        # 中：预览 + 播放条 + 时间线工具栏 + 时间线
        center = QWidget(self.splitter)
        center_box = QVBoxLayout(center)
        center_box.setContentsMargins(0, 0, 0, 0)
        center_box.setSpacing(0)
        self.viewer = Viewer(self.bridge, center)
        self._edit_shortcuts = []
        self.player = PlayerBar(self.bridge, self.toggle_play, center,
                                on_seek=self._on_user_seek)
        self.timeline_toolbar = TimelineToolbar(self, center)
        self.timeline = TimelineView(self.bridge, center)
        self.timeline.setFixedHeight(24 + 5 * 40 + 10)   # 表头 + 5 行，和 Tk 版同高
        # 24 个文字按钮加起来有一千多像素宽，直接放布局里会把左右两栏挤没；
        # 套一层横向滚动区：窗口窄的时候滚动，三栏宽度不受工具栏影响。
        from PySide6.QtWidgets import QFrame, QScrollArea

        tool_scroll = QScrollArea(center)
        tool_scroll.setObjectName("ToolScroll")
        tool_scroll.setWidgetResizable(True)
        tool_scroll.setFrameShape(QFrame.NoFrame)
        tool_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        tool_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        tool_scroll.setFixedHeight(38)
        tool_scroll.setMinimumWidth(200)
        tool_scroll.setWidget(self.timeline_toolbar)
        center_box.addWidget(self.viewer, 1)
        center_box.addWidget(self.player)
        center_box.addWidget(tool_scroll)
        center_box.addWidget(self.timeline, 0)
        center_box.addWidget(self._track_bar(center))

        # 右：属性
        self.inspector = Inspector(self.bridge, self.splitter)

        self.splitter.addWidget(left)
        self.splitter.addWidget(center)
        self.splitter.addWidget(self.inspector)
        # 左右两栏不抢空间：窗口变宽只让中间预览变大。
        # 不设这个的话，1400 宽时右栏被压到 72px（属性面板等于没有）。
        left.setMinimumWidth(theme.px(240))
        # 属性面板的滑杆 + 数字框 + 关键帧菱形，低于 330 就会把数字后缀切掉
        # （第 242 轮截图实测：试过 300，"100%" 只剩 "100"）
        self.inspector.setMinimumWidth(theme.px(350))
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 0)
        left_w, mid_w, right_w = theme.panes_for(self.width())
        self.splitter.setSizes([left_w, mid_w, right_w])
        outer.addWidget(self.splitter, 1)

        self.status_label = QLabel("就绪", self)
        self.status_label.setObjectName("StatusBar")
        outer.addWidget(self.status_label)
        self.setCentralWidget(central)

        # 信号
        self.rail.group.idClicked.connect(self._on_category)
        self.timeline.playheadRequested.connect(self._on_user_seek)
        self.bin.media_list.itemClicked.connect(self._on_media_clicked)
        self.viewer.tabs.currentChanged.connect(
            lambda index: self.player.set_source_mode(index == 1))
        # 选中由时间线自己处理（Ctrl+点 = 加选、空白拖动 = 框选），这里不再重复 select
        self.timeline.segmentPicked.connect(self._on_segment_picked)
        self.timeline.edited.connect(lambda text: self.status_label.setText(text))
        self.timeline.contextRequested.connect(self._show_context_menu)
        self.timeline.libraryDropped.connect(self._on_library_dropped)
        self.bridge.edited.connect(self._after_edit)
        self.bridge.statusMessage.connect(self.status_label.setText)
        self.bridge.selectionChanged.connect(lambda _s: self.viewer.schedule())
        # 打开/新建工程后轨道条上的计数要跟着变
        self.bridge.projectReloaded.connect(self._refresh_track_count)
        self.rail.buttons[self.first_available_category()].setChecked(True)

    def first_available_category(self) -> str:
        for name in theme.QT_CATEGORIES:
            if name not in theme.CLOUD_ONLY:
                return name
        return theme.QT_CATEGORIES[0]

    def showEvent(self, event):                      # noqa: N802
        """窗口第一次显示时按剪映的三栏比例分宽度。

        构造时窗口还没有真实尺寸，`setSizes` 会被最小宽度盖掉（实测 1400 宽时
        右栏只剩 72px）；等几何定了再分一次才准。
        """
        super().showEvent(event)
        if not getattr(self, "_panes_applied", False):
            self._panes_applied = True
            left_w, mid_w, right_w = theme.panes_for(self.width())
            self.splitter.setSizes([left_w, mid_w, right_w])

    def _track_bar(self, parent) -> QWidget:
        """时间线下面那一条「+ 视频轨 / + 音频轨 / + 文字轨」。

        剪映靠"拖到空白处"加轨，我们两种都支持：拖到空白会自动新建，
        也可以直接点这里加一条空轨道（加完就是一条新行）。
        """
        bar = QWidget(parent)
        bar.setObjectName("TrackBar")
        box = QHBoxLayout(bar)
        box.setContentsMargins(8, 2, 8, 4)
        box.setSpacing(6)
        label = QLabel("轨道", bar)
        box.addWidget(label)
        self.track_buttons = {}
        for ttype, text in (("video", "+ 视频轨"), ("audio", "+ 音频轨"),
                            ("text", "+ 文字轨")):
            button = QToolButton(bar)
            button.setText(text)
            button.setToolTip(f"在时间线上加一条{text[2:]}（也可以把素材拖到空白处）")
            button.clicked.connect(lambda _checked=False, t=ttype: self.add_track(t))
            box.addWidget(button)
            self.track_buttons[ttype] = button
        box.addStretch(1)
        self.track_count_label = QLabel("", bar)
        self.track_count_label.setObjectName("TrackCount")
        box.addWidget(self.track_count_label)
        self._refresh_track_count()
        return bar

    def _refresh_track_count(self) -> None:
        if not hasattr(self, "track_count_label"):
            return
        counts = {}
        for track in self.bridge.project.tracks:
            counts[track.type] = counts.get(track.type, 0) + 1
        parts = [f"视频 {counts.get('video', 0)}", f"音频 {counts.get('audio', 0)}"]
        if counts.get("text"):
            parts.append(f"文字 {counts['text']}")
        self.track_count_label.setText(" · ".join(parts))

    def add_track(self, ttype: str = "video"):
        """加一条空轨道，并把它放到同类轨道之后（行顺序由 rows() 决定）。"""
        from ..model import Track

        with self.bridge.render_lock:
            track = Track(type=ttype)
            position = len(self.bridge.project.tracks)
            for index, existing in enumerate(self.bridge.project.tracks):
                if existing.type == ttype:
                    position = index + 1
            if position >= len(self.bridge.project.tracks):
                self.bridge.project.tracks.append(track)
            else:
                self.bridge.project.tracks.insert(position, track)
            self.bridge.touch()
        self.bridge.projectReloaded.emit()
        self._refresh_track_count()
        names = {"video": "视频轨", "audio": "音频轨", "text": "文字轨"}
        self.status_label.setText(f"已添加一条{names.get(ttype, ttype)}："
                                  f"把素材拖到这一行，或点素材栏的「添加到时间线」")
        return track

    def _title_bar(self) -> QWidget:
        """剪映顶栏：菜单入口 / 工程名 / 自动保存状态 … 比例 / 分辨率 / 导出 / 分享 / 通知。"""
        bar = QWidget(self)
        bar.setObjectName("TitleBar")
        box = QHBoxLayout(bar)
        box.setContentsMargins(6, 0, 8, 0)
        box.setSpacing(8)

        self.menu_btn = QToolButton(bar)
        self.menu_btn.setObjectName("MenuButton")
        self.menu_btn.setText("☰")
        self.menu_btn.setToolTip("主菜单")
        self.menu_btn.setPopupMode(QToolButton.InstantPopup)
        box.addWidget(self.menu_btn)

        name = QLabel("FreeCut · 免费视频剪辑", bar)
        name.setObjectName("AppName")
        name.setToolTip(f"构建版本 {self.build_tag}（顶栏点「帮助 → 关于」也能看）")
        self.build_label = QLabel(self.build_tag, bar)
        self.build_label.setObjectName("BuildTag")
        self.build_label.setToolTip("这是哪一次构建——换版本时先看这里")
        self.doc_label = QLabel("未命名工程", bar)
        self.doc_label.setObjectName("DocName")
        box.addWidget(name)
        box.addWidget(self.build_label)
        box.addWidget(self.doc_label)

        self.autosave_label = QLabel("尚未自动保存", bar)
        self.autosave_label.setObjectName("Autosave")
        self.autosave_label.setToolTip("工程每 60 秒自动存一份到临时目录（剪映的健康提示位）")
        box.addWidget(self.autosave_label)
        box.addStretch(1)

        self.ratio_box = QComboBox(bar)
        for ratio in RATIO_SIZES:
            self.ratio_box.addItem(ratio)
        self.ratio_box.setToolTip("画面比例（会改画布尺寸）")
        self.ratio_box.currentTextChanged.connect(self.set_ratio)
        box.addWidget(self.ratio_box)

        self.resolution_box = QComboBox(bar)
        for key in RESOLUTIONS:
            self.resolution_box.addItem(key)
        self.resolution_box.setToolTip("导出/预览的分辨率档")
        self.resolution_box.currentTextChanged.connect(self.set_resolution)
        box.addWidget(self.resolution_box)

        for text, tip, slot, enabled in (
            ("导出", "导出成片（Ctrl+E）", self.open_export_dialog, True),
            ("分享", "剪映的分享是发布到平台，FreeCut 不上传", None, False),
        ):
            btn = QToolButton(bar)
            btn.setText(text)
            btn.setToolTip(tip)
            if slot is not None:
                btn.clicked.connect(slot)
            btn.setEnabled(enabled)
            box.addWidget(btn)

        self.notify_btn = QToolButton(bar)
        self.notify_btn.setText("通知")
        self.notify_btn.setToolTip("最近的操作反馈")
        self.notify_btn.clicked.connect(self.show_notifications)
        box.addWidget(self.notify_btn)
        return bar

    def _tool_bar(self) -> QWidget:
        bar = QToolBar(self)
        bar.setObjectName("ToolBar")
        bar.setMovable(False)
        bar.setFloatable(False)
        for text, slot in (
            ("新建", self.new_project),
            ("打开", self.open_project),
            ("保存", self.save_project),
        ):
            act = QAction(text, self)
            act.triggered.connect(slot)
            bar.addAction(act)
        bar.addSeparator()
        for text, slot in (("导入剪映草稿", self.import_draft), ("导入媒体", self.import_media)):
            act = QAction(text, self)
            act.triggered.connect(slot)
            bar.addAction(act)
        spacer = QWidget(bar)
        spacer.setSizePolicy(spacer.sizePolicy().horizontalPolicy().Expanding,
                             spacer.sizePolicy().verticalPolicy())
        bar.addWidget(spacer)
        for text, slot in (("导出剪映草稿", self.export_draft), ("导出", self.open_export_dialog)):
            act = QAction(text, self)
            act.triggered.connect(slot)
            bar.addAction(act)
        return bar

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("文件")
        for text, seq, slot in (
            ("新建工程", QKeySequence.New, self.new_project),
            ("打开工程…", QKeySequence.Open, self.open_project),
            ("保存工程", QKeySequence.Save, self.save_project),
        ):
            act = QAction(text, self)
            act.setShortcut(seq)
            act.triggered.connect(slot)
            file_menu.addAction(act)
        file_menu.addSeparator()
        draft_act = QAction("导入剪映草稿…", self)
        draft_act.triggered.connect(self.import_draft)
        file_menu.addAction(draft_act)
        out_draft = QAction("导出剪映草稿…", self)
        out_draft.triggered.connect(self.export_draft)
        file_menu.addAction(out_draft)
        xml_act = QAction("导出 FCP7 XML…", self)
        xml_act.setToolTip("给 Premiere / Resolve / Final Cut 用")
        xml_act.triggered.connect(self.export_fcp7_ui)
        file_menu.addAction(xml_act)
        prproj_act = QAction("导出 .prproj…", self)
        prproj_act.setToolTip("结构近似；未在 Premiere 里验证过")
        prproj_act.triggered.connect(self.export_prproj_ui)
        file_menu.addAction(prproj_act)
        script_act = QAction("图文成片…", self)
        script_act.setToolTip("粘贴文案，用本机 SAPI 配音并生成时间线")
        script_act.triggered.connect(self.from_script_ui)
        file_menu.addAction(script_act)
        relink_act = QAction("重新链接缺失素材…", self)
        relink_act.setToolTip("素材被移动/改名后，按文件名在新目录里找回")
        relink_act.triggered.connect(self.relink_materials)
        file_menu.addAction(relink_act)
        self.recent_menu = file_menu.addMenu("最近打开")
        self._refresh_recent_menu()
        caps_act = QAction("硬件加速与编码器…", self)
        caps_act.setToolTip("查看本机能用的硬件解码/编码器（对应 freecut caps）")
        caps_act.triggered.connect(self.caps_ui)
        file_menu.addAction(caps_act)
        export_act = QAction("导出成片…", self)
        export_act.setShortcut("Ctrl+E")
        export_act.triggered.connect(self.open_export_dialog)
        file_menu.addAction(export_act)
        file_menu.addSeparator()
        quit_act = QAction("退出", self)
        quit_act.setShortcut(QKeySequence.Quit)
        quit_act.triggered.connect(self.close)
        file_menu.addAction(quit_act)
        self.menu_btn.setMenu(file_menu)

        edit_menu = self.menuBar().addMenu("编辑")
        undo_act = QAction("撤销", self)
        undo_act.setShortcut(QKeySequence.Undo)
        undo_act.triggered.connect(self.undo)
        edit_menu.addAction(undo_act)
        redo_act = QAction("重做", self)
        redo_act.setShortcut(QKeySequence.Redo)
        redo_act.triggered.connect(self.redo)
        edit_menu.addAction(redo_act)
        edit_menu.addSeparator()
        for text, seq, slot in (
            ("复制片段", "Ctrl+C", lambda: self.bridge.copy_selected()),
            ("剪切片段", "Ctrl+X", lambda: self.bridge.cut_selected()),
            ("粘贴片段", "Ctrl+V", lambda: self.bridge.paste()),
            ("复制属性", "Ctrl+Alt+C", lambda: self.bridge.copy_attributes()),
            ("粘贴属性", "Ctrl+Alt+V", lambda: self.bridge.paste_attributes()),
        ):
            act = QAction(text, self)
            act.setShortcut(seq)
            act.triggered.connect(slot)
            edit_menu.addAction(act)
        edit_menu.addSeparator()
        for text, seq, slot in (
            ("标记入点", "I", self.mark_in),
            ("标记出点", "O", self.mark_out),
            ("插入到时间线", ",", self.insert_source),
            ("覆盖到时间线", ".", self.overwrite_source),
        ):
            act = QAction(text, self)
            act.setShortcut(seq)
            act.triggered.connect(slot)
            edit_menu.addAction(act)

        timeline_menu = self.menuBar().addMenu("时间线")
        self.actions = {}
        for key, text, seq, slot in (
            ("split", "分割（播放头）", "Ctrl+K", self.split_at_playhead),
            ("delete", "删除片段", "Delete", self.delete_selected),
            ("ripple_delete", "波纹删除（闭合空隙）", "Shift+Delete", self.ripple_delete_selected),
            ("duplicate", "复制到末尾", "Ctrl+D", lambda: self.bridge.duplicate_selected()),
            ("mirror", "水平镜像", "Ctrl+M", lambda: self.bridge.mirror("h")),
            ("detach_audio", "分离音频", "Ctrl+Shift+D", self.detach_audio),
        ):
            act = QAction(text, self)
            act.setShortcut(seq)
            act.triggered.connect(slot)
            timeline_menu.addAction(act)
            self.actions[key] = act
        timeline_menu.addSeparator()
        for text, slot in (("倒放", lambda: self.bridge.reverse_selected()),
                           ("定格 2 秒", lambda: self.bridge.freeze_selected()),
                           ("把这一帧设为封面", lambda: self.bridge.set_cover()),
                           ("封面回到第一帧", lambda: self.bridge.set_cover(0))):
            act = QAction(text, self)
            act.triggered.connect(slot)
            timeline_menu.addAction(act)

        fit_act = QAction("缩放到全部", self)
        fit_act.setShortcut(QKeySequence("Ctrl+0"))
        fit_act.triggered.connect(self.fit_timeline)
        timeline_menu.addAction(fit_act)
        height_menu = timeline_menu.addMenu("轨道高度")
        for text, height in (("紧凑（28px）", 28), ("标准（40px）", 40), ("高（56px）", 56)):
            act = QAction(text, self)
            act.triggered.connect(lambda _checked=False, h=height: self.timeline.set_row_h(h))
            height_menu.addAction(act)

        timeline_menu.addSeparator()
        group_act = QAction("编组选中片段", self)
        group_act.setShortcut(QKeySequence("Ctrl+G"))
        group_act.triggered.connect(lambda: self.bridge.group_selected())
        timeline_menu.addAction(group_act)
        ungroup_act = QAction("取消编组", self)
        ungroup_act.setShortcut(QKeySequence("Ctrl+Shift+G"))
        ungroup_act.triggered.connect(lambda: self.bridge.ungroup_selected())
        timeline_menu.addAction(ungroup_act)

        seq_menu = timeline_menu.addMenu("时间线（多序列）")
        new_seq = QAction("新建一条时间线", self)
        new_seq.triggered.connect(lambda: (self.bridge.new_sequence(), self.viewer.refresh_sequences()))
        seq_menu.addAction(new_seq)
        del_seq = QAction("删除当前时间线", self)
        del_seq.triggered.connect(lambda: (self.bridge.delete_sequence(),
                                           self.viewer.refresh_sequences()))
        seq_menu.addAction(del_seq)

        tools = self.menuBar().addMenu("工具")
        for text, slot in (("文档自检", self.doc_selfcheck),
                           ("快捷键…", self.show_shortcuts)):
            act = QAction(text, self)
            act.triggered.connect(slot)
            tools.addAction(act)

        help_menu = self.menuBar().addMenu("帮助")
        shortcuts_act = QAction("快捷键…", self)
        shortcuts_act.setShortcut(QKeySequence("?"))

        # 剪映常用键：空格播放/暂停、S 分割、左右方向键逐帧
        from PySide6.QtGui import QShortcut

        for seq, slot in (
            ("Space", self.toggle_play),
            ("S", self.split_at_playhead),
            ("Right", lambda: self.step_frames(1)),
            ("Left", lambda: self.step_frames(-1)),
        ):
            shortcut = QShortcut(QKeySequence(seq), self)
            shortcut.activated.connect(slot)
            self._edit_shortcuts.append(shortcut)

        shortcuts_act.triggered.connect(self.show_shortcuts)
        help_menu.addAction(shortcuts_act)
        about = QAction("关于 FreeCut", self)
        from .. import UI_BUILD, __version__

        about.triggered.connect(lambda: QMessageBox.information(
            self, "关于",
            f"FreeCut · Qt 版\n构建版本：{UI_BUILD}（内核 {__version__}）\n"
            "界面复现剪映，编辑模型借鉴 Premiere。"))
        help_menu.addAction(about)

    # -- 动作：选中相关 ---------------------------------------------------- #
    def split_at_playhead(self) -> None:
        made = self.bridge.split_selected()
        if made:
            self._after_edit()

    def delete_selected(self) -> None:
        if self.bridge.delete_selected():
            self._after_edit()

    def ripple_delete_selected(self) -> None:
        if self.bridge.delete_selected(ripple=True):
            self._after_edit()

    def detach_audio(self) -> None:
        """分离音频：把视频里的声音拆到音频轨，并把原片段静音（剪映行为）。"""
        from .. import builder

        seg = self.bridge.selected
        if seg is None:
            self.bridge.statusMessage.emit("先选中一个带声音的视频片段")
            return
        path = self.bridge._media_path_of(seg)
        if not path or not os.path.isfile(path):
            self.bridge.statusMessage.emit("这个片段没有素材文件，无法分离音频")
            return
        track = self.bridge.track_of_segment(seg)
        if track is None:
            return
        with self.bridge.render_lock:
            before = self.bridge._snapshot([track])
            try:
                new_seg = builder.add_audio(
                    self.bridge.project, path,
                    start=int(seg.target_timerange.start),
                    source_in=int(seg.source_timerange.start),
                    duration=int(seg.target_timerange.duration))
            except Exception as exc:                   # noqa: BLE001
                self.bridge.statusMessage.emit(f"分离音频失败: {exc}")
                return
            seg.volume = 0.0
            self.bridge.select(new_seg)
            self.bridge._record_structure(before, "分离音频")
        self.bridge.projectReloaded.emit()
        self.bridge.statusMessage.emit("已分离音频到音频轨（原片段已静音）")

    def new_text_at_playhead(self) -> None:
        from .. import builder

        with self.bridge.render_lock:
            seg = builder.add_text(self.bridge.project, "双击右侧文字页改内容",
                                   start=int(self.bridge.playhead), duration=3_000_000)
            self.bridge.select(seg)
            self.bridge.touch()
        self.bridge.projectReloaded.emit()
        self.focus_inspector("文字")
        self.bridge.statusMessage.emit("已新建文本片段")

    def new_adjust_at_playhead(self) -> None:
        self.bin.add_adjustment_layer()
        self.focus_inspector("调节")

    def focus_inspector(self, page_name: str, keyword: str = "") -> bool:
        """工具栏/右键菜单的「打开某一页」：切页并滚到对应分组。"""
        self.inspector.show()
        return self.inspector.focus(page_name, keyword)

    def auto_subtitles(self) -> None:
        self.rail.buttons["字幕"].setChecked(True)
        self.bin.show_category("字幕")
        self.bin.auto_subtitles()

    def set_option(self, key: str, value: bool) -> None:
        """时间线工具栏上的四个开关。

        「主轨磁吸」是**草稿级**开关（剪映里叫 DRAFT_SET_MAIN_TRACK_ADSORB_ACTION，
        见 docs/measurements/RUNTIME-CLOCK-TRACK-GPU.md §2），所以写进工程而不是只放内存。
        """
        self.options[key] = bool(value)
        self.timeline.snap_enabled = bool(self.options["snap"])
        if key == "magnetic":
            self.bridge.project.main_track_adsorb = bool(value)
            self.bridge.edited.emit()
        label = {"magnetic": "主轨磁吸", "snap": "自动吸附",
                 "link": "联动", "playhead_axis": "预览轴"}.get(key, key)
        self.status_label.setText(f"{label}：{'开' if value else '关'}")

    # -- 比例 / 分辨率 ------------------------------------------------------ #
    def set_ratio(self, name: str) -> None:
        size = RATIO_SIZES.get(name)
        if not size:
            return
        with self.bridge.render_lock:
            self.bridge.project.canvas.ratio = name
            self.bridge.project.canvas.width, self.bridge.project.canvas.height = size
            self.bridge.touch()
        self._refresh_all()
        self.status_label.setText(f"比例 {name}（{size[0]}×{size[1]}）")

    def set_resolution(self, key: str) -> None:
        height = RESOLUTIONS.get(key)
        if not height:
            return
        ratio = self.bridge.project.canvas.ratio or "16:9"
        try:
            num, den = (float(x) for x in ratio.split(":"))
        except Exception:                              # noqa: BLE001
            num, den = 16.0, 9.0
        width = int(round(height * num / max(den, 1e-6) / 2.0)) * 2
        with self.bridge.render_lock:
            self.bridge.project.canvas.width = width
            self.bridge.project.canvas.height = height
            self.bridge.touch()
        self._refresh_all()
        self.status_label.setText(f"分辨率 {key}（{width}×{height}）")

    # -- 自动保存 / 通知 --------------------------------------------------- #
    def _recover_on_startup(self) -> None:
        """构造完成后尝试恢复；用户拒绝就什么都不做。

        注意：这里会弹模态框。自检/离屏跑的时候没人点，会永久卡住，
        所以这两种情况直接跳过（只影响"要不要问"，不影响恢复能力本身）。
        """
        try:
            import sys

            if os.environ.get("QT_QPA_PLATFORM", "").lower() == "offscreen":
                return
            if os.environ.get("FREECUT_NO_PROMPTS"):
                return
            if any(arg in sys.argv for arg in
                   ("gui-smoke", "media-smoke", "audio-smoke", "caps")):
                return
            if self.bridge.path:            # 有正常打开的工程就不打扰
                return
            self.recover_autosave()
        except Exception:                              # noqa: BLE001
            pass

    def _autosave_dir(self) -> str:
        import tempfile

        return os.path.join(tempfile.gettempdir(), "freecut-autosave")

    def latest_autosave(self):
        """返回最近一份自动保存的路径（没有就 None）。"""
        folder = self._autosave_dir()
        if not os.path.isdir(folder):
            return None
        files = [os.path.join(folder, n) for n in os.listdir(folder)
                 if n.lower().endswith(".json")]
        files = [f for f in files if os.path.isfile(f)]
        return max(files, key=os.path.getmtime) if files else None

    def recover_autosave(self, ask=None) -> bool:
        """有自动保存就询问是否恢复；返回是否真的恢复了。"""
        path = self.latest_autosave()
        if not path:
            return False
        if ask is None:
            ask = lambda p: QMessageBox.question(            # noqa: E731
                self, "恢复自动保存",
                f"发现自动保存（{time.strftime('%m-%d %H:%M', time.localtime(os.path.getmtime(p)))}）：\n"
                f"{os.path.basename(p)}\n\n上次可能没有正常退出，要恢复吗？",
                QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes
        if not ask(path):
            return False
        if not self.bridge.load(path):
            return False
        self._refresh_all()
        self.status_label.setText("已恢复上次自动保存的内容")
        return True

    def clear_autosave(self) -> None:
        """正常退出时删掉自动保存，避免下次误提示。"""
        try:
            path = self.latest_autosave()
            if path:
                os.remove(path)
        except OSError:
            pass

    def autosave(self) -> None:
        """每 60 秒存一份到临时目录（不改用户工程文件）。"""
        import tempfile

        folder = os.path.join(tempfile.gettempdir(), "freecut-autosave")
        try:
            os.makedirs(folder, exist_ok=True)
            name = os.path.splitext(os.path.basename(self.bridge.path or "未命名"))[0]
            target = os.path.join(folder, f"{name}.json")
            self.bridge.project.save(target)
        except Exception as exc:                       # noqa: BLE001
            self.autosave_label.setText(f"自动保存失败：{exc}")
            return
        self._last_autosave = time.time()
        stamp = time.strftime("%H:%M:%S", time.localtime(self._last_autosave))
        self.autosave_label.setText(f"{stamp} 自动保存本地")
        self.autosave_label.setToolTip(target)

    def show_notifications(self) -> None:
        QMessageBox.information(
            self, "通知",
            f"最近一条：{self.status_label.text()}\n\n"
            f"自动保存：{self.autosave_label.text()}\n"
            f"已打开 {max(0, int(time.time() - self._opened_at))} 秒\n\n"
            "FreeCut 不上传任何数据；这里的「通知」只显示本机操作反馈。")

    def doc_selfcheck(self) -> None:
        from .. import selfcheck

        try:
            report = selfcheck.run()
        except Exception as exc:                       # noqa: BLE001
            self.status_label.setText(f"自检失败: {exc}")
            return
        QMessageBox.information(self, "文档自检", str(report)[:2000])

    # -- 导入/导出 --------------------------------------------------------- #
    def import_media(self) -> None:
        self.bin.import_media()

    def import_draft(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择剪映草稿目录")
        if not folder:
            return
        from .. import jianying

        try:
            project = jianying.load_project_draft(folder)
        except Exception as exc:                       # noqa: BLE001
            QMessageBox.warning(self, "导入失败", str(exc))
            return
        self.bridge.set_project(project, None)
        self._refresh_all()
        self.status_label.setText(f"已导入草稿：{os.path.basename(folder)}")

    def caps_ui(self) -> None:
        from .dialogs.caps_dialog import CapsDialog

        CapsDialog(self).exec()

    def from_script_ui(self) -> None:
        from .dialogs.script_dialog import ScriptDialog

        dialog = ScriptDialog(self.bridge.project, self)
        if dialog.exec() != dialog.Accepted or not dialog.stats:
            return
        stats = dialog.stats
        self.bridge.set_project(stats["project"])
        self._refresh_all()
        self.status_label.setText(
            f"图文成片：{len(stats['sentences'])} 句 / {stats['total_us'] / 1e6:.1f} 秒")

    def export_fcp7_ui(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        project = getattr(self.bridge, "project", None)
        if project is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出 FCP7 XML", "timeline.xml",
                                              "FCP7 XML (*.xml)")
        if path:
            self.export_fcp7_to(path)

    def export_fcp7_to(self, path: str) -> str:
        from .. import interchange

        written = interchange.write_fcp7(self.bridge.project, path)
        self.status_label.setText(f"已导出 XML：{os.path.basename(written)}")
        return written

    def export_prproj_ui(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getSaveFileName(self, "导出 .prproj", "project.prproj",
                                              "Premiere 工程 (*.prproj)")
        if path:
            self.export_prproj_to(path)

    def export_prproj_to(self, path: str) -> str:
        from ..prproj_export import export_prproj

        written = export_prproj(self.bridge.project, path)
        self.status_label.setText(f"已导出 prproj：{os.path.basename(written)}")
        return written

    def export_draft(self) -> None:
        from .. import jianying_export

        folder = QFileDialog.getExistingDirectory(self, "选择写出草稿的目录")
        if not folder:
            return
        try:
            path = jianying_export.write_draft(self.bridge.project, folder)
        except Exception as exc:                       # noqa: BLE001
            QMessageBox.warning(self, "导出草稿失败", str(exc))
            return
        self.status_label.setText(f"草稿已写出：{path}")

    # -- 右键菜单 ---------------------------------------------------------- #
    def _show_context_menu(self, seg, global_pos) -> None:
        menu = build_context_menu(self, seg)
        menu.exec(global_pos)

    def _on_segment_picked(self, seg) -> None:
        """时间线已改好选中集合，这里只需要让预览/属性跟上（选中本身不再重复设置）。"""
        self.viewer.schedule()

    def _on_library_dropped(self, payload, seg) -> None:
        """素材库的特效/转场被拖到时间线某个片段上。"""
        kind = str(payload.get("kind") or "")
        value = str(payload.get("value") or "")
        if seg is None or not value:
            return
        if kind == "effect":
            self.bin.apply_effect_to(seg, value)
        elif kind == "transition":
            self.bin.apply_transition_to(seg, value)
        self._after_edit()

    # -- 动作 ------------------------------------------------------------- #
    def _on_media_clicked(self, _item) -> None:
        picked = self.bin._selected_material()
        if picked is None:
            return
        path, kind = picked
        self.open_source(path, kind)

    def _on_category(self, index: int) -> None:
        name = theme.QT_CATEGORIES[index]
        if name in theme.CLOUD_ONLY:
            self.status_label.setText(f"{name}：{theme.CLOUD_ONLY[name]}")
            return
        self.bin.show_category(name)

    def new_project(self) -> None:
        from .. import builder

        self.bridge.set_project(builder.new_project(1280, 720, 30), None)
        self._refresh_all()

    def _refresh_recent_menu(self) -> None:
        from .. import recent

        menu = getattr(self, "recent_menu", None)
        if menu is None:
            return
        menu.clear()
        items = recent.load()
        if not items:
            empty = menu.addAction("（还没有）")
            empty.setEnabled(False)
            return
        for path in items:
            action = menu.addAction(os.path.basename(path))
            action.setToolTip(path)
            action.triggered.connect(lambda _checked=False, p=path: self.open_recent(p))

    def open_recent(self, path: str) -> None:
        if self.bridge.load(path):
            self._refresh_all()
            self._refresh_recent_menu()

    def remember_recent(self, path: str) -> None:
        from .. import recent

        try:
            recent.add(path)
        except Exception:                              # noqa: BLE001
            pass
        self._refresh_recent_menu()

    def open_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "打开工程", "", "工程 (*.json);;所有文件 (*)")
        if path:
            if self.bridge.load(path):
                self._refresh_all()
                self.remember_recent(path)

    def save_project(self) -> None:
        if self.bridge.path:
            self.bridge.save()
            self.remember_recent(self.bridge.path)
        else:
            path, _ = QFileDialog.getSaveFileName(self, "保存工程", "project.json", "工程 (*.json)")
            if path:
                self.bridge.save(path)
        self._refresh_all()

    def undo(self) -> None:
        self.bridge.undo()
        self._after_edit()

    def redo(self) -> None:
        self.bridge.redo()
        self._after_edit()

    def _after_edit(self) -> None:
        """片段被改过：时间线重画、预览重渲。"""
        self.timeline.refresh()
        self.viewer.schedule()
        self._refresh_track_count()
        if hasattr(self, "timeline_toolbar"):
            self.timeline_toolbar.update_states()

    # -- 三点编辑 --------------------------------------------------------- #
    def mark_in(self) -> None:
        self.bridge.mark_source_in()
        self.viewer.schedule()

    def mark_out(self) -> None:
        self.bridge.mark_source_out()
        self.viewer.schedule()

    def insert_source(self) -> None:
        self.bridge.insert_from_source()
        self._after_edit()

    def overwrite_source(self) -> None:
        self.bridge.overwrite_from_source()
        self._after_edit()

    def open_source(self, path: str, kind: str) -> None:
        """左栏单击素材：装进源监视器并切到「源」标签（PR 的源监视器）。"""
        self.bridge.load_source(path, kind)
        self.viewer.tabs.setCurrentIndex(1)
        self.viewer.schedule()

    def show_shortcuts(self) -> None:
        from .dialogs.shortcuts import ShortcutsDialog

        ShortcutsDialog(self).exec()

    def open_export_dialog(self) -> None:
        from .dialogs.export_dialog import ExportDialog

        dialog = ExportDialog(self.bridge, self)
        dialog.exec()

    def _prepare_audio(self) -> None:
        """后台打开音频设备（失败就记下来，播放时再提示）。"""
        import threading

        threading.Thread(target=self.audio.prepare, name="fc-audio-prepare",
                         daemon=True).start()

    def pause(self) -> None:
        if self._playing:
            self.toggle_play()

    def _on_user_seek(self, us: int) -> None:
        """用户主动定位（拖标尺/点时间线）：剪映的行为是**先暂停**再定位。

        不这么做的话，播放中拖动会被 `_tick` 按墙上时间推回去（用户实测报过）。
        """
        self.pause()
        self.bridge.set_playhead(int(us))

    def fit_timeline(self) -> None:
        """把时间线缩放到全部片段可见（剪映：适应窗口）。"""
        self.timeline.fit_to_window()
        self.status_label.setText("时间线：已缩放到全部")

    def step_frames(self, delta: int) -> None:
        """←/→：按帧移动播放头（剪映里是逐帧预览）。"""
        fps = float(self.bridge.project.fps or 30.0)
        # set_playhead 收「微秒」；一帧 = 1e6/fps 微秒
        step = 1_000_000.0 / max(1.0, fps)
        target = max(0.0, float(self.bridge.playhead) + delta * step)
        self.bridge.set_playhead(int(round(target)))

    def toggle_play(self) -> None:
        self._playing = not self._playing
        self.player.set_playing(self._playing)
        self.viewer.set_playing(self._playing)
        if self._playing:
            # 播放头按**墙上时间**推进，不按定时器次数：定时器被拖慢时
            # 播放速度不会跟着变慢（剪映/PR 都是这个行为）
            self._play_clock.restart()
            self._play_base = int(self.bridge.playhead)
            self._play_timer.start()
            # 设备在后台准备：WASAPI 打开一条输出流要 ~1 秒，同步做会卡住界面
            if not self.audio.playing:
                import threading

                threading.Thread(target=self.audio.start,
                                 args=(self.bridge.playhead,),
                                 name="fc-audio-start", daemon=True).start()
        else:
            self._play_timer.stop()
            self.audio.stop()
            self.viewer.schedule()

    def _tick(self) -> None:
        elapsed_us = int(self._play_clock.nsecsElapsed() // 1000)
        nxt = self._play_base + elapsed_us
        if nxt >= self.bridge.duration_us:
            self.bridge.set_playhead(self.bridge.duration_us)
            self.toggle_play()
            return
        self.bridge.set_playhead(nxt)
        # 音频按同一个墙上时钟校准（每块各自推进会有几毫秒漂移）
        self.audio.seek(nxt)

    def missing_materials(self) -> list:
        """返回引用了但文件不存在的素材（去重）。"""
        missing = []
        seen = set()
        for mat in self.bridge.project.materials.values():
            path = str(getattr(mat, "path", "") or "")
            if not path or path in seen:
                continue
            seen.add(path)
            if not os.path.isfile(path):
                missing.append((mat, path))
        return missing

    def relink_material(self, mat, new_path: str) -> bool:
        """把某个素材指向新路径（失败返回 False）。"""
        if not os.path.isfile(new_path):
            return False
        try:
            mat.extra["path"] = str(new_path) if hasattr(mat, "extra") else new_path
        except Exception:                              # noqa: BLE001
            try:
                mat.path = new_path
            except Exception:                          # noqa: BLE001
                return False
        self.bridge.touch()
        self.bridge.edited.emit()
        self._refresh_all()
        return True

    def relink_materials(self) -> None:
        """让用户选一个目录，按文件名把缺失素材接回去。"""
        from PySide6.QtWidgets import QFileDialog

        missing = self.missing_materials()
        if not missing:
            QMessageBox.information(self, "重新链接", "没有缺失的素材。")
            return
        folder = QFileDialog.getExistingDirectory(self, "选择素材所在文件夹（按文件名匹配）")
        if not folder:
            return
        fixed = 0
        for mat, old_path in missing:
            candidate = os.path.join(folder, os.path.basename(old_path))
            if os.path.isfile(candidate) and self.relink_material(mat, candidate):
                fixed += 1
        self.status_label.setText(f"重新链接：{fixed}/{len(missing)} 个素材已接回")

    def _refresh_all(self) -> None:
        # 后台把解码器先打开：点播放/拖播放头时不用等容器初始化
        self.bridge.prewarm_decoders()
        # 工程里的开关（主轨磁吸）同步到工具栏
        sync = getattr(getattr(self, "timeline_toolbar", None), "sync_from_project", None)
        if callable(sync):
            sync()
        project = self.bridge.project
        self.doc_label.setText(os.path.basename(self.bridge.path) if self.bridge.path
                               else project.name or "未命名工程")
        self.meta_label = getattr(self, "meta_label", None)
        fps = float(project.fps)
        summary = (f"{project.canvas.width}×{project.canvas.height} · {fps:.0f} fps · "
                   f"{len(project.tracks)} 轨 · {format_timecode(project.duration(), fps)}")
        if self.meta_label is not None:
            self.meta_label.setText(summary)
        self.status_label.setToolTip(summary)
        ratio = project.canvas.ratio or "16:9"
        if ratio in RATIO_SIZES and self.ratio_box.currentText() != ratio:
            self.ratio_box.blockSignals(True)
            self.ratio_box.setCurrentText(ratio)
            self.ratio_box.blockSignals(False)
        self.bin.refresh_media()
        self.timeline.refresh()
        self.player.update_timecode()
        self.viewer.schedule()
        self.status_label.setText("就绪")

    # -- 截图辅助（测试与文档用）------------------------------------------ #
    def grab_to(self, path: str) -> bool:
        pix = self.grab()
        return pix.save(path, "PNG")

    def _confirm_close(self) -> bool:
        """返回 True 表示可以关；False 表示用户取消了。"""
        if not getattr(self.bridge, "dirty", False):
            return True
        answer = QMessageBox.question(
            self, "未保存的修改",
            "当前工程有未保存的修改，是否保存后再退出？",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if answer == QMessageBox.Cancel:
            return False
        if answer == QMessageBox.Save:
            try:
                if self.bridge.path:
                    self.bridge.save()
                else:
                    from PySide6.QtWidgets import QFileDialog

                    path, _ = QFileDialog.getSaveFileName(self, "保存工程", "project.json",
                                                          "工程 (*.json)")
                    if not path:
                        return False
                    self.bridge.project.save(path)
                    self.bridge.path = path
                    self.bridge.dirty = False
            except Exception as exc:                   # noqa: BLE001
                self.status_label.setText(f"保存失败：{exc}")
                return False
        return True

    def closeEvent(self, event):                     # noqa: N802
        if not self._confirm_close():
            event.ignore()
            return
        self.clear_autosave()
        """关窗口前把预览线程与解码器收掉（否则每次开窗漏一个线程）。"""
        try:
            self._play_timer.stop()
            self._autosave_timer.stop()
            self.audio.shutdown()
            self.bridge.shutdown()
        except Exception:                            # noqa: BLE001
            pass
        super().closeEvent(event)


def media_smoke(argv: Optional[list] = None) -> int:
    """拖拽自检：素材列表的 mimeData 是否带 URL、拖进时间线能否建轨建片段。

    打包版用它证明「素材拖不进」这类修复真的进了 exe，而不是只在源码里。
    """
    import os
    import sys as _sys

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    argv = list(_sys.argv if argv is None else argv)
    media = argv[1] if len(argv) > 1 else os.path.join("out", "demo.mp4")
    if not os.path.isfile(media):
        print(f"media-smoke: 找不到素材 {media}")
        return 2

    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QDropEvent

    app = QApplication.instance() or QApplication(argv[:1])
    app.setStyleSheet(stylesheet())
    window = MainWindow(None)
    window.resize(1400, 860)
    window.show()
    app.processEvents()

    ok = True
    report = []
    try:
        window.bin._add_material_only(media)
        window.bin.refresh_media()
        app.processEvents()
        item = window.bin.media_list.item(0)
        mime = window.bin.media_list.mimeData([item])
        has_url = mime.hasUrls()
        ok = ok and has_url
        report.append(f"mime {'has-url' if has_url else 'NO-URL'}:{','.join(mime.formats())}")

        window.timeline.dropEvent(QDropEvent(QPointF(300, 200), Qt.CopyAction, mime,
                                             Qt.LeftButton, Qt.NoModifier))
        app.processEvents()
        tracks = len(window.bridge.project.tracks)
        clips = sum(len(tr.segments) for tr in window.bridge.project.tracks)
        ok = ok and clips >= 1
        report.append(f"drop tracks={tracks} clips={clips}")

        window.bin.media_list.setCurrentRow(0)
        window.bin.add_selected_to_timeline()
        app.processEvents()
        clips = sum(len(tr.segments) for tr in window.bridge.project.tracks)
        ok = ok and clips >= 2
        report.append(f"double-click clips={clips}")
        # 解码器自检：打包版要能真的解出一帧（av 的 DLL 有没有进包）
        from ..media import FrameReader

        reader = FrameReader(media, width=160, height=90)
        try:
            frame = reader.frame_at(0)
            report.append(f"decoder={type(reader).__name__} frame={frame.shape[1]}x{frame.shape[0]}")
            ok = ok and frame is not None and frame.size > 0
        finally:
            reader.close()
    except Exception as exc:                           # noqa: BLE001
        ok = False
        report.append(f"error={exc}")
    finally:
        window.close()
    print("media-smoke:", "; ".join(report))
    return 0 if ok else 1


def audio_smoke(argv: Optional[list] = None) -> int:
    """无头自检预览音频：合成一段 440 Hz，走一遍混音，断言 PCM 不是静音。

    打包版用它证明「sounddevice/portaudio 与混音链路确实进了包」——不用声卡也能验。
    """
    import math
    import struct
    import sys as _sys
    import tempfile
    import wave

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from .. import builder
    from ..audio_engine import AudioEngine, NullBackend

    report = []
    tmp = tempfile.mkdtemp(prefix="freecut-audio-")
    tone = os.path.join(tmp, "tone.wav")
    with wave.open(tone, "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(48000)
        frames = b"".join(struct.pack("<h", int(12000 * math.sin(2 * math.pi * 440 * i / 48000)))
                          for i in range(48000))
        fh.writeframes(frames)

    project = builder.new_project(640, 360, 30, name="音频自检")
    builder.add_audio(project, tone, start=0, duration=1_000_000)
    from .bridge import Bridge

    bridge = Bridge()
    bridge.set_project(project, None)
    backend = NullBackend()
    engine = AudioEngine(bridge, backend=backend)
    ok = True
    try:
        if not engine.start(0):
            report.append(f"start failed: {engine.last_error}")
            ok = False
        else:
            for _ in range(8):
                backend.pump(1024)
            peak = max((int(abs(int(s))) for block in backend.blocks
                        for s in block.reshape(-1)[:64]), default=0)
            report.append(f"blocks={len(backend.blocks)}")
            report.append(f"peak={peak}")
            if peak < 100:
                report.append("静音（混音链路没出声）")
                ok = False
            from ..audio_engine import SoundDeviceBackend

            available = SoundDeviceBackend().default_samplerate() > 0
            report.append(f"device_sr={available}")
    finally:
        engine.stop()
    print("audio-smoke:", "; ".join(report))
    return 0 if ok else 1


def smoke(argv: Optional[list] = None) -> int:
    """无头自检：建窗口、切一遍类别、渲染一帧，然后退出。

    打包版用它证明「Qt 运行时 + 平台插件确实在包里」——比只看 exe 存在可靠。
    """
    import os
    import sys as _sys

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    argv = list(_sys.argv if argv is None else argv)
    app = QApplication.instance() or QApplication(argv[:1])
    app.setStyleSheet(stylesheet())
    window = MainWindow(None)
    window.resize(1400, 860)
    window.show()                 # 不 show 的话分隔条只分到最小尺寸
    app.processEvents()
    app.processEvents()
    ok = True
    report = []
    try:
        report.append(f"build {window.build_tag}")
        report.append(f"window {window.width()}x{window.height()}")
        report.append(f"rail {len(window.rail.buttons)}")
        for name in theme.QT_CATEGORIES:
            window.bin.show_category(name)
        app.processEvents()
        image = window.bridge.frame_image(160, 90)
        ok = image is not None and image.width() == 160
        report.append(f"frame {'ok' if ok else 'fail'}")
        report.append(f"panes {window.splitter.sizes()}")
    finally:
        window.close()
    print("gui-smoke:", "; ".join(report))
    return 0 if ok else 1


def main(argv: Optional[list] = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    project = None
    if len(argv) > 1 and argv[1].lower().endswith(".json"):
        project = argv[1]
    app = QApplication.instance() or QApplication(argv)
    # 装到 app 上而不是主窗口：对话框是独立顶层窗口，装在窗口上它拿不到样式，
    # 中文会退化成方框（第 10 轮实测）。
    app.setStyleSheet(stylesheet())
    window = MainWindow(project)
    window.show()
    return app.exec()



