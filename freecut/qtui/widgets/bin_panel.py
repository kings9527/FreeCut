"""左栏：类别栏 + 内容页。

类别与顺序来自 ``freecut.theme.JY_CATEGORIES``（我们做到的 8 个（云端-only 的已删除））。
做不到的类别**灰显**并写清原因（``theme.CLOUD_ONLY``），不隐藏——用户要看到
剪映有什么、我们缺什么（HANDOFF §5.2 / §10.2）。
"""

from __future__ import annotations

import os

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QDoubleSpinBox,
                               QHBoxLayout, QLabel, QListView, QListWidget,
                               QListWidgetItem, QPushButton, QStackedWidget,
                               QToolButton, QVBoxLayout, QWidget)

from ... import theme

#: 条目的**原始文字**（不含「· 已添加」角标）存在这个角色里，刷新时可以还原
BASE_TEXT_ROLE = Qt.UserRole + 1
#: 素材已经在时间线上时追加的角标（剪映同款写法）
ADDED_SUFFIX = " · 已添加"


class Rail(QWidget):
    """竖排类别按钮，选中用青色底；需云端的类别灰显并带 ☁ 标记。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Rail")
        self.setFixedWidth(theme.px(theme.RAIL_W))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 6, 4, 6)
        layout.setSpacing(2)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons = {}
        self.unavailable = dict(theme.CLOUD_ONLY)
        from ..icons import qicon

        for index, name in enumerate(theme.QT_CATEGORIES):
            btn = QToolButton(self)
            btn.setIcon(qicon(name, 20))
            btn.setIconSize(QSize(20, 20))
            cloud = name in theme.CLOUD_ONLY
            btn.setText(f"{name}☁" if cloud else name)
            btn.setToolTip(f"{name} —— {theme.CLOUD_ONLY[name]}" if cloud else name)
            btn.setCheckable(True)
            btn.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            btn.setFixedHeight(50)
            if cloud:
                # 灰显但**保留在栏里**：剪映有这一项，我们做不了就说明原因
                btn.setEnabled(False)
            layout.addWidget(btn)
            self.group.addButton(btn, index)
            self.buttons[name] = btn
        layout.addStretch(1)


class MediaList(QListWidget):
    """素材列表。默认拖拽只带 ``x-qabstractitemmodeldatalist``，时间线认不出路径；
    这里改写 mimeData()，把文件路径塞成 URL（和从资源管理器拖进来同一种格式）。
    """

    def mimeData(self, items):                          # noqa: N802
        from PySide6.QtCore import QMimeData, QUrl

        data = QMimeData()
        urls = []
        for item in items:
            path = item.data(Qt.UserRole)
            if path and os.path.isfile(str(path)):
                urls.append(QUrl.fromLocalFile(str(path)))
        if urls:
            data.setUrls(urls)
            data.setText(urls[0].toLocalFile())
        return data


class BinPanel(QWidget):
    """右半：面板标题 + 叠放的类别页。"""

    def __init__(self, bridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.favorites = set()          # 素材星标（本次会话内有效）
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QWidget(self)
        header.setObjectName("PanelHeader")
        hbox = QHBoxLayout(header)
        hbox.setContentsMargins(0, 0, 8, 0)
        self.title = QLabel("素材", header)
        hbox.addWidget(self.title, 1)
        layout.addWidget(header)

        self.stack = QStackedWidget(self)
        layout.addWidget(self.stack, 1)
        self.pages = {}
        self._page_scrolls = {}
        self._icons_built = set()
        for name in theme.QT_CATEGORIES:
            page = self._build_page(name)
            self.pages[name] = page
            # 每页套一个滚动区：窗口矮的时候，"加在片段开头"这类底部按钮
            # 会被挤出可见区域（用户实测截图里就是被切掉的）
            self._wrap_scroll(name, page)
        # 素材列表的缩略图由工作线程解，解好一张就补一个图标
        try:
            from ..media_cache import MediaCache

            MediaCache.shared().ready.connect(self._on_media_thumb_ready)
        except Exception:                              # noqa: BLE001
            pass
        # 选中变了 / 加过特效转场之后，列表里的「✓ 已用」要跟着变
        self.bridge.selectionChanged.connect(lambda _seg: self.refresh_applied_state())
        self.bridge.edited.connect(self.refresh_applied_state)
        # 时间线可能变了：「· 已添加」角标跟着变。只逐条改文字，不重建列表
        # （重建会丢选中、重解缩略图；拖动/撤销时 edited 发得很频繁）
        self.bridge.edited.connect(self.refresh_added_state)
        # 换工程 / 新建工程：素材库整体重建
        self.bridge.projectReloaded.connect(self.refresh_media)

    # -- 页面 ------------------------------------------------------------- #
    def _wrap_scroll(self, name: str, page: QWidget) -> None:
        """把一页套进 QScrollArea 再放进 stack（底部的按钮才不会被切掉）。"""
        from PySide6.QtWidgets import QScrollArea

        scroll = QScrollArea(self)
        scroll.setObjectName("BinScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        page.setMinimumSize(1, 1)
        scroll.setWidget(page)
        self._page_scrolls[name] = scroll
        self.stack.addWidget(scroll)

    def _build_page(self, name: str) -> QWidget:
        page = QWidget(self)
        box = QVBoxLayout(page)
        box.setContentsMargins(8, 8, 8, 8)
        box.setSpacing(6)

        if name in ("素材", "媒体"):
            from PySide6.QtWidgets import QLineEdit

            tools = QHBoxLayout()
            self.search_edit = QLineEdit(page)
            self.search_edit.setPlaceholderText("搜索素材…")
            self.search_edit.textChanged.connect(lambda _t: self.refresh_media())
            tools.addWidget(self.search_edit, 1)
            self.kind_box = QComboBox(page)
            for key in ("全部", "视频", "图片", "音频"):
                self.kind_box.addItem(key)
            self.kind_box.currentIndexChanged.connect(lambda _i: self.refresh_media())
            tools.addWidget(self.kind_box)
            # 列表 / 网格切换：剪映素材页右上角那颗小按钮
            self.view_btn = QToolButton(page)
            self.view_btn.setText("▦")
            self.view_btn.setToolTip("网格视图")
            self.view_btn.setAutoRaise(True)
            self.view_btn.clicked.connect(
                lambda _checked=False: self.toggle_media_view())
            tools.addWidget(self.view_btn)
            box.addLayout(tools)

            # 「已添加」筛选：和上面的搜索、类型筛选叠加生效（只隐藏行，不重建）
            self.added_box = QComboBox(page)
            for key in ("全部", "仅显示未添加", "仅显示已添加"):
                self.added_box.addItem(key)
            self.added_box.setToolTip("按素材是否已加到时间线筛选")
            self.added_box.currentIndexChanged.connect(
                lambda _i: self.apply_media_filters())
            box.addWidget(self.added_box)

            fav_row = QHBoxLayout()
            from PySide6.QtWidgets import QCheckBox

            self.only_fav_box = QCheckBox("只看收藏", page)
            self.only_fav_box.toggled.connect(lambda _v: self.refresh_media())
            fav_row.addWidget(self.only_fav_box)
            self.fav_btn = QPushButton("收藏 / 取消", page)
            self.fav_btn.setToolTip("把选中的素材加星标（只在本次会话内有效）")
            self.fav_btn.clicked.connect(self.toggle_favorite)
            fav_row.addWidget(self.fav_btn)
            box.addLayout(fav_row)

            self.media_list = MediaList(page)
            self.media_list.setContextMenuPolicy(Qt.CustomContextMenu)
            self.media_list.customContextMenuRequested.connect(self._media_menu)
            self.media_list.setIconSize(QSize(48, 27))
            self.media_list.setDragEnabled(True)
            self.media_list.setDragDropMode(QListWidget.DragOnly)
            self.media_list.itemDoubleClicked.connect(self.add_selected_to_timeline)
            box.addWidget(self.media_list, 1)

            row = QHBoxLayout()
            import_btn = QPushButton("导入媒体", page)
            import_btn.clicked.connect(self.import_media)
            row.addWidget(import_btn)
            add_btn = QPushButton("添加到时间线", page)
            add_btn.clicked.connect(self.add_selected_to_timeline)
            row.addWidget(add_btn)
            box.addLayout(row)

        elif name == "音频":
            self.audio_list = QListWidget(page)
            box.addWidget(self.audio_list, 1)
            box.addWidget(QLabel("音量 / 淡入淡出 / 变声在右栏「声音」页签", page))

        elif name == "文本":
            from ... import textstyles

            tip = QLabel("选一个花字样式，再点下面的按钮（或双击样式）插入到播放头", page)
            tip.setWordWrap(True)
            box.addWidget(tip)
            self.text_list = QListWidget(page)
            for preset in textstyles.names():
                item = QListWidgetItem(preset)
                item.setData(Qt.UserRole, preset)
                self.text_list.addItem(item)
            self.text_list.itemDoubleClicked.connect(
                lambda _item: self._insert_text_preset())
            box.addWidget(self.text_list, 1)
            self.text_insert_btn = QPushButton("插入到播放头", page)
            self.text_insert_btn.clicked.connect(self._insert_text_preset)
            box.addWidget(self.text_insert_btn)

        elif name == "贴纸":
            label = QLabel("内置贴纸：0 个\n\n剪映的贴纸是云端素材包，FreeCut 不自带也不盗链。\n"
                           "需要装饰时用「文本」页的花字或「特效」页的光效代替。", page)
            label.setWordWrap(True)
            label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
            box.addWidget(label)
            box.addStretch(1)

        elif name == "特效":
            from ... import effects

            from PySide6.QtGui import QIcon

            from ..library import EFFECT_GROUPS, make_grid
            from ..thumbs import effect_pixmap

            self.effect_group = QComboBox(page)
            self.effect_group.addItem("全部")
            for group in EFFECT_GROUPS:
                self.effect_group.addItem(group)
            box.addWidget(self.effect_group)
            self._effect_kinds = list(effects.available())
            self.effect_list = make_grid(page, group_filter=False,
                                         icon_size=QSize(80, 45),
                                         library_kind="effect")
            for kind in self._effect_kinds:
                item = QListWidgetItem(effects.label(kind))
                item.setData(Qt.UserRole, kind)
                icon = self._effect_icon(kind)
                if icon is not None:
                    item.setIcon(icon)
                item.setToolTip(f"{effects.label(kind)}（本地程序化实现，非剪映同名特效本体）")
                self.effect_list.addItem(item)
            self.effect_list.itemDoubleClicked.connect(
                lambda _item: self.apply_selected_effect())
            self.effect_group.currentTextChanged.connect(self._filter_effects)
            box.addWidget(self.effect_list, 1)
            # "当前片段用了什么"——用户要能判断是否已经生效
            self.effect_applied = QLabel("当前片段：未使用特效", page)
            self.effect_applied.setObjectName("AppliedHint")
            self.effect_applied.setWordWrap(True)
            box.addWidget(self.effect_applied)
            apply_btn = QPushButton("加到选中片段", page)
            apply_btn.clicked.connect(self.apply_selected_effect)
            box.addWidget(apply_btn)
            self.effect_apply_btn = apply_btn
            hint = QLabel("全部为本地程序化实现；与剪映同名特效只是观感接近。", page)
            hint.setWordWrap(True)
            box.addWidget(hint)

        elif name == "转场":
            from ...render import TRANSITION_KINDS

            from PySide6.QtGui import QIcon

            from ..labels import ENGINE_LABELS
            from ..library import TRANSITION_GROUPS, make_grid
            from ..thumbs import transition_pixmap

            # 二级分类（HANDOFF §5.3：热门/基础/运镜/幻灯片/光效/扭曲…）
            self.transition_group = QComboBox(page)
            self.transition_group.addItem("全部")
            for group in TRANSITION_GROUPS:
                self.transition_group.addItem(group)
            box.addWidget(self.transition_group)

            row = QHBoxLayout()
            row.addWidget(QLabel("时长", page))
            self.transition_duration = QDoubleSpinBox(page)
            self.transition_duration.setRange(0.1, 5.0)
            self.transition_duration.setSingleStep(0.1)
            self.transition_duration.setValue(0.5)
            self.transition_duration.setSuffix(" 秒")
            self.transition_duration.setToolTip("加转场时用这个时长（剪映也能先设时长再加）")
            row.addWidget(self.transition_duration)
            row.addStretch(1)
            box.addLayout(row)

            engines = [k for k in ENGINE_LABELS if k in set(TRANSITION_KINDS.values())]
            self._transition_engines = engines
            self.transition_list = make_grid(page, group_filter=False,
                                             icon_size=QSize(80, 45),
                                             library_kind="transition")
            for kind in engines:
                item = QListWidgetItem(ENGINE_LABELS[kind])
                item.setData(Qt.UserRole, kind)
                item.setToolTip(f"{ENGINE_LABELS[kind]}（本地近似；剪映同名转场本体在云端）")
                icon = self._transition_icon(kind)
                if icon is not None:
                    item.setIcon(icon)
                self.transition_list.addItem(item)
            self.transition_list.itemDoubleClicked.connect(
                lambda _item: self.apply_selected_transition())
            self.transition_group.currentTextChanged.connect(self._filter_transitions)
            box.addWidget(self.transition_list, 1)
            self.transition_applied = QLabel("当前片段：开头没有转场", page)
            self.transition_applied.setObjectName("AppliedHint")
            self.transition_applied.setWordWrap(True)
            box.addWidget(self.transition_applied)
            apply_btn = QPushButton("加在片段开头", page)
            apply_btn.clicked.connect(self.apply_selected_transition)
            box.addWidget(apply_btn)
            all_btn = QPushButton("应用到全部接缝", page)
            all_btn.setToolTip("把选中的转场与时长套到所有「前一段紧挨后一段」的接缝上")
            all_btn.clicked.connect(self.apply_transition_to_all)
            box.addWidget(all_btn)
            self.transition_apply_btn = apply_btn
            hint = QLabel("19 种本地引擎；453 个剪映转场名只能作标签导出。", page)
            hint.setWordWrap(True)
            box.addWidget(hint)

        elif name == "滤镜":
            from ... import builder, luts

            self.lut_list = QListWidget(page)
            self.lut_entries = []
            try:
                self.lut_entries = list(luts.library())
            except Exception:                          # noqa: BLE001
                self.lut_entries = []
            for entry in self.lut_entries:
                item = QListWidgetItem(getattr(entry, "name", os.path.basename(entry.path)))
                item.setData(Qt.UserRole, entry.path)
                icon = self._lut_icon(entry.path)
                if icon is not None:
                    item.setIcon(icon)
                self.lut_list.addItem(item)
            box.addWidget(self.lut_list, 1)
            apply_lut = QPushButton("套用到选中片段", page)
            apply_lut.clicked.connect(self.apply_selected_lut)
            box.addWidget(apply_lut)
            imp = QPushButton("导入 LUT（.cube）…", page)
            imp.setToolTip("把外部的 .cube 滤镜复制到本地库（%LOCALAPPDATA%\\FreeCut\\luts）")
            imp.clicked.connect(self.import_lut)
            box.addWidget(imp)
            gen = QPushButton("生成并套用 20 支自研 LUT", page)
            gen.setToolTip("本地生成的 .cube 预设（电影感/日系/赛博…），不联网")
            gen.clicked.connect(self.generate_luts)
            box.addWidget(gen)
            hint = QLabel("剪映的 1052 支滤镜本体在云端，拿不到；这里用自研 .cube 做等效影调。", page)
            hint.setWordWrap(True)
            box.addWidget(hint)

        elif name == "调节":
            from ... import builder

            add_btn = QPushButton("在播放头新建调节图层（2 秒）", page)
            add_btn.clicked.connect(self.add_adjustment_layer)
            box.addWidget(add_btn)
            box.addWidget(QLabel("建好后在右栏「调节」页拖十个滑杆；\n"
                                 "它只影响下方的画面，不影响文字。", page))
            box.addStretch(1)

        elif name in theme.CLOUD_ONLY:
            label = QLabel(f"{name}：需云端\n\n{theme.CLOUD_ONLY[name]}\n\n"
                           "FreeCut 不绕过会员、不盗链 CDN，所以这一项灰着。\n"
                           "本地能做的是「文本 / 特效 / 转场 / 滤镜 / 调节」五类。", page)
            label.setWordWrap(True)
            label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
            box.addWidget(label)
            box.addStretch(1)

        elif name == "字幕":
            from PySide6.QtWidgets import QListWidget as _LW

            import_btn = QPushButton("导入字幕 (SRT/VTT)", page)
            import_btn.clicked.connect(self.import_subtitles)
            box.addWidget(import_btn)

            export_btn = QPushButton("导出字幕 (SRT)", page)
            export_btn.clicked.connect(self.export_subtitles)
            box.addWidget(export_btn)

            self.asr_btn = QPushButton("自动字幕（离线语音识别）", page)
            self.asr_btn.clicked.connect(self.auto_subtitles)
            box.addWidget(self.asr_btn)

            self.sub_list = _LW(page)
            box.addWidget(self.sub_list, 1)
            refresh = QPushButton("刷新字幕列表", page)
            refresh.clicked.connect(self.refresh_subtitles)
            box.addWidget(refresh)

            hint = QLabel("识别用本地 Whisper，不上传音频。", page)
            hint.setWordWrap(True)
            box.addWidget(hint)

        return page

    # -- 生效动作 --------------------------------------------------------- #
    def _selected(self, title: str):
        seg = getattr(self.bridge, "selected", None)
        if seg is None:
            self.bridge.statusMessage.emit(f"先在时间线上点选一个片段，再加{title}")
            return None
        return seg

    def _insert_text_preset(self) -> None:
        """按列表里选中的花字插入文本（没选就插默认样式）。"""
        style = ""
        if hasattr(self, "text_list") and self.text_list.currentItem() is not None:
            style = str(self.text_list.currentItem().data(Qt.UserRole) or "")
        self._insert_text(style)

    def _insert_text(self, style: str = "", content: str = "双击右侧文字页改内容") -> None:
        """在播放头插一段 3 秒文本，可套花字样式（可撤销）。"""
        from ... import builder, textstyles

        bridge = self.bridge
        with bridge.render_lock:
            seg = builder.add_text(bridge.project, content,
                                   start=int(bridge.playhead), duration=3_000_000)
            if style:
                mat = bridge.project.material(seg.material_id)
                if mat is not None:
                    textstyles.apply(mat, style)
            # 这段文本可能刚建了一条新的文本轨；快照必须包含这条轨道，
            # 所以先建片段再抓快照，然后把这一段从快照里剔掉。
            before = bridge._snapshot(bridge.project.tracks)
            for _track, segs in before:
                segs[:] = [s for s in segs if getattr(s, "id", None) != seg.id]
            bridge.select(seg)
            bridge.touch()
        bridge._record_structure(before, "新建文本")
        bridge.projectReloaded.emit()
        bridge.statusMessage.emit(
            f"已新建文本：{style or '默认样式'}（3 秒，双击文字页改内容）")

    def apply_selected_effect(self) -> None:
        """按钮/双击：把网格里选中的特效加到当前选中片段。"""
        item = self.effect_list.currentItem() if hasattr(self, "effect_list") else None
        if item is None:
            self.bridge.statusMessage.emit("先在网格里选一种特效")
            return
        seg = self._selected("特效")
        if seg is None:
            return
        self._apply_effect(seg, str(item.data(Qt.UserRole) or ""))

    def apply_effect_to(self, seg, kind: str) -> None:
        """拖放：把指定特效套到**落点**那个片段（不是选中片段）。"""
        self._apply_effect(seg, kind)

    def _apply_effect(self, seg, kind: str) -> None:
        from ... import builder, effects

        if not kind:
            return
        with self.bridge.render_lock:
            before = len(seg.extra_material_refs)
            builder.add_effect(self.bridge.project, seg, kind)
            self.bridge.record_materials(seg, before, "加特效")
        self.bridge.select(seg)
        self.bridge.statusMessage.emit(f"已加特效：{effects.label(kind)}")
        self.refresh_applied_state()
        self.bridge.edited.emit()

    def apply_selected_transition(self) -> None:
        """给"后一个片段"的开头加转场。

        - **换**转场是替换，不是叠加（旧转场材质要被摘掉，否则渲染还是旧的那个）
        - 加完把播放头**跳到转场中间**，用户立刻能在预览里看到
        - 左边没有片段时，除闪黑/闪白外都看不出效果——如实提示，不假装生效
        """
        from ... import builder

        from ..labels import ENGINE_LABELS

        item = (self.transition_list.currentItem()
                if hasattr(self, "transition_list") else None)
        if item is None:
            self.bridge.statusMessage.emit("先在网格里选一种转场")
            return
        seg = self._selected("转场")
        if seg is None:
            self.bridge.statusMessage.emit(
                "先在时间线上点选**后面那个**片段（转场加在它的开头）")
            return
        kind = str(item.data(Qt.UserRole) or "")
        duration_us = int(round(
            (self.transition_duration.value() if hasattr(self, "transition_duration") else 0.5)
            * 1_000_000))
        self._apply_transition(seg, str(item.data(Qt.UserRole) or ""), duration_us)

    def apply_transition_to_all(self) -> None:
        """给所有相邻接缝加同一个转场（剪映的「应用全部」）。"""
        item = (self.transition_list.currentItem()
                if hasattr(self, "transition_list") else None)
        if item is None:
            self.bridge.statusMessage.emit("先在网格里选一种转场")
            return
        kind = str(item.data(Qt.UserRole) or "")
        duration_us = int(round(
            (self.transition_duration.value() if hasattr(self, "transition_duration") else 0.5)
            * 1_000_000))
        junctions = self._all_junctions()
        if not junctions:
            self.bridge.statusMessage.emit("时间线上没有可加转场的接缝（需要两段紧挨）")
            return
        from ..labels import ENGINE_LABELS

        for seg in junctions:
            self._apply_transition(seg, kind, duration_us, seek=False)
        self.bridge.statusMessage.emit(
            f"已给 {len(junctions)} 个接缝加上「{ENGINE_LABELS.get(kind, kind)}」")
        self.bridge.edited.emit()

    def _all_junctions(self) -> list:
        """所有"前一段紧挨本段"的片段（转场加在这些片段的开头）。"""
        found = []
        for track in self.bridge.project.tracks:
            segs = track.sorted_segments() if hasattr(track, "sorted_segments") else list(track.segments)
            for seg in segs:
                prev = None
                start = int(seg.target_timerange.start)
                for other in segs:
                    if other is seg:
                        continue
                    end = int(other.target_timerange.start + other.target_timerange.duration)
                    if end <= start + 1 and start - end <= 2_000_000:
                        if prev is None or end > int(prev.target_timerange.start + prev.target_timerange.duration):
                            prev = other
                if prev is not None:
                    found.append(seg)
        return found

    def apply_transition_to(self, seg, kind: str) -> None:
        """拖放：把指定转场加到**落点**那个片段的开头。"""
        duration_us = int(round(
            (self.transition_duration.value() if hasattr(self, "transition_duration") else 0.5)
            * 1_000_000))
        self._apply_transition(seg, kind, duration_us)

    def _apply_transition(self, seg, kind: str, duration_us: int, seek: bool = True) -> None:
        from ... import builder

        from ..labels import ENGINE_LABELS

        if not kind:
            return
        name = ENGINE_LABELS.get(kind, kind)
        with self.bridge.render_lock:
            before_refs = list(seg.extra_material_refs)
            had_transition = any(
                getattr(self.bridge.project.material(rid), "type", "") == "transition"
                for rid in before_refs)
            # 替换：把旧转场的 **ref** 摘掉。材质本身留着——撤销要把它接回去，
            # 而且草稿导出只写被引用的材质，留几个孤儿不会泄漏到工程里。
            for rid in list(seg.extra_material_refs):
                mat = self.bridge.project.material(rid)
                if mat is not None and getattr(mat, "type", "") == "transition":
                    seg.extra_material_refs.remove(rid)
            builder.add_transition(self.bridge.project, seg, kind=kind,
                                   duration_us=duration_us, name=name)
            self.bridge.record_refs(seg, before_refs,
                                    "换转场" if had_transition else "加转场")
        self.bridge.select(seg)
        self.refresh_applied_state()
        self.bridge.edited.emit()

        # 播放头跳到接缝：转场以切点为中心，切点就是效果最强的那一刻
        if seek:
            start = int(seg.target_timerange.start)
            self.bridge.set_playhead(start)
            self.bridge.statusMessage.emit(
                f"已加转场：{name}（{duration_us / 1e6:.1f}s）— 播放头已停在接缝，预览可见")
        if self._previous_segment(seg) is None and kind not in ("fade_black", "fade_white"):
            self.bridge.statusMessage.emit(
                f"注意：{name} 要左边紧挨着另一个片段才看得出效果；"
                "这个片段是轨道开头，可以先用「闪黑/闪白」，或把它挪到后面")

    def _previous_segment(self, seg):
        """同一条轨道上，紧挨在 ``seg`` 左边的那个片段（没有就 None）。"""
        track = self.bridge.track_of_segment(seg)
        if track is None:
            return None
        start = int(seg.target_timerange.start)
        best = None
        for other in track.sorted_segments():
            if other is seg:
                continue
            end = int(other.target_timerange.start + other.target_timerange.duration)
            # 和 renderer._find_previous 一致：只认 2 秒以内的前一段，
            # 否则界面说"会显示"但渲染其实找不到前一段
            if start - end > 2_000_000:
                continue
            if end <= start + 1 and (best is None or
                                     int(other.target_timerange.start)
                                     > int(best.target_timerange.start)):
                best = other
        return best

    def _filter_effects(self, group: str) -> None:
        from ... import effects

        from ..library import kinds_in, EFFECT_GROUPS

        wanted = set(kinds_in(group, EFFECT_GROUPS))
        for row in range(self.effect_list.count()):
            item = self.effect_list.item(row)
            kind = str(item.data(Qt.UserRole) or "")
            item.setHidden(bool(wanted) and kind not in wanted)

    # -- 已应用状态 -------------------------------------------------------- #
    def _effect_kind_on(self, seg) -> str:
        if seg is None:
            return ""
        try:
            refs = self.bridge.project.refs_of(seg)
        except Exception:                              # noqa: BLE001
            return ""
        for ref in refs:
            if getattr(ref, "type", "") == "effect":
                return str(getattr(ref, "kind", "") or ref.get("kind") or "")
        return ""

    def _transition_kind_on(self, seg) -> str:
        if seg is None:
            return ""
        try:
            refs = self.bridge.project.refs_of(seg)
        except Exception:                              # noqa: BLE001
            return ""
        for ref in refs:
            if getattr(ref, "type", "") == "transition":
                return str(getattr(ref, "kind", "") or ref.get("kind") or "")
        return ""

    def refresh_applied_state(self) -> None:
        """在列表里勾出"已经用在当前片段上的"那一项，并写一行文字说明。"""
        from ..library import mark_applied

        seg = getattr(self.bridge, "selected", None)
        if hasattr(self, "effect_list"):
            current = self._effect_kind_on(seg)
            for row in range(self.effect_list.count()):
                item = self.effect_list.item(row)
                mark_applied(item, bool(current) and str(item.data(Qt.UserRole)) == current)
            from ... import effects

            self.effect_applied.setText(
                f"当前片段：{effects.label(current)}" if current else "当前片段：未使用特效")
        if hasattr(self, "transition_list"):
            current = self._transition_kind_on(seg)
            from ..labels import ENGINE_LABELS

            for row in range(self.transition_list.count()):
                item = self.transition_list.item(row)
                mark_applied(item, bool(current) and str(item.data(Qt.UserRole)) == current)
            self.transition_applied.setText(self._transition_hint(seg, current))

    def _transition_hint(self, seg, kind: str) -> str:
        """一行说清：有没有转场、左侧有没有紧挨的片段（否则转场看不出来）。"""
        from ..labels import ENGINE_LABELS

        neighbour = self._previous_segment(seg) if seg is not None else None
        has_prev = neighbour is not None
        if not kind:
            if seg is None:
                return "当前片段：未选中片段"
            return ("当前片段：开头没有转场（左侧有相邻片段，加转场会显示）"
                    if has_prev else
                    "当前片段：开头没有转场（左侧没有相邻片段，加转场也不显示）")
        name = ENGINE_LABELS.get(kind, kind)
        if has_prev or kind in ("fade_black", "fade_white"):
            return f"当前片段：开头有「{name}」，会显示"
        return (f"当前片段：开头有「{name}」，但左侧没有紧挨的片段 → 不显示；"
                "把片段挪到别的片段后面即可")

    def _filter_transitions(self, group: str) -> None:
        from ..library import TRANSITION_GROUPS, kinds_in

        wanted = set(kinds_in(group, TRANSITION_GROUPS))
        for row in range(self.transition_list.count()):
            item = self.transition_list.item(row)
            kind = str(item.data(Qt.UserRole) or "")
            item.setHidden(bool(wanted) and kind not in wanted)

    # -- 滤镜 / 调节 -------------------------------------------------------- #
    def apply_selected_lut(self) -> None:
        from ... import builder

        item = self.lut_list.currentItem() if hasattr(self, "lut_list") else None
        if item is None:
            self.bridge.statusMessage.emit("先在列表里选一支滤镜")
            return
        seg = self._selected("滤镜")
        if seg is None:
            return
        path = str(item.data(Qt.UserRole) or "")
        if not path or not os.path.isfile(path):
            self.bridge.statusMessage.emit("这支 LUT 文件不在（先点「生成并套用」）")
            return
        before = len(seg.extra_material_refs)
        builder.add_filter(self.bridge.project, seg, path)
        self.bridge.record_materials(seg, before, "加滤镜")
        self.bridge.statusMessage.emit(f"已套用滤镜：{item.text()}")
        self.bridge.edited.emit()

    def import_lut(self) -> None:
        """选一个 .cube 导入本地库，然后刷新列表。"""
        from PySide6.QtWidgets import QFileDialog

        from ... import luts

        path, _ = QFileDialog.getOpenFileName(self, "导入 LUT", "",
                                              "LUT (*.cube);;所有文件 (*)")
        if not path:
            return
        try:
            dest = luts.import_cube(path)
        except Exception as exc:                       # noqa: BLE001
            self.bridge.statusMessage.emit(f"导入 LUT 失败：{exc}")
            return
        self.refresh_luts()
        self.bridge.statusMessage.emit(f"已导入 LUT：{os.path.basename(dest)}")

    def refresh_luts(self) -> None:
        from ... import luts

        if not hasattr(self, "lut_list"):
            return
        self.lut_list.clear()
        try:
            self.lut_entries = list(luts.library(refresh=True))
        except Exception:                              # noqa: BLE001
            self.lut_entries = []
        for entry in self.lut_entries:
            item = QListWidgetItem(getattr(entry, "name", os.path.basename(entry.path)))
            item.setData(Qt.UserRole, entry.path)
            icon = self._lut_icon(entry.path)
            if icon is not None:
                item.setIcon(icon)
            self.lut_list.addItem(item)

    def _lut_icon(self, lut_path: str, width: int = 80, height: int = 45):
        """用一张渐变小图套上 LUT，得到能看出色调差异的缩略图。"""
        try:
            import numpy as np
            from PySide6.QtGui import QIcon, QImage, QPixmap
            from ... import color, luts

            yy = np.linspace(0.0, 1.0, height, dtype="float32")[:, None]
            xx = np.linspace(0.0, 1.0, width, dtype="float32")[None, :]
            img = np.zeros((height, width, 3), dtype="float32")
            img[..., 0] = 0.25 + 0.7 * xx
            img[..., 1] = 0.20 + 0.65 * yy
            img[..., 2] = 0.45 - 0.25 * (xx * yy)
            table = color.load_cube_lut(str(lut_path))
            applied = color.apply_lut3d(img, table)
            data = np.clip(np.asarray(applied) * 255.0, 0, 255).astype("uint8")
            image = QImage(data.data, width, height, width * 3, QImage.Format_RGB888).copy()
            return QIcon(QPixmap.fromImage(image))
        except Exception:                              # noqa: BLE001
            return None

    def generate_luts(self) -> None:
        """生成 20 支自研 .cube（本地算，不联网），然后刷新列表。"""
        from ... import lutgen

        try:
            made = lutgen.write_all(lutgen.default_dir())
        except Exception as exc:                       # noqa: BLE001
            self.bridge.statusMessage.emit(f"生成 LUT 失败: {exc}")
            return
        self.refresh_luts()
        self.bridge.statusMessage.emit(f"已生成 {len(made)} 支自研 LUT")

    def add_adjustment_layer(self) -> None:
        from ... import builder

        start = int(self.bridge.playhead)
        with self.bridge.render_lock:
            seg = builder.add_adjustment(self.bridge.project, start, 2_000_000)
            self.bridge.select(seg)
            self.bridge.touch()
        self.bridge.projectReloaded.emit()
        self.bridge.statusMessage.emit("已新建调节图层（右栏「调节」页拖滑杆）")

    # -- 素材区动作 ------------------------------------------------------- #
    KIND_LABEL = {"video": "视频", "audio": "音频", "image": "图片", "text": "文字"}

    def _iter_materials(self):
        project = self.bridge.project
        materials = (project.materials.values()
                     if isinstance(project.materials, dict) else project.materials)
        return materials

    def refresh_media(self) -> None:
        if not hasattr(self, "media_list"):
            return
        import os

        needle = self.search_edit.text().strip().lower() if hasattr(self, "search_edit") else ""
        wanted = self.kind_box.currentText() if hasattr(self, "kind_box") else "全部"
        used = self._timeline_media_paths()
        self.media_list.clear()
        self._media_items = []
        for material in self._iter_materials():
            kind = str(getattr(material, "type", ""))
            path = getattr(material, "path", None) or getattr(material, "file_path", None)
            if not path:
                continue
            name = os.path.basename(str(path))
            label = self.KIND_LABEL.get(kind, kind)
            if wanted != "全部" and label != wanted:
                continue
            if needle and needle not in name.lower():
                continue
            if self.only_fav_box.isChecked() and not self.is_favorite(material):
                continue
            from PySide6.QtWidgets import QListWidgetItem

            star = "★ " if self.is_favorite(material) else ""
            base = f"{star}[{label}] {name}"
            item = QListWidgetItem(base)
            item.setData(Qt.UserRole, str(path))
            # 原始文字单独存一份：加/去角标只要改 text()，不会越叠越长
            item.setData(BASE_TEXT_ROLE, base)
            if self._path_key(str(path)) in used:
                item.setText(f"{base}{ADDED_SUFFIX}")
            thumb = self._thumbnail_for(str(path), kind)
            if thumb is not None:
                item.setIcon(thumb)
            self.media_list.addItem(item)
            self._media_items.append((str(path), kind, material))
        if self.media_list.count() == 0:
            self.media_list.addItem("（没有匹配的素材）")
        # 搜索/类型已经在上面的循环里筛掉了，这里只按「已添加」隐藏行
        self.apply_media_filters(used)

    def _effect_icon(self, kind: str, width: int = 80, height: int = 45):
        """用一张测试图跑该特效，得到能看出差异的缩略图。"""
        try:
            import numpy as np
            from PySide6.QtGui import QIcon, QImage, QPixmap
            from ... import effects

            yy = np.linspace(0.0, 1.0, height, dtype="float32")[:, None]
            xx = np.linspace(0.0, 1.0, width, dtype="float32")[None, :]
            rgb = np.zeros((height, width, 3), dtype="float32")
            rgb[..., 0] = 0.9 - 0.6 * xx
            rgb[..., 1] = 0.35 + 0.5 * yy
            rgb[..., 2] = 0.25 + 0.45 * (1.0 - yy)
            alpha = np.ones((height, width), dtype="float32")
            out, _a = effects.apply_effect(kind, rgb, alpha, 500_000, {})
            data = np.ascontiguousarray(
                np.clip(np.asarray(out) * 255.0, 0, 255).astype("uint8"))
            image = QImage(data.data, width, height, width * 3, QImage.Format_RGB888).copy()
            return QIcon(QPixmap.fromImage(image))
        except Exception:                              # noqa: BLE001
            return None

    def _transition_icon(self, kind: str, width: int = 80, height: int = 45):
        """画一个转场在 u=0.5 时的示意缩略图（两张纯色 + 掩码）。"""
        try:
            import numpy as np
            from PySide6.QtGui import QImage
            from ...render import _transition_mask

            if kind in ("fade_black", "fade_white"):
                # 中点是纯色（真实渲染在 u=0.5 也是纯黑/纯白）
                value = 12 if kind == "fade_black" else 245
                data = np.full((height, width, 3), value, dtype="uint8")
                image = QImage(data.data, width, height, width * 3,
                               QImage.Format_RGB888).copy()
                from PySide6.QtGui import QIcon, QPixmap

                return QIcon(QPixmap.fromImage(image))
            mask = np.asarray(_transition_mask(kind, 0.5, width, height), dtype="float32")
            mask = np.broadcast_to(mask, (height, width))[..., None]
            a = np.zeros((height, width, 3), dtype="float32")
            b = np.zeros((height, width, 3), dtype="float32")
            a[..., 0], a[..., 1] = 240.0, 150.0          # 前一段：橙
            b[..., 1], b[..., 2] = 210.0, 240.0          # 后一段：青
            img = a * (1.0 - mask) + b * mask
            data = np.clip(img, 0, 255).astype("uint8")
            image = QImage(data.data, width, height, width * 3, QImage.Format_RGB888).copy()
            from PySide6.QtGui import QIcon, QPixmap

            return QIcon(QPixmap.fromImage(image))
        except Exception:                              # noqa: BLE001
            return None

    def _media_menu(self, pos) -> None:
        """素材右键：定位文件 / 从工程移除。"""
        from PySide6.QtWidgets import QMenu

        item = self.media_list.itemAt(pos)
        if item is None:
            return
        path = item.data(Qt.UserRole)
        if not path:
            return
        menu = QMenu(self)
        reveal = menu.addAction("在文件夹中显示")
        remove = menu.addAction("从工程移除")
        chosen = menu.exec(self.media_list.mapToGlobal(pos))
        if chosen is reveal:
            self._reveal_file(str(path))
        elif chosen is remove:
            self._remove_media(str(path))

    def _reveal_file(self, path: str) -> None:
        import subprocess
        import sys as _sys

        if not os.path.exists(path):
            self.bridge.statusMessage.emit("文件已经不在了")
            return
        try:
            if _sys.platform.startswith("win"):
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            elif _sys.platform == "darwin":
                subprocess.Popen(["open", "-R", path])
            else:
                subprocess.Popen(["xdg-open", os.path.dirname(path)])
        except OSError as exc:
            self.bridge.statusMessage.emit(f"打不开文件夹：{exc}")

    def _remove_media(self, path: str) -> None:
        """把素材从工程里删掉（时间线上的片段也一起删，和剪映一致）。"""
        project = self.bridge.project
        target = None
        for mat in project.materials.values():
            if str(getattr(mat, "path", "") or "") == str(path):
                target = mat
                break
        if target is None:
            self.bridge.statusMessage.emit("工程里找不到这个素材")
            return
        segs = [seg for track in project.tracks for seg in track.segments
                if seg.material_id == getattr(target, "id", "")]
        with self.bridge.render_lock:
            if segs:
                self.bridge.delete_segments(segs)
            try:
                project.materials.pop(getattr(target, "id", ""), None)
            except Exception:                          # noqa: BLE001
                pass
            self.bridge._epoch += 1
        self.bridge.touch()
        self.bridge.edited.emit()
        self.refresh_media()
        self.bridge.statusMessage.emit(f"已移除素材：{os.path.basename(path)}")

    # -- 「已添加」角标 / 筛选 --------------------------------------------- #
    @staticmethod
    def _path_key(path: str) -> str:
        """路径比较用的规范形式（Windows 下大小写/斜杠不敏感）。"""
        return os.path.normcase(os.path.abspath(str(path)))

    def _timeline_media_paths(self) -> set:
        """时间线上**至少有一个片段在用**的素材路径（规范化后）。

        只认片段的主材质（``material_id``）：特效/转场/速度这些附加材质
        没有素材路径，不该让列表里的东西变成「已添加」。
        """
        project = getattr(self.bridge, "project", None)
        used = set()
        if project is None:
            return used
        for track in getattr(project, "tracks", ()) or ():
            for seg in getattr(track, "segments", ()) or ():
                try:
                    material = project.material(getattr(seg, "material_id", ""))
                except Exception:                  # noqa: BLE001
                    material = None
                path = getattr(material, "path", None) if material is not None else None
                if path:
                    used.add(self._path_key(str(path)))
        return used

    def apply_media_filters(self, used=None) -> None:
        """「全部 / 仅显示未添加 / 仅显示已添加」：只隐藏行，不重建列表。

        和搜索、类型筛选是叠加的：那两个在建条目时已经筛过，这里只叠加
        「是否在时间线上」这一条。``used`` 可由调用方传入，省一次全时间线扫描。
        """
        if not hasattr(self, "media_list"):
            return
        mode = self.added_box.currentText() if hasattr(self, "added_box") else "全部"
        if used is None:
            used = self._timeline_media_paths()
        want_added = mode == "仅显示已添加"
        for row in range(self.media_list.count()):
            item = self.media_list.item(row)
            if item is None:
                continue
            path = item.data(Qt.UserRole)
            if not path:                           # 「（没有匹配的素材）」占位行
                continue
            added = self._path_key(str(path)) in used
            item.setHidden(mode != "全部" and added != want_added)

    def refresh_added_state(self) -> None:
        """时间线变了：补/去「· 已添加」角标，并把当前筛选重新套一遍。

        只改每条的文字（不 clear/重建），所以选中项和缩略图都保得住。
        """
        if not hasattr(self, "media_list") or not getattr(self, "_media_items", None):
            return
        used = self._timeline_media_paths()
        changed = False
        for row, (path, _kind, _material) in enumerate(self._media_items):
            item = self.media_list.item(row)
            if item is None:
                continue
            base = item.data(BASE_TEXT_ROLE)
            if base is None:
                continue
            text = f"{base}{ADDED_SUFFIX}" if self._path_key(path) in used else str(base)
            if item.text() != text:
                item.setText(text)
                changed = True
        if changed:
            self.media_list.viewport().update()
        self.apply_media_filters(used)

    def toggle_media_view(self) -> None:
        """列表 / 网格 切换；网格的格子要放得下名字（图标下面一行）。"""
        if not hasattr(self, "media_list"):
            return
        if self.media_list.viewMode() == QListView.IconMode:
            self.media_list.setViewMode(QListView.ListMode)
            self.media_list.setIconSize(QSize(32, 18))
            self.media_list.setGridSize(QSize())
            tip, mark = "网格视图", "▦"
        else:
            self.media_list.setViewMode(QListView.IconMode)
            self.media_list.setIconSize(QSize(64, 36))
            # 150×78：64×36 的图 + 下面两行名字，和剪映的格子接近
            self.media_list.setGridSize(QSize(150, 78))
            tip, mark = "列表视图", "☷"
        if hasattr(self, "view_btn"):
            self.view_btn.setText(mark)
            self.view_btn.setToolTip(tip)
        self.media_list.doItemsLayout()

    # -- 收藏 -------------------------------------------------------------- #
    def toggle_favorite(self, *_args) -> None:
        material = self._selected_material_obj()
        if material is None:
            self.bridge.statusMessage.emit("先在素材列表里选一个素材")
            return
        path = str(getattr(material, "path", "") or "")
        name = os.path.basename(path)
        if material.id in self.favorites:
            self.favorites.discard(material.id)
            self.bridge.statusMessage.emit(f"已取消收藏：{name}")
        else:
            self.favorites.add(material.id)
            self.bridge.statusMessage.emit(f"已收藏：{name}")
        self.refresh_media()

    def is_favorite(self, material) -> bool:
        """按**材质**收藏：同一文件在工程里可能有多个副本，星标要跟着副本走。"""
        return material is not None and material.id in self.favorites

    def _selected_material_obj(self):
        if not hasattr(self, "media_list") or not getattr(self, "_media_items", None):
            return None
        row = self.media_list.currentRow()
        if row < 0 or row >= len(self._media_items):
            return None
        entry = self._media_items[row]
        return entry[2] if len(entry) > 2 else None

    # -- 缩略图 ------------------------------------------------------------ #
    def _thumb_cache(self):
        """和视觉上其他缩略图共用一份缓存（解码都在工作线程里做）。"""
        from ..media_cache import MediaCache

        return MediaCache.shared()

    THUMB_W, THUMB_H = 64, 36

    def _thumbnail_for(self, path: str, kind: str):
        """素材列表的小图。**不再同步解码**：命中缓存立刻给，没命中先空着，
        工作线程解好之后信号把图标补上（否则打开 25 个素材的工程要等 1.5 秒）。"""
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QIcon, QPixmap

        if not hasattr(self, "_icon_cache"):
            self._icon_cache = {}
        cache_key = (path, kind)
        if cache_key in self._icon_cache:
            return self._icon_cache[cache_key]

        cache = self._thumb_cache()
        if kind == "audio":
            peaks = cache.waveform(path, 0, 8_000_000, buckets=self.THUMB_W)
            if peaks is None:
                return None
            icon = QIcon(self._waveform_pixmap(peaks))
            self._icon_cache[cache_key] = icon
            return icon
        pixmap = cache.pixmap(path, 0, self.THUMB_W, self.THUMB_H)
        if pixmap is None:
            return None
        pixmap = pixmap.scaled(self.THUMB_W, self.THUMB_H, Qt.KeepAspectRatio,
                               Qt.SmoothTransformation)
        icon = QIcon(pixmap)
        self._icon_cache[cache_key] = icon
        return icon

    def _on_media_thumb_ready(self) -> None:
        """缓存里多了一张图：把缺图标的行补上（不重建列表，保住选中项）。"""
        if not hasattr(self, "media_list") or not getattr(self, "_media_items", None):
            return
        changed = False
        for row, (path, kind, _material) in enumerate(self._media_items):
            item = self.media_list.item(row)
            if item is None or not item.icon().isNull():
                continue
            icon = self._thumbnail_for(path, kind)
            if icon is not None:
                item.setIcon(icon)
                changed = True
        if changed:
            self.media_list.viewport().update()

    def _waveform_pixmap(self, peaks):
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QColor, QPainter, QPixmap

        from ... import theme

        pixmap = QPixmap(self.THUMB_W, self.THUMB_H)
        pixmap.fill(QColor(theme.BG_INPUT))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(QColor(theme.ACCENT))
        mid = self.THUMB_H / 2
        for index, value in enumerate(peaks):
            if index >= self.THUMB_W:
                break
            half = max(1.0, float(value) * (self.THUMB_H / 2 - 1))
            painter.drawLine(index, int(mid - half), index, int(mid + half))
        painter.end()
        return pixmap

    def import_media(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        paths, _ = QFileDialog.getOpenFileNames(
            self, "导入媒体", "",
            "媒体 (*.mp4 *.mov *.mkv *.avi *.webm *.wav *.mp3 *.m4a *.png *.jpg *.jpeg *.bmp);;"
            "所有文件 (*)")
        if not paths:
            return
        added = 0
        dupes = 0
        for path in paths:
            result = self._add_material_only(path)
            if result == "added":
                added += 1
            elif result == "exists":
                dupes += 1
        self.refresh_media()
        message = f"已导入 {added} 个素材（拖到时间线或双击添加）"
        if dupes:
            message = f"新增 {added} 个，{dupes} 个已在素材库（未重复添加）"
        self.bridge.statusMessage.emit(message)
        self.bridge.edited.emit()

    def _add_material_only(self, path: str):
        """只把素材放进素材库，不放到时间线。

        同一个文件**只建一个材质**：按 normcase(abspath) 查已有素材，命中就复用。
        返回 "added" / "exists" / "failed"。
        """
        import os

        from ...media import probe
        from ...model import AudioMaterial, ImageMaterial, VideoMaterial

        ext = os.path.splitext(path)[1].lower()
        # 同一个文件只建一份材质（用户实测：导入两次会出现两条）
        if self.bridge.project.material_by_path(path) is not None:
            return "exists"
        try:
            if ext in (".png", ".jpg", ".jpeg", ".bmp", ".webp"):
                material = ImageMaterial(path=os.path.abspath(path),
                                         material_name=os.path.basename(path))
            elif ext in (".wav", ".mp3", ".m4a", ".aac", ".flac"):
                info = probe(path)
                material = AudioMaterial(path=os.path.abspath(path),
                                         duration=info.duration_us,
                                         name=os.path.basename(path))
            else:
                info = probe(path)
                material = VideoMaterial(path=os.path.abspath(path),
                                         duration=info.duration_us,
                                         name=os.path.basename(path))
        except Exception as exc:                       # noqa: BLE001
            self.bridge.statusMessage.emit(f"读不了 {os.path.basename(path)}: {exc}")
            return "failed"
        self.bridge.project.add_material(material)
        return "added"

    def _selected_material(self):
        """返回 (path, kind)。别把第三项（材质对象）一起返回——调用方是按两个值解包的。"""
        if not hasattr(self, "media_list") or not getattr(self, "_media_items", None):
            return None
        row = self.media_list.currentRow()
        if row < 0 or row >= len(self._media_items):
            return None
        entry = self._media_items[row]
        return (entry[0], entry[1])

    def add_selected_to_timeline(self, *_args) -> None:
        picked = self._selected_material()
        if picked is None:
            self.bridge.statusMessage.emit("先在素材列表里选一个素材")
            return
        path, kind = picked
        self.bridge.add_media_to_timeline(path, kind, self.bridge.playhead)

    # -- 字幕 -------------------------------------------------------------- #
    def _subtitle_track(self):
        for track in self.bridge.project.tracks:
            if track.type == "text":
                return track
        return None

    def refresh_subtitles(self) -> None:
        if not hasattr(self, "sub_list"):
            return
        from ... import subtitles

        self.sub_list.clear()
        try:
            subs = subtitles.extract_subtitles(self.bridge.project)
        except Exception:                              # noqa: BLE001
            subs = []
        for item in subs:
            self.sub_list.addItem(f"{item.start_us / 1e6:6.2f}s  {item.text[:28]}")
        if not subs:
            self.sub_list.addItem("（还没有字幕）")

    def import_subtitles(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        from ... import subtitles

        path, _ = QFileDialog.getOpenFileName(self, "导入字幕", "",
                                              "字幕 (*.srt *.vtt);;所有文件 (*)")
        if not path:
            return
        try:
            if path.lower().endswith(".vtt"):
                subs = subtitles.parse_vtt(path)
            else:
                subs = subtitles.parse_srt(path)
        except Exception as exc:                       # noqa: BLE001
            self.bridge.statusMessage.emit(f"字幕解析失败: {exc}")
            return
        if not subs:
            self.bridge.statusMessage.emit("这个字幕文件里没有条目")
            return
        tracks_before = len([t for t in self.bridge.project.tracks if t.type == "text"])
        subtitles.add_subtitles(self.bridge.project, subs)
        tracks_after = len([t for t in self.bridge.project.tracks if t.type == "text"])
        where = "新文字轨" if tracks_after > tracks_before else "现有文字轨"
        self.bridge.statusMessage.emit(f"已导入 {len(subs)} 条字幕（{where}）")
        self.refresh_subtitles()
        self.bridge.projectReloaded.emit()

    def export_subtitles(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        from ... import subtitles

        subs = subtitles.extract_subtitles(self.bridge.project)
        if not subs:
            self.bridge.statusMessage.emit("工程里没有字幕可导出")
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出字幕", "subtitles.srt",
                                              "字幕 (*.srt)")
        if not path:
            return
        try:
            subtitles.write_srt(subs, path)
        except Exception as exc:                       # noqa: BLE001
            self.bridge.statusMessage.emit(f"导出失败: {exc}")
            return
        self.bridge.statusMessage.emit(f"已导出 {len(subs)} 条字幕 → {os.path.basename(path)}")

    def auto_subtitles(self) -> None:
        """对时间线上第一段带声音的视频跑离线识别。"""
        from ... import asr, subtitles

        target = None
        for track in self.bridge.project.tracks:
            if track.type != "video":
                continue
            for seg in track.segments:
                try:
                    material = self.bridge.project.material(seg.material_id)
                except Exception:                      # noqa: BLE001
                    continue
                if getattr(material, "path", None):
                    target = (seg, material.path)
                    break
            if target:
                break
        if target is None:
            self.bridge.statusMessage.emit("时间线上还没有带声音的视频")
            return
        seg, path = target
        if asr.find_backend() is None:
            self.bridge.statusMessage.emit("没有找到离线识别后端（tools/whisper）")
            return
        self.asr_btn.setEnabled(False)
        self.bridge.statusMessage.emit("正在识别（离线，可能需要一会儿）…")
        try:
            subs = asr.transcribe_to_subtitles(path)
        except Exception as exc:                       # noqa: BLE001
            self.asr_btn.setEnabled(True)
            self.bridge.statusMessage.emit(f"识别失败: {exc}")
            return
        self.asr_btn.setEnabled(True)
        if not subs:
            self.bridge.statusMessage.emit("没识别出文字")
            return
        offset = int(seg.target_timerange.start) - int(seg.source_timerange.start)
        subtitles.add_subtitles(self.bridge.project, subs, offset_us=max(0, offset))
        self.bridge.statusMessage.emit(f"识别完成：{len(subs)} 条字幕")
        self.refresh_subtitles()
        self.bridge.projectReloaded.emit()

    # -- 切换 ------------------------------------------------------------- #
    def show_category(self, name: str) -> None:
        page = self.pages.get(name)
        if page is None:
            return
        scroll = self._page_scrolls.get(name)
        self.stack.setCurrentWidget(scroll if scroll is not None else page)
        self.title.setText(name)
        self._ensure_icons(name)
        self.refresh_applied_state()

    def _ensure_icons(self, name: str) -> None:
        """特效/转场的缩略图第一次打开那一页时才生成（启动少等约 1 秒）。

        生成完必须 `doItemsLayout()`：条目是先建出来（无图标）后排版的，
        不重排的话格子还按"没有图标"的高度算——文字会被挤没（用户实测报过）。
        """
        if not hasattr(self, "_icons_built"):
            self._icons_built = set()
        if name in self._icons_built:
            return
        from PySide6.QtGui import QIcon

        if name == "特效":
            from ..thumbs import effect_pixmap

            for row in range(self.effect_list.count()):
                item = self.effect_list.item(row)
                kind = str(item.data(Qt.UserRole) or "")
                if not item.icon().isNull():
                    continue
                item.setIcon(QIcon(effect_pixmap(kind)))
            self.effect_list.doItemsLayout()
            self._icons_built.add(name)
        elif name == "转场":
            from ..thumbs import transition_pixmap

            for row in range(self.transition_list.count()):
                item = self.transition_list.item(row)
                kind = str(item.data(Qt.UserRole) or "")
                if not item.icon().isNull():
                    continue
                item.setIcon(QIcon(transition_pixmap(kind)))
            self.transition_list.doItemsLayout()
            self._icons_built.add(name)

