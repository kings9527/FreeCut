"""属性面板。

P2 进度：**画面 · 基础** 已经是真控件（位置/缩放/旋转/不透明度），
改一下立刻写进模型并重渲预览；其余页签仍是占位，按计划逐批迁入。
"""

from __future__ import annotations

import os

from typing import Optional

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox,
                               QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                               QCheckBox, QSlider, QTabWidget, QVBoxLayout, QWidget)

from ... import builder, theme

#: 调节项的短标签（状态栏用）
ADJUST_LABELS = {
    "brightness": "亮度", "contrast": "对比", "saturation": "饱和",
    "temperature": "色温", "tint": "色调", "highlight": "高光",
    "shadow": "阴影", "white": "白场", "black": "黑场",
    "sharpen": "锐化", "vignette": "暗角",
}


class DiamondButton(QPushButton):
    """关键帧按钮：自绘菱形。字体里没 ◇/◆，用文字会变成方块（第 21 轮实测）。"""

    def __init__(self, parent=None) -> None:
        super().__init__("", parent)
        self.setFixedSize(22, 22)
        self.setCursor(Qt.PointingHandCursor)
        self._state = "none"          # none | some | here

    def set_state(self, state: str) -> None:
        self._state = state if state in ("none", "some", "here") else "none"
        self.update()

    def paintEvent(self, event) -> None:            # noqa: N802
        from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        centre = self.rect().center()
        r = 6
        path = QPainterPath()
        path.moveTo(centre.x(), centre.y() - r)
        path.lineTo(centre.x() + r, centre.y())
        path.lineTo(centre.x(), centre.y() + r)
        path.lineTo(centre.x() - r, centre.y())
        path.closeSubpath()
        accent = QColor("#4cc2d4")
        if self._state == "here":
            painter.setBrush(accent)
            painter.setPen(QPen(accent, 1))
        elif self._state == "some":
            painter.setBrush(accent.lighter(140) if hasattr(accent, "lighter") else accent)
            painter.setBrush(accent)
            painter.setPen(QPen(accent, 1))
            painter.drawPath(path)
            from PySide6.QtGui import QColor as _QColor
            painter.setBrush(_QColor(21, 21, 26))
            inner = QPainterPath()
            inner.moveTo(centre.x(), centre.y() - 3)
            inner.lineTo(centre.x() + 3, centre.y())
            inner.lineTo(centre.x(), centre.y() + 3)
            inner.lineTo(centre.x() - 3, centre.y())
            inner.closeSubpath()
            painter.setPen(Qt.NoPen)
            painter.drawPath(inner)
            return
        else:
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor("#6c6c7a"), 1.2))
        painter.drawPath(path)


class SliderRow(QWidget):
    """一行「滑杆 + 数字」：两边同步，数字是用户看到的值。"""

    valueChanged = Signal(float)
    keyframeRequested = Signal()          # 点了菱形：在播放头处打/删关键帧

    def __init__(self, label: str, minimum: float, maximum: float,
                 value: float = 0.0, decimals: int = 0, suffix: str = "",
                 steps: int = 1000, keyframable: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._min = float(minimum)
        self._max = float(maximum)
        self._updating = False

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 2, 0, 2)
        row.setSpacing(3)
        self.setMinimumHeight(24)
        name = QLabel(label, self)
        # 4 个汉字 ≈ 52px；留 8px 给描边，中文标签不再被切一半
        name.setFixedWidth(60)
        row.addWidget(name)

        self.slider = QSlider(Qt.Horizontal, self)
        self.slider.setRange(0, steps)
        self.slider.setMinimumWidth(56)
        row.addWidget(self.slider, 1)

        self.spin = QDoubleSpinBox(self)
        self.spin.setRange(self._min, self._max)
        self.spin.setDecimals(decimals)
        self.spin.setSuffix(suffix)
        # 去掉上下箭头：这一列只有 60 多像素，箭头吃掉一半宽度，
        # 「100%」会被切成「100」（第 242 轮截图实测）。剪映的数字框也没有箭头。
        from PySide6.QtWidgets import QAbstractSpinBox

        self.spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.spin.setMinimumHeight(22)
        self.spin.setFixedWidth(68)
        self.spin.setKeyboardTracking(False)
        row.addWidget(self.spin)

        self.kf_button = None
        if keyframable:
            self.kf_button = DiamondButton(self)
            self.kf_button.setToolTip("在播放头处添加关键帧（再点一次删除）")
            self.kf_button.clicked.connect(self.keyframeRequested.emit)
            row.addWidget(self.kf_button)

        self.slider.valueChanged.connect(self._on_slider)
        self.spin.valueChanged.connect(self._on_spin)
        self.set_value(value, emit=False)

    # -- 换算 ------------------------------------------------------------- #
    def _to_slider(self, value: float) -> int:
        span = self._max - self._min or 1.0
        return int(round((value - self._min) / span * self.slider.maximum()))

    def _from_slider(self, pos: int) -> float:
        span = self._max - self._min
        return self._min + span * pos / max(1, self.slider.maximum())

    def _on_slider(self, pos: int) -> None:
        if self._updating:
            return
        self._updating = True
        value = round(self._from_slider(pos), self.spin.decimals())
        self.spin.setValue(value)
        self._updating = False
        self.valueChanged.emit(value)

    def _on_spin(self, value: float) -> None:
        if self._updating:
            return
        self._updating = True
        self.slider.setValue(self._to_slider(float(value)))
        self._updating = False
        self.valueChanged.emit(float(value))

    # -- 外部 ------------------------------------------------------------- #
    def set_value(self, value: float, emit: bool = False) -> None:
        self._updating = True
        self.spin.setValue(float(value))
        self.slider.setValue(self._to_slider(float(value)))
        self._updating = False
        if emit:
            self.valueChanged.emit(float(value))


class Inspector(QWidget):
    """右栏：未选中给提示；选中后按页签编辑。

    页签顺序照剪映专业版（HANDOFF §5.5）：**AI编辑 / 画面 / 音频 / 变速 / 动画**
    在前，我们多出来的「文字 / 调节 / 曲线 / 效果」排在后面。剪映里做不到的
    AI 项留在页签里但灰显（点开能看到为什么）。
    """

    TABS = ("画面", "音频", "变速", "动画", "文字", "调节", "曲线", "效果")

    #: 选中片段后默认停在哪一页（剪映默认停在「画面」）
    DEFAULT_TAB = "画面"

    def __init__(self, bridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self._seg = None
        self._loading = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QWidget(self)
        header.setObjectName("PanelHeader")
        hbox = QHBoxLayout(header)
        hbox.setContentsMargins(0, 0, 8, 0)
        self.title = QLabel("属性", header)
        hbox.addWidget(self.title, 1)
        layout.addWidget(header)

        self.empty = QWidget(self)
        empty_box = QVBoxLayout(self.empty)
        empty_box.setContentsMargins(12, 12, 12, 12)
        hint = QLabel("未选择片段\n\n· 点击时间线片段进行选择\n· 拖动片段两端裁剪\n"
                      "· Ctrl + 滚轮 缩放时间线\n· 空格 播放 / 暂停", self.empty)
        hint.setWordWrap(True)
        hint.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        empty_box.addWidget(hint)
        empty_box.addStretch(1)
        layout.addWidget(self.empty, 1)

        from PySide6.QtWidgets import QScrollArea

        self.tabs = QTabWidget(self)
        self.tabs.setElideMode(Qt.ElideRight)
        self.tabs.setUsesScrollButtons(True)
        self.tabs.tabBar().setMinimumWidth(0)
        self.pages = {}
        for name in self.TABS:
            page = self._build_page(name)
            self.pages[name] = page
            scroll = QScrollArea(self)
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QScrollArea.NoFrame)
            scroll.setWidget(page)
            # 内容比面板宽时允许横向滚动：直接裁掉的话，右侧的数字框会被切一半
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            # 只允许"窄"：横向可滚动，右侧数字框不会被切。
            # 高度**不能**压到 1 —— 那会让 widgetResizable 把整页挤进视口，
            # 行被压扁（数字只剩上半截、曲线压住按钮，第 180 轮用户截图）。
            # 高度交给 minimumSizeHint：放不下就出竖滚动条。
            page.setMinimumWidth(1)
            self.tabs.addTab(scroll, name)
        layout.addWidget(self.tabs, 1)
        self.tabs.hide()

        bridge.selectionChanged.connect(self.on_selection)
        bridge.projectReloaded.connect(lambda: self.on_selection(None))

    # -- 页面 ------------------------------------------------------------- #
    def _build_page(self, name: str) -> QWidget:
        page = QWidget(self)
        box = QVBoxLayout(page)
        box.setContentsMargins(10, 10, 10, 10)
        box.setSpacing(8)

        if name == "文字":
            box.addWidget(self._build_text_group(page))
        elif name == "变速":
            box.addWidget(self._build_speed_group(page))
        elif name == "画面":
            box.addWidget(self._build_transform_group(page))
            box.addWidget(self._build_crop_group(page))
            box.addWidget(self._build_chroma_group(page))
            box.addWidget(self._build_mask_group(page))
        elif name == "调节":
            box.addWidget(self._build_adjust_group(page))
        elif name == "音频":
            box.addWidget(self._build_audio_group(page))
            box.addWidget(self._build_audio_cloud_group(page))
        elif name == "曲线":
            box.addWidget(self._build_hsl_group(page))
            box.addWidget(self._build_curve_group(page))
            box.addWidget(self._build_wheel_group(page))
        elif name == "效果":
            box.addWidget(self._build_effect_group(page))
        elif name == "动画":
            box.addWidget(self._build_animation_group(page))
        elif name == "AI编辑":
            box.addWidget(self._build_ai_group(page))
        else:
            box.addWidget(QLabel(f"「{name}」的控件在后续批次迁入", page))
        box.addStretch(1)
        return page

    def _build_ai_group(self, parent) -> QGroupBox:
        """剪映的 AI 页：我们离线做不到，逐项列出来并写明原因（不隐藏）。"""
        group = QGroupBox("AI 能力（需云端，离线版做不到）", parent)
        form = QVBoxLayout(group)
        form.setContentsMargins(8, 8, 8, 8)
        form.setSpacing(4)
        items = (
            ("智能抠像", "要云端人像分割模型；本机可用「色度抠图」或「智能抠像（近似）」"),
            ("美颜 / 瘦脸 / 大眼", "要云端人脸关键点模型；本机只有「磨皮（近似）」"),
            ("智能调色", "要云端 LUT 与场景识别"),
            ("AI 配音", "要云端声库；本机只有 Windows 离线 SAPI 音色"),
            ("文本朗读成片", "要云端 TTS；本机用 SAPI 可做近似（见命令行 fromscript）"),
            ("AI 画面生成", "要云端生成模型"),
        )
        self.ai_buttons = {}
        for title, reason in items:
            btn = QPushButton(title, group)
            btn.setEnabled(False)
            btn.setToolTip(reason)
            form.addWidget(btn)
            self.ai_buttons[title] = btn
        hint = QLabel("这些能力都依赖剪映云端服务；FreeCut 不联网、不绕过会员，"
                      "所以灰着。每一项的替代方案写在按钮提示里。", group)
        hint.setWordWrap(True)
        form.addWidget(hint)
        return group

    def _build_audio_cloud_group(self, parent) -> QGroupBox:
        """剪映音频页里依赖云端的那几项（灰显 + 原因）。"""
        group = QGroupBox("音频（更多）", parent)
        form = QVBoxLayout(group)
        form.setContentsMargins(8, 8, 8, 8)
        form.setSpacing(4)
        self.volume_db = QLabel("音量 0.0 dB", group)
        form.addWidget(self.volume_db)
        self.audio_cloud_buttons = {}
        for title, reason in (
            ("换音色", "要云端声库（本机可改音高：见上面的「变声」）"),
            ("人声美化", "要云端人声增强模型"),
            ("声音分离", "要云端人声/伴奏分离模型；本机可用「分离音频」把声音拆到音频轨"),
        ):
            btn = QPushButton(title, group)
            btn.setEnabled(False)
            btn.setToolTip(reason)
            form.addWidget(btn)
            self.audio_cloud_buttons[title] = btn

        # 响度统一改成真实现（EBU R128 测量 + 补增益），不再是灰显
        self.loudness_btn = QPushButton("响度统一（-14 LUFS）", group)
        self.loudness_btn.setToolTip("逐段测量响度，把音量补到 -14 LUFS（单段最多 ±12 dB）")
        self.loudness_btn.clicked.connect(self.normalize_loudness)
        form.addWidget(self.loudness_btn)
        return group

    def normalize_loudness(self) -> None:
        """把工程里的音频片段补到目标响度。"""
        from ...audio_loudness import normalize_project

        try:
            applied = normalize_project(self.bridge.project)
        except Exception as exc:                       # noqa: BLE001
            self.bridge.statusMessage.emit(f"响度统一失败: {exc}")
            return
        if not applied:
            self.bridge.statusMessage.emit("响度统一：没有需要调整的音频片段")
            return
        self.bridge.touch()
        self.bridge.edited.emit()
        self.bridge.statusMessage.emit(f"响度统一：已调整 {len(applied)} 段音频")

    def _build_transform_group(self, parent) -> QGroupBox:
        group = QGroupBox("基础", parent)
        form = QVBoxLayout(group)
        form.setContentsMargins(8, 8, 8, 8)
        form.setSpacing(2)

        self.row_x = SliderRow("位置 X", -1.0, 1.0, 0.0, decimals=3,
                               keyframable=True, parent=group)
        self.row_y = SliderRow("位置 Y", -1.0, 1.0, 0.0, decimals=3,
                               keyframable=True, parent=group)
        self.row_scale = SliderRow("缩放", 5.0, 400.0, 100.0, decimals=0, suffix="%",
                                   keyframable=True, parent=group)
        self.row_rotation = SliderRow("旋转", -180.0, 180.0, 0.0, decimals=0,
                                      suffix="°", keyframable=True, parent=group)
        self.row_alpha = SliderRow("不透明度", 0.0, 100.0, 100.0, decimals=0,
                                   suffix="%", keyframable=True, parent=group)
        self.keyframe_props = dict(self.KEYFRAME_PROPS)
        self.kf_rows = {"位置 X": self.row_x, "位置 Y": self.row_y, "缩放": self.row_scale,
                        "旋转": self.row_rotation, "不透明度": self.row_alpha}
        for label, row in self.kf_rows.items():
            row.keyframeRequested.connect(
                lambda l=label, r=row: self._toggle_keyframe(r, l, r.spin.value()))
        for row in (self.row_x, self.row_y, self.row_scale, self.row_rotation,
                    self.row_alpha):
            form.addWidget(row)

        from ...color import BLEND_NAME_TO_ID

        blend_row = QHBoxLayout()
        blend_row.setContentsMargins(0, 2, 0, 2)
        blend_row.setSpacing(3)
        blend_label = QLabel("混合模式", group)
        blend_label.setFixedWidth(52)
        blend_row.addWidget(blend_label)
        self.blend_combo = QComboBox(group)
        # 「正常」排第一；BLEND_NAME_TO_ID 里也有「正常」，去重后保持固定顺序。
        for label in dict.fromkeys(["正常", *BLEND_NAME_TO_ID]):
            self.blend_combo.addItem(label, label)
        self.blend_combo.setEnabled(False)
        self.blend_combo.setToolTip("把当前画面叠到下方画面上的方式（正常 = 不混合）")
        self.blend_combo.currentIndexChanged.connect(
            lambda _index: self._apply_blend_mode())
        blend_row.addWidget(self.blend_combo, 1)
        form.addLayout(blend_row)

        reset = QPushButton("复位（位置 0 / 100% / 0° / 100%）", group)
        reset.clicked.connect(self.reset_transform)
        form.addWidget(reset)

        self.row_x.valueChanged.connect(lambda v: self._apply("tx", v))
        self.row_y.valueChanged.connect(lambda v: self._apply("ty", v))
        self.row_scale.valueChanged.connect(lambda v: self._apply("scale", v / 100.0))
        self.row_rotation.valueChanged.connect(lambda v: self._apply("rotation", v))
        self.row_alpha.valueChanged.connect(lambda v: self._apply("alpha", v / 100.0))
        return group

    # -- 混合模式 ---------------------------------------------------------- #
    def _blend_label(self, seg) -> str:
        """把片段现有的 mix_mode 材质映射回剪映的中文标签。"""
        if seg is None:
            return "正常"
        from ...color import BLEND_NAME_TO_ID

        id_to_name = {ident: label for label, ident in BLEND_NAME_TO_ID.items()}
        try:
            refs = self.bridge.project.refs_of(seg)
        except Exception:                              # noqa: BLE001
            return "正常"
        for ref in refs:
            if getattr(ref, "type", "") != "mix_mode":
                continue
            name = str(ref.get("name") or "")
            if name in BLEND_NAME_TO_ID:
                return name
            name_id = str(ref.get("name_id") or "")
            if name_id in id_to_name:
                return id_to_name[name_id]
        return "正常"

    def _load_blend_mode(self) -> None:
        """按当前选中片段刷新下拉框；没有可混合的片段时灰显。"""
        combo = getattr(self, "blend_combo", None)
        if combo is None:
            return
        enabled = self._seg is not None and not self._seg_is_audio()
        combo.setEnabled(enabled)
        label = self._blend_label(self._seg) if enabled else "正常"
        index = combo.findText(label)
        # 程序化 setCurrentIndex 也会发 currentIndexChanged，用 blocker 防重入。
        with QSignalBlocker(combo):
            combo.setCurrentIndex(max(0, int(index)))

    def _apply_blend_mode(self) -> None:
        """用户改混合模式：先清掉旧材质，再按标签写入（正常 = 删除）。"""
        seg = self._seg
        if seg is None or self._loading or self._seg_is_audio():
            return
        from ...color import BLEND_NAME_TO_ID

        label = str(self.blend_combo.currentData() or self.blend_combo.currentText())
        if label not in BLEND_NAME_TO_ID:
            label = "正常"
        with self.bridge.render_lock:
            old_ids = {ref.id for ref in self.bridge.project.refs_of(seg)
                       if getattr(ref, "type", "") == "mix_mode"}
            seg.extra_material_refs = [rid for rid in seg.extra_material_refs
                                       if rid not in old_ids]
            for rid in old_ids:
                self.bridge.project.materials.pop(rid, None)
            if label != "正常":
                # builder 只负责新建；旧材质已在上面的清理里删掉，避免叠加多条。
                material = builder.set_mix_mode(self.bridge.project, seg, label)
                material["name_id"] = BLEND_NAME_TO_ID[label]
            self.bridge.touch()
        self.bridge.edited.emit()
        self.bridge.statusMessage.emit(f"混合模式：{label}")

    def _build_adjust_group(self, parent) -> QGroupBox:
        from ...color import ADJUST_SPEC

        group = QGroupBox("调节（剪映单位 -50…50）", parent)
        form = QVBoxLayout(group)
        form.setContentsMargins(10, 8, 10, 10)
        form.setSpacing(3)
        self.adjust_rows = {}
        self.adjust_kf_rows = {}
        for key, spec in ADJUST_SPEC.items():
            kf_name = self.ADJUST_KEYFRAME_NAMES.get(key)
            row = SliderRow(spec["label"], float(spec["min"]), float(spec["max"]),
                            float(spec["default"]), decimals=0,
                            keyframable=kf_name is not None, parent=group)
            row.valueChanged.connect(lambda value, k=key: self._apply_adjust(k, value))
            if kf_name is not None:
                label = spec["label"]
                self.keyframe_props[label] = [kf_name]
                self.kf_rows[label] = row
                self.adjust_kf_rows[key] = row
                row.keyframeRequested.connect(
                    lambda l=label, r=row: self._toggle_keyframe(r, l, r.spin.value()))
            else:
                row.setToolTip("这个参数在剪映的 KFType 枚举里没有对应名字，暂不支持关键帧")
            form.addWidget(row)
            self.adjust_rows[key] = row
        reset = QPushButton("调节归零", group)
        reset.clicked.connect(self.reset_adjust)
        form.addWidget(reset)
        return group

    # -- 动画 -------------------------------------------------------------- #
    def _build_animation_group(self, parent) -> QGroupBox:
        from PySide6.QtWidgets import QComboBox

        group = QGroupBox("动画", parent)
        box = QVBoxLayout(group)
        box.setContentsMargins(8, 8, 8, 8)
        box.setSpacing(4)

        self.anim_boxes = {}
        for anim_type, label in (("in", "入场"), ("out", "出场"), ("loop", "循环")):
            row = QHBoxLayout()
            row.addWidget(QLabel(label, group))
            combo = QComboBox(group)
            combo.addItem("（无）", "")
            for kind, (name, kind_type) in self._kind_names(anim_type):
                combo.addItem(name, name)
            combo.currentIndexChanged.connect(
                lambda _i, t=anim_type: self._apply_animation(t))
            row.addWidget(combo, 1)
            box.addLayout(row)
            self.anim_boxes[anim_type] = combo

        self.anim_duration = SliderRow("时长", 0.2, 3.0, 0.8, decimals=1, suffix=" s",
                                       parent=group)
        box.addWidget(self.anim_duration)

        remove = QPushButton("清除这个片段的所有动画", group)
        remove.clicked.connect(self.remove_animations)
        box.addWidget(remove)
        self.anim_hint = QLabel("", group)
        self.anim_hint.setWordWrap(True)
        box.addWidget(self.anim_hint)
        return group

    @staticmethod
    def _kind_names(anim_type: str):
        from ...animation import KIND_TO_NAME

        out = []
        for kind, (name, kind_type) in KIND_TO_NAME.items():
            if kind_type == anim_type:
                out.append((kind, (name, kind_type)))
        return out

    def _animation_refs(self, seg) -> list:
        """剪映把动画放在 ``material_animation`` 材质的 animations 列表里。"""
        try:
            return [ref for ref in self.bridge.project.refs_of(seg)
                    if getattr(ref, "type", "") == "material_animation"]
        except Exception:                              # noqa: BLE001
            return []

    def _animation_entries(self, seg) -> list:
        entries = []
        for ref in self._animation_refs(seg):
            entries.extend(list(ref.get("animations") or []))
        return entries

    def _load_animations(self) -> None:
        seg = self._seg
        if seg is None:
            return
        self._loading = True
        entries = self._animation_entries(seg)
        present = {}
        for entry in entries:
            anim_type = str(entry.get("type") or "")
            present[anim_type] = str(entry.get("name") or "")
            if entry.get("duration"):
                self.anim_duration.set_value(float(entry["duration"]) / 1e6)
        for anim_type, combo in getattr(self, "anim_boxes", {}).items():
            want = present.get(anim_type, "")
            index = combo.findData(want) if want else 0
            combo.setCurrentIndex(max(0, int(index)))
        self.anim_hint.setText(
            f"已有 {len(entries)} 个动画；614 个剪映动画名里 270 个能落到 24 种引擎"
            if entries else "")
        self._loading = False

    def _apply_animation(self, anim_type: str) -> None:
        seg = self._seg
        if seg is None or self._loading:
            return
        combo = self.anim_boxes.get(anim_type)
        if combo is None:
            return
        name = combo.currentData() or ""
        from ... import builder

        if name:
            # add_animation 内部已经会替换同类型的旧条目
            builder.add_animation(self.bridge.project, seg, name, anim_type=anim_type,
                                  duration_us=int(self.anim_duration.spin.value() * 1e6))
        else:
            for ref in self._animation_refs(seg):
                items = [a for a in (ref.get("animations") or [])
                         if str(a.get("type") or "") != anim_type]
                ref["animations"] = items
        self.bridge.edited.emit()
        self.bridge.statusMessage.emit(
            f"动画：{anim_type} = {name or '无'}，时长 {self.anim_duration.spin.value():.1f}s")
        self._load_animations()

    def remove_animations(self) -> None:
        seg = self._seg
        if seg is None:
            return
        ids = {ref.id for ref in self._animation_refs(seg)}
        seg.extra_material_refs = [r for r in seg.extra_material_refs if r not in ids]
        for entry_type in getattr(self, "anim_boxes", {}):
            self.anim_boxes[entry_type].blockSignals(True)
            self.anim_boxes[entry_type].setCurrentIndex(0)
            self.anim_boxes[entry_type].blockSignals(False)
        self.bridge.statusMessage.emit("已清除动画")
        self.bridge.edited.emit()
        self._load_animations()

    # -- 效果（参数编辑）--------------------------------------------------- #
    def _build_effect_group(self, parent) -> QGroupBox:
        from PySide6.QtWidgets import QComboBox

        group = QGroupBox("效果", parent)
        box = QVBoxLayout(group)
        box.setContentsMargins(8, 8, 8, 8)
        box.setSpacing(4)

        row = QHBoxLayout()
        row.addWidget(QLabel("已加特效", group))
        self.effect_box = QComboBox(group)
        self.effect_box.currentIndexChanged.connect(lambda _i: self._build_params())
        row.addWidget(self.effect_box, 1)
        box.addLayout(row)

        self.params_host = QWidget(group)
        self.params_layout = QVBoxLayout(self.params_host)
        self.params_layout.setContentsMargins(0, 0, 0, 0)
        self.params_layout.setSpacing(2)
        box.addWidget(self.params_host)

        self.effect_hint = QLabel("", group)
        self.effect_hint.setWordWrap(True)
        box.addWidget(self.effect_hint)

        remove = QPushButton("移除这个特效", group)
        remove.clicked.connect(self.remove_effect)
        box.addWidget(remove)

        fade_row = QHBoxLayout()
        fade_row.addWidget(QLabel("转场时长", group))
        self.transition_row = SliderRow("", 200.0, 3000.0, 800.0, decimals=0,
                                        suffix=" ms", parent=group)
        fade_row.addWidget(self.transition_row, 1)
        box.addLayout(fade_row)
        self.transition_row.valueChanged.connect(self._apply_transition_length)

        # 转场时长（只在选中的片段带转场时可用）
        row = QHBoxLayout()
        row.addWidget(QLabel("转场时长", group))
        self.transition_spin = QDoubleSpinBox(group)
        self.transition_spin.setRange(0.1, 5.0)
        self.transition_spin.setSingleStep(0.1)
        self.transition_spin.setDecimals(1)
        self.transition_spin.setSuffix(" 秒")
        self.transition_spin.setEnabled(False)
        self.transition_spin.setToolTip("选中带转场的片段后可以改时长")
        self.transition_spin.valueChanged.connect(self._apply_transition_duration)
        row.addWidget(self.transition_spin, 1)
        box.addLayout(row)

        return group

    def _effect_refs(self, seg) -> list:
        try:
            return [ref for ref in self.bridge.project.refs_of(seg)
                    if getattr(ref, "type", "") == "effect"]
        except Exception:                              # noqa: BLE001
            return []

    def _load_effects(self) -> None:
        from ... import effects

        seg = self._seg
        if seg is None:
            return
        self._loading = True
        refs = self._effect_refs(seg)
        self.effect_box.clear()
        if not refs:
            self.effect_box.addItem("（这个片段没有特效）")
        else:
            for ref in refs:
                kind = str(ref.get("kind") or "")
                self.effect_box.addItem(effects.label(kind) or kind)
        self._effect_kinds = [str(ref.get("kind") or "") for ref in refs]
        # 转场时长
        transition = None
        for ref in self.bridge.project.refs_of(seg):
            if getattr(ref, "type", "") == "transition":
                transition = ref
                break
        if transition is not None:
            self.transition_row.set_value(float(transition.get("duration") or 0) / 1000.0)
        self._loading = False
        self._build_params()

    def _build_params(self) -> None:
        from ... import effects

        while self.params_layout.count():
            item = self.params_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.param_rows = {}
        if self._seg is None or not getattr(self, "_effect_kinds", None):
            self.effect_hint.setText("")
            return
        index = max(0, self.effect_box.currentIndex())
        if index >= len(self._effect_kinds):
            return
        kind = self._effect_kinds[index]
        spec = effects.EFFECT_SPEC.get(kind, {})
        refs = self._effect_refs(self._seg)
        values = {}
        if index < len(refs):
            values = dict(refs[index].get("params") or {})
        self.effect_hint.setText(spec.get("hint", ""))
        defaults = spec.get("params", {})
        for key, default in defaults.items():
            current = values.get(key, default)
            low, high = self._param_range(key, float(default))
            row = SliderRow(key, low, high, float(current), decimals=2,
                            parent=self.params_host)
            row.valueChanged.connect(
                lambda value, k=key: self._apply_effect_param(k, value))
            self.params_layout.addWidget(row)
            self.param_rows[key] = row

    @staticmethod
    def _param_range(key: str, default: float):
        """按参数名给一个合理范围（没有统一的 spec，只能按语义猜）。"""
        if key in ("radius", "size", "lines", "feather", "edge", "shift"):
            return (0.0, max(8.0, default * 6))
        if key in ("period_s",):
            return (0.2, 12.0)
        if key in ("density",):
            return (0.0, 0.05)
        return (0.0, max(1.0, default * 3))

    def _apply_effect_param(self, key: str, value: float) -> None:
        if self._seg is None or self._loading:
            return
        index = max(0, self.effect_box.currentIndex())
        refs = self._effect_refs(self._seg)
        if index >= len(refs):
            return
        before = self._color_state()          # 复用同一套快照结构（hsl/curve/wheel）
        ref = refs[index]
        params = dict(ref.get("params") or {})
        params[key] = float(value)
        ref["params"] = params
        self.bridge.record_color(self._seg, before, self._color_state(), "特效参数")
        self.bridge.edited.emit()
        self.bridge.statusMessage.emit(f"特效参数 {key} = {value:g}")

    def remove_effect(self) -> None:
        seg = self._seg
        if seg is None:
            return
        index = max(0, self.effect_box.currentIndex())
        refs = self._effect_refs(seg)
        if index >= len(refs):
            return
        ref = refs[index]
        seg.extra_material_refs = [r for r in seg.extra_material_refs if r != ref.id]
        self.bridge.statusMessage.emit("已移除特效")
        self.bridge.edited.emit()
        self.load_values()

    def _apply_transition_length(self, value: float) -> None:
        if self._seg is None or self._loading:
            return
        transition = None
        for ref in self.bridge.project.refs_of(self._seg):
            if getattr(ref, "type", "") == "transition":
                transition = ref
                break
        if transition is None:
            return
        transition["duration"] = int(value * 1000)
        self.bridge.edited.emit()
        self.bridge.statusMessage.emit(f"转场时长 {value:.0f} ms")

    # -- 关键帧 ------------------------------------------------------------ #
    #: 剪映草稿里的属性名；渲染器只认这 6 个
    KEYFRAME_PROPS = {
        "位置 X": ["KFTypePositionX"],
        "位置 Y": ["KFTypePositionY"],
        "缩放": ["KFTypeScaleX", "KFTypeScaleY"],
        "旋转": ["KFTypeRotation"],
        "不透明度": ["KFTypeAlpha"],
    }

    #: 界面用百分比（50 = 50%），草稿与渲染器用小数（0.5）。写关键帧必须换算，
    #: 否则 KFTypeScaleX=50 会把画面放大 50 倍（第 21 轮测试抓到过）。
    KEYFRAME_SCALE = {"缩放": 0.01, "不透明度": 0.01}

    #: 调节参数 → 草稿属性名。名字来自剪映二进制里的 186 个 KFType 枚举
    #: （docs/measurements/kftype-values.txt）；「高光」在剪映里拼成
    #: KFTypeHightLight（多一个 t），照抄才认。色调（tint）没有对应名字，故不可打帧。
    ADJUST_KEYFRAME_NAMES = {
        "brightness": "KFTypeBrightness",
        "contrast": "KFTypeContrast",
        "saturation": "KFTypeSaturation",
        "temperature": "KFTypeTemperature",
        "highlight": "KFTypeHightLight",
        "shadow": "KFTypeShadow",
        "sharpen": "KFTypeSharpen",
        "vignette": "KFTypeVignetting",
        "white": "KFTypeWhite",
        "black": "KFTypeBlack",
    }

    def _kf_state(self, seg) -> dict:
        return {name: [(k.time_offset, k.value, k.curve_type) for k in keys]
                for name, keys in (seg.keyframes or {}).items()}

    def _restore_kf(self, seg, state: dict) -> None:
        from ...model import Keyframe

        seg.keyframes = {
            name: [Keyframe(int(t), float(v), str(c)) for t, v, c in items]
            for name, items in state.items()
        }

    def _toggle_keyframe(self, row, label: str, value: float) -> None:
        seg = self._seg
        if seg is None:
            return
        props = self.keyframe_props.get(label)
        if not props:
            return
        offset = int(self.bridge.playhead) - int(seg.target_timerange.start)
        offset = max(0, min(offset, int(seg.target_timerange.duration)))
        factor = self.KEYFRAME_SCALE.get(label, 1.0)
        value = float(value) * factor
        before = self._kf_state(seg)
        existing = []
        for prop in props:
            existing.extend(seg.keyframes.get(prop, []))
        near = [k for k in existing if abs(int(k.time_offset) - offset) < 34_000]
        if near:
            for prop in props:
                seg.keyframes[prop] = [k for k in seg.keyframes.get(prop, [])
                                       if abs(int(k.time_offset) - offset) >= 34_000]
                if not seg.keyframes[prop]:
                    seg.keyframes.pop(prop, None)
            message = f"已删除 {label} 在 {offset / 1e6:.2f}s 的关键帧"
        else:
            from ... import builder

            for prop in props:
                builder.add_keyframe(seg, prop, offset, float(value))
            message = f"已添加 {label} 关键帧（{offset / 1e6:.2f}s）"
        self.bridge.record_keyframes(seg, before, self._kf_state(seg), "关键帧")
        self.bridge.statusMessage.emit(message)
        self._refresh_kf_buttons()
        self.bridge.edited.emit()

    def _refresh_kf_buttons(self) -> None:
        seg = self._seg
        if seg is None:
            return
        offset = int(self.bridge.playhead) - int(seg.target_timerange.start)
        for label, row in getattr(self, "kf_rows", {}).items():
            if row.kf_button is None:
                continue
            props = self.keyframe_props.get(label, [])
            keys = [k for prop in props for k in seg.keyframes.get(prop, [])]
            has_any = bool(keys)
            on_here = any(abs(int(k.time_offset) - offset) < 34_000 for k in keys)
            row.kf_button.set_state("here" if on_here else ("some" if has_any else "none"))
            row.kf_button.setToolTip(
                f"{label}：{len(keys)} 个关键帧" + ("（当前位置有关键帧）" if on_here else ""))

    # -- 变速 -------------------------------------------------------------- #
    def _build_speed_group(self, parent) -> QGroupBox:
        from PySide6.QtWidgets import QComboBox

        group = QGroupBox("变速", parent)
        box = QVBoxLayout(group)
        box.setContentsMargins(8, 8, 8, 8)
        box.setSpacing(4)

        self.row_speed = SliderRow("速度", 0.25, 4.0, 1.0, decimals=2, suffix="×",
                                   parent=group)
        box.addWidget(self.row_speed)
        self.speed_hint = QLabel("", group)
        self.speed_hint.setWordWrap(True)
        self.speed_hint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        box.addWidget(self.speed_hint)
        reset = QPushButton("恢复 1×", group)
        reset.clicked.connect(lambda: self.row_speed.set_value(1.0, emit=True))
        box.addWidget(reset)

        curve_row = QHBoxLayout()
        curve_row.addWidget(QLabel("预设", group))
        self.speed_curve_box = QComboBox(group)
        from ...speed import PRESETS

        self.SPEED_PRESETS = PRESETS
        for name in self.SPEED_PRESETS:
            self.speed_curve_box.addItem(name)
        self.speed_curve_box.setCurrentIndex(0)
        curve_row.addWidget(self.speed_curve_box, 1)
        box.addLayout(curve_row)

        apply_curve = QPushButton("套用预设", group)
        apply_curve.clicked.connect(self._apply_speed_curve)
        box.addWidget(apply_curve)

        # 可拖点的曲线（剪映「曲线变速」那张图）：拖一下就直接改 seg.speed_curve
        from .curve_editor import CurveEditor

        self.speed_curve_editor = CurveEditor(group)
        self.speed_curve_editor.pointsChanged.connect(self._on_curve_edited)
        box.addWidget(self.speed_curve_editor)
        reset_curve = QPushButton("曲线恢复匀速", group)
        reset_curve.clicked.connect(self._reset_speed_curve)
        box.addWidget(reset_curve)

        tip = QLabel("平均速度会写进 seg.speed（导出与音频按它算）；曲线参与逐帧取帧", group)
        tip.setWordWrap(True)
        box.addWidget(tip)
        self.row_speed.valueChanged.connect(self._apply_speed)
        return group

    def _speed_state(self, seg) -> dict:
        return {"speed": float(seg.speed),
                "curve": list(getattr(seg, "speed_curve", []) or [])}

    def _load_speed(self) -> None:
        seg = self._seg
        if seg is None:
            return
        self._loading = True
        self.row_speed.set_value(float(seg.speed or 1.0))
        curve = list(getattr(seg, "speed_curve", []) or [])
        self.speed_curve_editor.set_points(curve or [(0.0, 1.0), (1.0, 1.0)])
        self._update_speed_hint()
        self._loading = False

    def _on_curve_edited(self, points) -> None:
        """拖动曲线控制点：直接写进片段（和其它滑杆一样可撤销）。"""
        if self._loading:
            return
        seg = self._seg
        if seg is None or len(points) < 2:
            return
        from ... import builder

        before = self._speed_state(seg)
        builder.set_speed_curve(self.bridge.project, seg,
                                [{"x": float(x), "y": float(y)} for x, y in points])
        after = self._speed_state(seg)
        self.bridge.record_speed(seg, before, after, "拖曲线变速")
        self._loading = True
        self.row_speed.set_value(float(seg.speed or 1.0))
        self._update_speed_hint()
        self._loading = False
        self.bridge.edited.emit()

    def _reset_speed_curve(self) -> None:
        seg = self._seg
        if seg is None:
            return
        before = self._speed_state(seg)
        with self.bridge.render_lock:
            seg.speed_curve = []
            for ref in self.bridge.project.refs_of(seg):
                if getattr(ref, "type", "") == "speed":
                    ref["mode"] = "normal"
        after = self._speed_state(seg)
        self.bridge.record_speed(seg, before, after, "曲线恢复匀速")
        self._load_speed()
        self.bridge.edited.emit()

    def _update_speed_hint(self) -> None:
        seg = self._seg
        if seg is None:
            return
        source = max(1, int(seg.source_timerange.duration))
        target = max(1, int(seg.target_timerange.duration))
        self.speed_hint.setText(
            f"时长 {source / 1e6:.2f}s → {target / 1e6:.2f}s（源 {source / 1e6:.2f}s 不变）")

    def _apply_speed(self, value: float) -> None:
        seg = self._seg
        if seg is None or self._loading:
            return
        from ... import builder

        before = self._speed_state(seg)
        builder.set_speed(seg, float(value))
        after = self._speed_state(seg)
        self.bridge.record_speed(seg, before, after, "变速")
        self._update_speed_hint()
        self.bridge.edited.emit()
        self.bridge.statusMessage.emit(
            f"速度 {value:.2f}× → 时长 {seg.target_timerange.duration / 1e6:.2f}s")

    def _apply_speed_curve(self) -> None:
        seg = self._seg
        if seg is None:
            return
        from ... import builder

        name = self.speed_curve_box.currentText()
        points = self.SPEED_PRESETS.get(name)
        if not points:
            return
        before = self._speed_state(seg)
        pts = [{"x": float(x), "y": float(y)} for x, y in points]
        builder.set_speed_curve(self.bridge.project, seg, pts)
        after = self._speed_state(seg)
        self.bridge.record_speed(seg, before, after, f"曲线变速：{name}")
        self.row_speed.set_value(float(seg.speed))
        self._update_speed_hint()
        self.bridge.edited.emit()
        self.bridge.statusMessage.emit(
            f"曲线变速「{name}」：平均 {seg.speed:.2f}×，{len(seg.speed_curve)} 个控制点")

    # -- 裁剪 -------------------------------------------------------------- #
    def _build_crop_group(self, parent) -> QGroupBox:
        group = QGroupBox("裁剪", parent)
        box = QVBoxLayout(group)
        box.setContentsMargins(8, 8, 8, 8)
        box.setSpacing(2)

        self.row_crop_left = SliderRow("左", 0.0, 0.48, 0.0, decimals=2, parent=group)
        self.row_crop_right = SliderRow("右", 0.0, 0.48, 0.0, decimals=2, parent=group)
        self.row_crop_top = SliderRow("上", 0.0, 0.48, 0.0, decimals=2, parent=group)
        self.row_crop_bottom = SliderRow("下", 0.0, 0.48, 0.0, decimals=2, parent=group)
        for row in (self.row_crop_left, self.row_crop_right,
                    self.row_crop_top, self.row_crop_bottom):
            box.addWidget(row)
            row.valueChanged.connect(lambda _v: self._apply_crop())

        reset = QPushButton("裁剪复位", group)
        reset.clicked.connect(self.reset_crop)
        box.addWidget(reset)

        self.crop_hint = QLabel("裁掉的边以源画面为比例（0.20 = 裁掉 20%）", group)
        self.crop_hint.setWordWrap(True)
        box.addWidget(self.crop_hint)
        return group

    def _crop_material(self):
        seg = self._seg
        if seg is None:
            return None
        try:
            return self.bridge.project.material(seg.material_id)
        except Exception:                              # noqa: BLE001
            return None

    def _crop_state(self) -> dict:
        mat = self._crop_material()
        return dict(mat.get("crop") or {}) if mat is not None else {}

    def _load_crop(self) -> None:
        seg = self._seg
        if seg is None:
            return
        self._loading = True
        crop = self._crop_state()
        self.row_crop_left.set_value(float(crop.get("upper_left_x", 0.0) or 0.0))
        self.row_crop_top.set_value(float(crop.get("upper_left_y", 0.0) or 0.0))
        self.row_crop_right.set_value(1.0 - float(crop.get("lower_right_x", 1.0) or 1.0))
        self.row_crop_bottom.set_value(1.0 - float(crop.get("lower_right_y", 1.0) or 1.0))
        self._update_crop_hint()
        self._loading = False

    def _update_crop_hint(self) -> None:
        left = self.row_crop_left.spin.value()
        right = self.row_crop_right.spin.value()
        top = self.row_crop_top.spin.value()
        bottom = self.row_crop_bottom.spin.value()
        keep_w = max(0.0, 1.0 - left - right)
        keep_h = max(0.0, 1.0 - top - bottom)
        self.crop_hint.setText(
            f"保留 {keep_w * 100:.0f}% × {keep_h * 100:.0f}%（裁掉四边后重采样回原尺寸）")

    def _apply_crop(self) -> None:
        seg = self._seg
        if seg is None or self._loading:
            return
        mat = self._crop_material()
        if mat is None:
            return
        before = self._crop_state()
        left = float(self.row_crop_left.spin.value())
        right = float(self.row_crop_right.spin.value())
        top = float(self.row_crop_top.spin.value())
        bottom = float(self.row_crop_bottom.spin.value())
        crop = {
            "upper_left_x": left,
            "upper_left_y": top,
            "lower_right_x": max(left + 0.02, 1.0 - right),
            "lower_right_y": max(top + 0.02, 1.0 - bottom),
        }
        mat["crop"] = crop
        self.bridge.record_crop(seg, before, crop, "裁剪")
        self._update_crop_hint()
        self.bridge.edited.emit()
        self.bridge.statusMessage.emit(
            f"裁剪：保留 {crop['lower_right_x'] - crop['upper_left_x']:.2f} × "
            f"{crop['lower_right_y'] - crop['upper_left_y']:.2f}")

    def reset_crop(self) -> None:
        seg = self._seg
        if seg is None:
            return
        mat = self._crop_material()
        if mat is None:
            return
        before = self._crop_state()
        mat["crop"] = {"upper_left_x": 0.0, "upper_left_y": 0.0,
                       "lower_right_x": 1.0, "lower_right_y": 1.0}
        self.bridge.record_crop(seg, before, dict(mat["crop"]), "裁剪复位")
        self._load_crop()
        self.bridge.edited.emit()
        self.bridge.statusMessage.emit("裁剪已复位")

    # -- 抠像 / 蒙版 -------------------------------------------------------- #
    def _build_chroma_group(self, parent) -> QGroupBox:
        from PySide6.QtWidgets import QCheckBox

        group = QGroupBox("抠像（色度）", parent)
        box = QVBoxLayout(group)
        box.setContentsMargins(8, 8, 8, 8)
        box.setSpacing(2)

        self.chroma_on = QCheckBox("启用色度抠图", group)
        self.chroma_on.toggled.connect(self._toggle_chroma)
        box.addWidget(self.chroma_on)

        colour_row = QHBoxLayout()
        colour_row.addWidget(QLabel("抠掉的颜色", group))
        self.chroma_btn = QPushButton("#00FF00", group)
        self.chroma_btn.clicked.connect(self._pick_chroma_colour)
        colour_row.addWidget(self.chroma_btn, 1)
        box.addLayout(colour_row)

        self.row_similarity = SliderRow("相似度", 0.0, 1.0, 0.25, decimals=2, parent=group)
        self.row_smooth = SliderRow("平滑", 0.0, 1.0, 0.12, decimals=2, parent=group)
        self.row_spill = SliderRow("溢色", 0.0, 1.0, 0.6, decimals=2, parent=group)
        for row in (self.row_similarity, self.row_smooth, self.row_spill):
            box.addWidget(row)
        self.row_similarity.valueChanged.connect(lambda v: self._apply_chroma("similarity", v))
        self.row_smooth.valueChanged.connect(lambda v: self._apply_chroma("smoothness", v))
        self.row_spill.valueChanged.connect(lambda v: self._apply_chroma("spill", v))
        return group

    def _build_mask_group(self, parent) -> QGroupBox:
        from PySide6.QtWidgets import QCheckBox, QComboBox

        group = QGroupBox("蒙版", parent)
        box = QVBoxLayout(group)
        box.setContentsMargins(8, 8, 8, 8)
        box.setSpacing(2)

        row = QHBoxLayout()
        row.addWidget(QLabel("形状", group))
        self.mask_box = QComboBox(group)
        from ...catalog import masks as _masks

        self.mask_names = [item.get("name") for item in _masks()]
        for name in self.mask_names:
            self.mask_box.addItem(name)
        row.addWidget(self.mask_box, 1)
        box.addLayout(row)
        self.mask_box.currentIndexChanged.connect(lambda _i: self._apply_mask("shape", None))

        self.row_mask_x = SliderRow("中心 X", -1.0, 1.0, 0.0, decimals=2, parent=group)
        self.row_mask_y = SliderRow("中心 Y", -1.0, 1.0, 0.0, decimals=2, parent=group)
        self.row_mask_w = SliderRow("宽", 0.05, 2.0, 1.0, decimals=2, parent=group)
        self.row_mask_h = SliderRow("高", 0.05, 2.0, 1.0, decimals=2, parent=group)
        self.row_mask_feather = SliderRow("羽化", 0.0, 1.0, 0.05, decimals=2, parent=group)
        self.row_mask_rot = SliderRow("旋转", -180.0, 180.0, 0.0, decimals=0, parent=group)
        self.row_mask_round = SliderRow("圆角", 0.0, 1.0, 0.0, decimals=2, parent=group)
        for row_widget in (self.row_mask_x, self.row_mask_y, self.row_mask_w,
                           self.row_mask_h, self.row_mask_feather, self.row_mask_rot,
                           self.row_mask_round):
            box.addWidget(row_widget)
        self.row_mask_x.valueChanged.connect(lambda v: self._apply_mask("centerX", v))
        self.row_mask_y.valueChanged.connect(lambda v: self._apply_mask("centerY", v))
        self.row_mask_w.valueChanged.connect(lambda v: self._apply_mask("width", v))
        self.row_mask_h.valueChanged.connect(lambda v: self._apply_mask("height", v))
        self.row_mask_feather.valueChanged.connect(lambda v: self._apply_mask("feather", v))
        self.row_mask_rot.valueChanged.connect(lambda v: self._apply_mask("rotation", v))
        self.row_mask_round.valueChanged.connect(lambda v: self._apply_mask("roundCorner", v))

        self.mask_invert = QCheckBox("反转", group)
        self.mask_invert.toggled.connect(lambda v: self._apply_mask("invert", bool(v)))
        box.addWidget(self.mask_invert)

        stroke_row = QHBoxLayout()
        stroke_row.addWidget(QLabel("描边", group))
        self.row_stroke_w = SliderRow("宽", 0.0, 20.0, 0.0, decimals=0, parent=group)
        stroke_row.addWidget(self.row_stroke_w, 1)
        box.addLayout(stroke_row)
        self.row_stroke_w.valueChanged.connect(lambda v: self._apply_stroke(int(v)))

        remove = QPushButton("移除蒙版", group)
        remove.clicked.connect(self.remove_mask)
        box.addWidget(remove)
        return group

    def _chroma_ref(self, seg):
        for ref in self.bridge.project.refs_of(seg):
            if getattr(ref, "type", "") == "mask" and str(ref.get("resource_type")) == "chroma":
                return ref
        return None

    def _mask_ref(self, seg):
        for ref in self.bridge.project.refs_of(seg):
            if getattr(ref, "type", "") == "mask" and str(ref.get("resource_type")) != "chroma":
                return ref
        return None

    def _load_mask_controls(self) -> None:
        seg = self._seg
        if seg is None:
            return
        self._loading = True
        chroma = self._chroma_ref(seg)
        self.chroma_on.setChecked(chroma is not None)
        if chroma is not None:
            cfg = dict(chroma.get("config") or {})
            self.chroma_btn.setText(str(cfg.get("key", "#00FF00")))
            self.row_similarity.set_value(float(cfg.get("similarity", 0.25)))
            self.row_smooth.set_value(float(cfg.get("smoothness", 0.12)))
            self.row_spill.set_value(float(cfg.get("spill", 0.6)))
        mask = self._mask_ref(seg)
        if mask is not None:
            cfg = dict(mask.get("config") or {})
            name = str(mask.get("name") or "圆形")
            if name in self.mask_names:
                self.mask_box.setCurrentIndex(self.mask_names.index(name))
            self.row_mask_x.set_value(float(cfg.get("centerX", 0.0)))
            self.row_mask_y.set_value(float(cfg.get("centerY", 0.0)))
            self.row_mask_w.set_value(float(cfg.get("width", 1.0)))
            self.row_mask_h.set_value(float(cfg.get("height", 1.0)))
            self.row_mask_feather.set_value(float(cfg.get("feather", 0.05)))
            self.row_mask_rot.set_value(float(cfg.get("rotation", 0.0)))
            self.row_mask_round.set_value(float(cfg.get("roundCorner", 0.0)))
            self.mask_invert.setChecked(bool(cfg.get("invert", False)))
            stroke = dict(mask.get("stroke") or {})
            self.row_stroke_w.set_value(float(stroke.get("width", 0) or 0))
        self._loading = False

    def _toggle_chroma(self, on: bool) -> None:
        seg = self._seg
        if seg is None or self._loading:
            return
        from ... import builder

        if on:
            builder.add_chroma(self.bridge.project, seg, key=self.chroma_btn.text())
            self.bridge.statusMessage.emit("已启用色度抠图")
        else:
            ref = self._chroma_ref(seg)
            if ref is not None:
                seg.extra_material_refs = [r for r in seg.extra_material_refs if r != ref.id]
                self.bridge.statusMessage.emit("已关闭色度抠图")
        self.bridge.edited.emit()

    def _pick_chroma_colour(self) -> None:
        from PySide6.QtWidgets import QColorDialog

        colour = QColorDialog.getColor(self.chroma_btn.text(), self, "抠掉的颜色")
        if colour.isValid():
            self.chroma_btn.setText(colour.name().upper())
            self._apply_chroma("key", colour.name().upper())

    def _apply_chroma(self, key: str, value) -> None:
        seg = self._seg
        if seg is None or self._loading or not self.chroma_on.isChecked():
            return
        ref = self._chroma_ref(seg)
        if ref is None:
            return
        cfg = dict(ref.get("config") or {})
        cfg[key] = value
        ref["config"] = cfg
        self.bridge.edited.emit()
        self.bridge.statusMessage.emit(f"抠像 {key} = {value}")

    def _apply_mask(self, key: str, value) -> None:
        seg = self._seg
        if seg is None or self._loading:
            return
        from ... import builder

        if key == "shape":
            name = self.mask_box.currentText()
            mask = self._mask_ref(seg)
            if mask is None:
                builder.add_mask(self.bridge.project, seg, name=name)
            else:
                from ...catalog import mask_by_name

                hit = mask_by_name(name) or {}
                mask["name"] = name
                mask["resource_type"] = hit.get("kind", "circle")
                mask["resource_id"] = hit.get("resource_id", "")
            self.bridge.statusMessage.emit(f"蒙版形状：{name}")
            self.bridge.edited.emit()
            return
        mask = self._mask_ref(seg)
        if mask is None:
            builder.add_mask(self.bridge.project, seg, name=self.mask_box.currentText())
            mask = self._mask_ref(seg)
        if mask is None:
            return
        cfg = dict(mask.get("config") or {})
        cfg[key] = value
        mask["config"] = cfg
        self.bridge.edited.emit()

    def _apply_stroke(self, width: int) -> None:
        seg = self._seg
        if seg is None or self._loading:
            return
        from ... import builder

        mask = self._mask_ref(seg)
        if mask is None:
            builder.add_mask(self.bridge.project, seg, name=self.mask_box.currentText())
            mask = self._mask_ref(seg)
        if mask is None:
            return
        stroke = dict(mask.get("stroke") or {})
        stroke["width"] = int(width)
        stroke.setdefault("color", "#FFFFFF")
        mask["stroke"] = stroke
        self.bridge.edited.emit()

    def remove_mask(self) -> None:
        seg = self._seg
        if seg is None:
            return
        mask = self._mask_ref(seg)
        if mask is not None:
            seg.extra_material_refs = [r for r in seg.extra_material_refs if r != mask.id]
            self.bridge.statusMessage.emit("已移除蒙版")
            self.bridge.edited.emit()

    # -- 曲线 · 轮 -------------------------------------------------------- #
    def _build_hsl_group(self, parent) -> QGroupBox:
        from PySide6.QtWidgets import QComboBox

        group = QGroupBox("HSL", parent)
        box = QVBoxLayout(group)
        box.setContentsMargins(8, 8, 8, 8)
        box.setSpacing(4)

        from .color_controls import (ColorSwatchRow, apply_gradient,
                                    gradient_stops)

        from ...color import HSL_BANDS

        self.BAND_LABELS = {"red": "红", "orange": "橙", "yellow": "黄", "green": "绿",
                            "cyan": "青", "blue": "蓝", "purple": "紫", "magenta": "洋红"}

        # 剪映那种"一排色圈"：点哪个色圈就编辑哪个色带
        self.band_swatches = ColorSwatchRow(group)
        self.band_swatches.set_bands([(key, hue) for key, hue in HSL_BANDS])
        box.addWidget(self.band_swatches)

        # 保留原来的下拉（脚本/测试/键盘可达），但不占版面
        self.band_box = QComboBox(group)
        for key, _hue in HSL_BANDS:
            self.band_box.addItem(self.BAND_LABELS.get(key, key), key)
        self.band_box.setVisible(False)
        box.addWidget(self.band_box)

        self.row_hue = SliderRow("色相", -100.0, 100.0, 0.0, decimals=0, parent=group)
        self.row_sat = SliderRow("饱和度", -100.0, 100.0, 0.0, decimals=0, parent=group)
        self.row_lum = SliderRow("亮度", -100.0, 100.0, 0.0, decimals=0, parent=group)
        for item in (self.row_hue, self.row_sat, self.row_lum):
            box.addWidget(item)

        self.band_swatches.bandPicked.connect(self._on_band_picked)
        self.band_box.currentIndexChanged.connect(lambda _i: self._sync_band_ui())
        self.row_hue.valueChanged.connect(lambda v: self._apply_hsl("hue", v))
        self.row_sat.valueChanged.connect(lambda v: self._apply_hsl("sat", v))
        self.row_lum.valueChanged.connect(lambda v: self._apply_hsl("lum", v))
        self._sync_band_ui()
        return group

    # -- HSL 色带联动 ------------------------------------------------------- #
    def _band_hue(self) -> float:
        from ...color import HSL_BANDS

        key = self.band_box.currentData() if hasattr(self, "band_box") else None
        for band_key, hue in HSL_BANDS:
            if band_key == key:
                return float(hue)
        return 0.0

    def _sync_band_ui(self) -> None:
        """色圈/下拉 -> 渐变底色 -> 载入该色带数值。"""
        from .color_controls import apply_gradient, gradient_stops

        key = self.band_box.currentData() if hasattr(self, "band_box") else ""
        if hasattr(self, "band_swatches"):
            self.band_swatches.set_current(str(key or ""))
        hue = self._band_hue()
        apply_gradient(self.row_hue.slider, gradient_stops("hue"))
        apply_gradient(self.row_sat.slider, gradient_stops("sat", hue))
        apply_gradient(self.row_lum.slider, gradient_stops("lum"))
        self.load_values()

    def _on_band_picked(self, key: str) -> None:
        index = self.band_box.findData(key)
        if index >= 0 and index != self.band_box.currentIndex():
            self.band_box.setCurrentIndex(index)     # 触发 _sync_band_ui
        else:
            self._sync_band_ui()

    def _build_curve_group(self, parent) -> QGroupBox:
        from PySide6.QtWidgets import QComboBox

        from .color_controls import CurveWidget

        group = QGroupBox("色调曲线", parent)
        box = QVBoxLayout(group)
        box.setContentsMargins(8, 8, 8, 8)
        box.setSpacing(4)

        row = QHBoxLayout()
        row.addWidget(QLabel("通道", group))
        self.channel_box = QComboBox(group)
        for key, label in (("rgb", "RGB"), ("r", "红"), ("g", "绿"), ("b", "蓝")):
            self.channel_box.addItem(label, key)
        row.addWidget(self.channel_box, 1)
        box.addLayout(row)

        self.curve = CurveWidget(parent=group)
        self.curve.setFixedHeight(200)
        box.addWidget(self.curve)

        reset = QPushButton("曲线复位", group)
        reset.setMinimumHeight(26)
        reset.clicked.connect(self.reset_curve)
        box.addWidget(reset)

        self.channel_box.currentIndexChanged.connect(lambda _i: self.load_values())
        self.curve.changed.connect(self._apply_curve)
        return group

    def _build_wheel_group(self, parent) -> QGroupBox:
        from PySide6.QtWidgets import QComboBox

        from .color_controls import WheelWidget

        group = QGroupBox("色轮", parent)
        box = QVBoxLayout(group)
        box.setContentsMargins(8, 8, 8, 8)
        box.setSpacing(4)

        row = QHBoxLayout()
        row.addWidget(QLabel("范围", group))
        self.wheel_box = QComboBox(group)
        for key, label in (("lift", "阴影"), ("gamma", "中间调"), ("gain", "高光")):
            self.wheel_box.addItem(label, key)
        row.addWidget(self.wheel_box, 1)
        box.addLayout(row)

        self.wheel = WheelWidget(group)
        box.addWidget(self.wheel)

        reset = QPushButton("色轮复位", group)
        reset.clicked.connect(self.reset_wheel)
        box.addWidget(reset)

        self.wheel_box.currentIndexChanged.connect(lambda _i: self.load_values())
        self.wheel.changed.connect(self._apply_wheel)
        return group

    # -- 曲线·轮的读写 ----------------------------------------------------- #
    def _ref_of(self, seg, type_name: str):
        try:
            refs = self.bridge.project.refs_of(seg)
        except Exception:                              # noqa: BLE001
            return None
        for ref in refs:
            if getattr(ref, "type", "") == type_name:
                return ref
        return None

    def _band_key(self) -> str:
        return str(self.band_box.currentData()) if hasattr(self, "band_box") else "red"

    def _channel_key(self) -> str:
        return str(self.channel_box.currentData()) if hasattr(self, "channel_box") else "rgb"

    def _wheel_key(self) -> str:
        return str(self.wheel_box.currentData()) if hasattr(self, "wheel_box") else "lift"

    def _load_color_controls(self) -> None:
        seg = self._seg
        if seg is None:
            return
        self._loading = True
        hsl = self._ref_of(seg, "hsl")
        bands = dict(hsl.get("bands") or {}) if hsl is not None else {}
        band = bands.get(self._band_key()) or {}
        self.row_hue.set_value(float(band.get("hue", 0.0) or 0.0))
        self.row_sat.set_value(float(band.get("sat", 0.0) or 0.0) * 100.0)
        self.row_lum.set_value(float(band.get("lum", 0.0) or 0.0) * 100.0)

        curve = self._ref_of(seg, "curve")
        curves = dict(curve.get("curves") or {}) if curve is not None else {}
        points = curves.get(self._channel_key()) or [(0.0, 0.0), (1.0, 1.0)]
        self.curve.set_points(points)

        wheel = self._ref_of(seg, "color_wheel")
        wheels = dict(wheel.get("wheels") or {}) if wheel is not None else {}
        value = wheels.get(self._wheel_key()) or (0.0, 0.0, 0.0)
        self.wheel.set_rgb(value)
        self._loading = False

    def _color_state(self) -> dict:
        seg = self._seg
        if seg is None:
            return {}
        hsl = self._ref_of(seg, "hsl")
        curve = self._ref_of(seg, "curve")
        wheel = self._ref_of(seg, "color_wheel")
        return {
            "hsl": dict(hsl.get("bands") or {}) if hsl is not None else {},
            "curve": dict(curve.get("curves") or {}) if curve is not None else {},
            "wheel": dict(wheel.get("wheels") or {}) if wheel is not None else {},
        }

    def _apply_hsl(self, key: str, value: float) -> None:
        if self._seg is None or self._loading:
            return
        before = self._color_state()
        material = self._ref_of(self._seg, "hsl")
        if material is None:
            from ... import builder

            material = builder.set_hsl(self.bridge.project, self._seg, bands={})
        bands = dict(material.get("bands") or {})
        band = dict(bands.get(self._band_key()) or {})
        band[key] = float(value) / (1.0 if key == "hue" else 100.0)
        bands[self._band_key()] = band
        material["bands"] = bands
        self.bridge.record_color(self._seg, before, self._color_state(), "HSL")
        self.bridge.edited.emit()
        self.bridge.statusMessage.emit(
            f"HSL {self.BAND_LABELS.get(self._band_key(), '')}：色相 {band.get('hue', 0):+.0f} · "
            f"饱和 {band.get('sat', 0) * 100:+.0f} · 明度 {band.get('lum', 0) * 100:+.0f}")

    def _apply_curve(self, points) -> None:
        if self._seg is None or self._loading:
            return
        before = self._color_state()
        material = self._ref_of(self._seg, "curve")
        if material is None:
            from ... import builder

            material = builder.set_curves(self.bridge.project, self._seg)
        curves = dict(material.get("curves") or {})
        curves[self._channel_key()] = [[float(x), float(y)] for x, y in points]
        material["curves"] = curves
        self.bridge.record_color(self._seg, before, self._color_state(), "曲线")
        self.bridge.edited.emit()

    def _apply_wheel(self, r: float, g: float, b: float) -> None:
        if self._seg is None or self._loading:
            return
        before = self._color_state()
        material = self._ref_of(self._seg, "color_wheel")
        if material is None:
            from ... import builder

            material = builder.set_wheels(self.bridge.project, self._seg)
        wheels = dict(material.get("wheels") or {})
        wheels[self._wheel_key()] = [float(r), float(g), float(b)]
        material["wheels"] = wheels
        self.bridge.record_color(self._seg, before, self._color_state(), "色轮")
        self.bridge.edited.emit()

    def reset_curve(self) -> None:
        if self._seg is None:
            return
        self.curve.reset()

    def reset_wheel(self) -> None:
        if self._seg is None:
            return
        self.wheel.set_rgb((0.0, 0.0, 0.0))
        self._apply_wheel(0.0, 0.0, 0.0)

    def _build_text_group(self, parent) -> QGroupBox:
        """文字页：按剪映的分组顺序排（基础→字形→排版→描边→阴影→背景→透明度）。"""
        from PySide6.QtWidgets import QComboBox, QPlainTextEdit

        group = QGroupBox("文字", parent)
        form = QVBoxLayout(group)
        form.setContentsMargins(8, 6, 8, 8)
        form.setSpacing(3)

        def section(title: str) -> None:
            label = QLabel(title, group)
            label.setStyleSheet("color:#7f8792;font-weight:600;padding-top:6px;")
            form.addWidget(label)

        # 内容
        self.text_edit = QPlainTextEdit(group)
        self.text_edit.setFixedHeight(58)
        self.text_edit.textChanged.connect(self._on_text_changed)
        form.addWidget(self.text_edit)

        # 基础：字体 / 字号 / 颜色
        section("基础")
        font_row = QHBoxLayout()
        font_row.addWidget(QLabel("字体", group))
        self.font_combo = QComboBox(group)
        from ... import text as text_mod

        seen = set()
        for folder in text_mod.FONT_DIRS:
            if not os.path.isdir(folder):
                continue
            for filename in sorted(os.listdir(folder)):
                if not filename.lower().endswith((".ttf", ".ttc", ".otf")):
                    continue
                if filename in seen:
                    continue
                seen.add(filename)
                self.font_combo.addItem(os.path.splitext(filename)[0], filename)
        self.font_combo.currentIndexChanged.connect(self._on_font_changed)
        font_row.addWidget(self.font_combo, 1)
        form.addLayout(font_row)

        self.row_font_size = SliderRow("字号", 6.0, 60.0, 12.0, decimals=0, parent=group)
        form.addWidget(self.row_font_size)

        colour_row = QHBoxLayout()
        colour_row.addWidget(QLabel("颜色", group))
        self.colour_btn = QPushButton("#FFFFFF", group)
        self.colour_btn.clicked.connect(self._pick_text_colour)
        colour_row.addWidget(self.colour_btn, 1)
        form.addLayout(colour_row)

        # 字形
        section("字形")
        self.row_bold = SliderRow("粗体", 0.0, 0.3, 0.0, decimals=2, parent=group)
        self.row_italic = SliderRow("斜体", 0.0, 30.0, 0.0, decimals=0, parent=group)
        self.chk_underline = QCheckBox("下划线", group)
        self.chk_underline.toggled.connect(self._on_underline_toggled)
        for widget in (self.row_bold, self.row_italic, self.chk_underline):
            form.addWidget(widget)

        # 排版
        section("排版")
        align_row = QHBoxLayout()
        align_row.addWidget(QLabel("对齐", group))
        self.align_box = QComboBox(group)
        for label, value in (("左对齐", 0), ("居中", 1), ("右对齐", 2)):
            self.align_box.addItem(label, value)
        self.align_box.currentIndexChanged.connect(self._on_align_changed)
        align_row.addWidget(self.align_box, 1)
        self.vertical_box = QCheckBox("竖排", group)
        self.vertical_box.toggled.connect(self._on_vertical_toggled)
        align_row.addWidget(self.vertical_box)
        form.addLayout(align_row)

        self.row_line_spacing = SliderRow("行距", 0.8, 2.0, 1.0, decimals=2, parent=group)
        self.row_letter_spacing = SliderRow("字距", -10.0, 20.0, 0.0, decimals=1, parent=group)
        form.addWidget(self.row_line_spacing)
        form.addWidget(self.row_letter_spacing)

        # 描边（宽度 / 颜色 / 不透明度 放一起）
        section("描边")
        self.row_stroke = SliderRow("宽度", 0.0, 12.0, 0.0, decimals=1, parent=group)
        form.addWidget(self.row_stroke)
        stroke_row = QHBoxLayout()
        stroke_row.addWidget(QLabel("颜色", group))
        self.btn_border_colour = QPushButton("#000000", group)
        self.btn_border_colour.clicked.connect(
            lambda: self._pick_colour("border_color", self.btn_border_colour, "描边色"))
        stroke_row.addWidget(self.btn_border_colour, 1)
        form.addLayout(stroke_row)
        self.row_border_alpha = SliderRow("不透明度", 0.0, 100.0, 100.0, decimals=0,
                                          suffix="%", parent=group)
        form.addWidget(self.row_border_alpha)

        # 阴影（强度 / 颜色 / 距离 / 角度）
        section("阴影")
        self.row_shadow = SliderRow("强度", 0.0, 100.0, 0.0, decimals=0,
                                    suffix="%", parent=group)
        form.addWidget(self.row_shadow)
        shadow_row = QHBoxLayout()
        shadow_row.addWidget(QLabel("颜色", group))
        self.btn_shadow_colour = QPushButton("#000000", group)
        self.btn_shadow_colour.clicked.connect(
            lambda: self._pick_colour("shadow_color", self.btn_shadow_colour, "阴影色"))
        shadow_row.addWidget(self.btn_shadow_colour, 1)
        form.addLayout(shadow_row)
        self.row_shadow_distance = SliderRow("距离", 0.0, 1.0, 0.1, decimals=2, parent=group)
        self.row_shadow_angle = SliderRow("角度", 0.0, 360.0, 45.0, decimals=0, parent=group)
        form.addWidget(self.row_shadow_distance)
        form.addWidget(self.row_shadow_angle)

        # 背景框
        section("背景框")
        bg_row = QHBoxLayout()
        bg_row.addWidget(QLabel("颜色", group))
        self.bg_colour_btn = QPushButton("无", group)
        self.bg_colour_btn.clicked.connect(self._pick_bg_colour)
        bg_row.addWidget(self.bg_colour_btn, 1)
        form.addLayout(bg_row)
        self.row_bg_alpha = SliderRow("不透明度", 0.0, 100.0, 0.0, decimals=0,
                                      suffix="%", parent=group)
        self.row_bg_radius = SliderRow("圆角", 0.0, 40.0, 0.0, decimals=0, parent=group)
        form.addWidget(self.row_bg_alpha)
        form.addWidget(self.row_bg_radius)

        # 透明度
        section("透明度")
        self.row_text_alpha = SliderRow("文字", 0.0, 100.0, 100.0, decimals=0,
                                        suffix="%", parent=group)
        self.row_global_alpha = SliderRow("整体", 0.0, 100.0, 100.0, decimals=0,
                                          suffix="%", parent=group)
        form.addWidget(self.row_text_alpha)
        form.addWidget(self.row_global_alpha)

        # 逐字高亮
        section("逐字高亮")
        hl_row = QHBoxLayout()
        self.chk_highlight = QCheckBox("开启", group)
        self.chk_highlight.toggled.connect(self._on_highlight_toggled)
        hl_row.addWidget(self.chk_highlight)
        self.btn_highlight_colour = QPushButton("#FFE066", group)
        self.btn_highlight_colour.clicked.connect(
            lambda: self._pick_colour("highlight_color", self.btn_highlight_colour, "高亮色"))
        hl_row.addWidget(self.btn_highlight_colour, 1)
        form.addLayout(hl_row)

        # 附加：朗读 / 花字
        section("附加")
        self.btn_speak = QPushButton("朗读成音频", group)
        self.btn_speak.setToolTip("用本机 SAPI 声音朗读这段文字，放到音频轨（离线，非云端音色）")
        self.btn_speak.clicked.connect(self._speak_text)
        form.addWidget(self.btn_speak)

        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("花字", group))
        self.preset_box = QComboBox(group)
        from ... import textstyles

        self.preset_box.addItem("（不套用）")
        for name in textstyles.names():
            self.preset_box.addItem(name)
        self.preset_box.currentIndexChanged.connect(self._on_preset_changed)
        preset_row.addWidget(self.preset_box, 1)
        form.addLayout(preset_row)

        # 信号（顺序集中放，方便对照）
        self.row_font_size.valueChanged.connect(lambda v: self._apply_text("font_size", v))
        self.row_bold.valueChanged.connect(lambda v: self._apply_text("bold_width", v))
        self.row_italic.valueChanged.connect(lambda v: self._apply_text("italic_degree", v))
        self.row_line_spacing.valueChanged.connect(lambda v: self._apply_text("line_spacing", v))
        self.row_letter_spacing.valueChanged.connect(
            lambda v: self._apply_text("letter_spacing", v))
        self.row_stroke.valueChanged.connect(lambda v: self._apply_text("border_width", v))
        self.row_border_alpha.valueChanged.connect(
            lambda v: self._apply_text("border_alpha", float(v) / 100.0))
        self.row_shadow.valueChanged.connect(
            lambda v: self._apply_text("shadow_alpha", v / 100.0))
        self.row_shadow_distance.valueChanged.connect(
            lambda v: self._apply_text("shadow_distance", v))
        self.row_shadow_angle.valueChanged.connect(lambda v: self._apply_text("shadow_angle", v))
        self.row_bg_alpha.valueChanged.connect(
            lambda v: self._apply_text("background_alpha", float(v) / 100.0))
        self.row_bg_radius.valueChanged.connect(
            lambda v: self._apply_text("background_round_radius", v))
        self.row_text_alpha.valueChanged.connect(
            lambda v: self._apply_text("text_alpha", float(v) / 100.0))
        self.row_global_alpha.valueChanged.connect(
            lambda v: self._apply_text("global_alpha", float(v) / 100.0))
        return group

    def _on_font_changed(self, _index: int) -> None:
        if self._loading or not hasattr(self, "font_combo"):
            return
        name = self.font_combo.currentData()
        if name:
            self._apply_text("font_name", name)

    def _load_font_choice(self, mat_text) -> None:
        if not hasattr(self, "font_combo"):
            return
        current = str(mat_text.get("font_name") or "") if mat_text is not None else ""
        index = self.font_combo.findData(current) if current else -1
        self.font_combo.setCurrentIndex(index if index >= 0 else 0)

    def _on_align_changed(self, _index: int) -> None:
        if self._loading or not hasattr(self, "align_box"):
            return
        value = self.align_box.currentData()
        if value is not None:
            self._apply_text("alignment", int(value))

    def _on_vertical_toggled(self, checked: bool) -> None:
        if self._loading or not hasattr(self, "vertical_box"):
            return
        self._apply_text("vertical", bool(checked))

    def _load_align_choice(self, mat_text) -> None:
        if not hasattr(self, "align_box"):
            return
        alignment = int(mat_text.get("alignment") or 1) if mat_text is not None else 1
        index = self.align_box.findData(max(0, min(2, alignment)))
        self.align_box.setCurrentIndex(index if index >= 0 else 1)
        self.vertical_box.setChecked(bool(mat_text.get("vertical")) if mat_text is not None else False)

    def _on_highlight_toggled(self, checked: bool) -> None:
        if self._loading:
            return
        seg = self._seg
        mat_text = self._text_mat(seg)
        if seg is None or mat_text is None:
            return
        if not checked:
            self._apply_text("highlight_mode", 0)
            self._apply_text("words", [])
            return
        from ...subtitles import split_words

        content = str(mat_text.get("content") or "")
        tokens = split_words(content)
        duration = int(getattr(seg.target_timerange, "duration", 0) or 0)
        if not tokens or duration <= 0:
            self.bridge.statusMessage.emit("逐字高亮：先把文字内容填好")
            return
        span = duration / len(tokens)
        words = [{"start_time": int(i * span), "end_time": int((i + 1) * span),
                  "word": token} for i, token in enumerate(tokens)]
        self._apply_text("words", words)
        self._apply_text("highlight_mode", 1)
        self.bridge.statusMessage.emit(f"逐字高亮：{len(words)} 个字/词")

    def _pick_colour(self, field: str, button, title: str) -> None:
        from PySide6.QtWidgets import QColorDialog

        mat_text = self._text_material()
        current = str(mat_text.get(field) or "#000000") if mat_text else "#000000"
        chosen = QColorDialog.getColor(QColor(current), self, title)
        if not chosen.isValid():
            return
        name = chosen.name()
        self._apply_text(field, name)
        button.setText(name)

    def _pick_bg_colour(self) -> None:
        from PySide6.QtWidgets import QColorDialog

        mat_text = self._text_material()
        current = str(mat_text.get("background_color") or "#000000") if mat_text else "#000000"
        chosen = QColorDialog.getColor(QColor(current), self, "背景色")
        if not chosen.isValid():
            return
        name = chosen.name()
        self._apply_text("background_color", name)
        self.bg_colour_btn.setText(name)
        if self.row_bg_alpha.spin.value() < 1.0:
            self.row_bg_alpha.set_value(60.0, emit=True)

    def _on_underline_toggled(self, checked: bool) -> None:
        if self._loading:
            return
        self._apply_text("underline", bool(checked))

    def _speak_text(self) -> None:
        seg = self._seg
        if seg is None:
            return
        new_seg = self.bridge.speak_text(seg)
        if new_seg is not None:
            self.bridge.select(new_seg)

    def _text_mat(self, seg):
        try:
            mat = self.bridge.project.material(seg.material_id)
        except Exception:                              # noqa: BLE001
            return None
        return mat if (mat is not None and mat.type == "text") else None

    def _text_state(self, seg) -> dict:
        mat = self._text_mat(seg)
        if mat is None:
            return {}
        return {"content": str(mat.get("content") or ""),
                "font_size": float(mat.get("font_size") or 12.0),
                "text_color": str(mat.get("text_color") or "#FFFFFF"),
                "border_width": float(mat.get("border_width") or 0.0),
                "shadow_alpha": float(mat.get("shadow_alpha") or 0.0)}

    def _on_text_changed(self) -> None:
        if self._seg is None or self._loading:
            return
        if self._text_mat(self._seg) is None:
            return
        self._apply_text("content", self.text_edit.toPlainText())

    def _pick_text_colour(self) -> None:
        from PySide6.QtWidgets import QColorDialog

        seg = self._seg
        if seg is None or self._text_mat(seg) is None:
            return
        current = str(self._text_mat(seg).get("text_color") or "#FFFFFF")
        colour = QColorDialog.getColor(current, self, "文字颜色")
        if colour.isValid():
            self._apply_text("text_color", colour.name().upper())

    def _on_preset_changed(self, index: int) -> None:
        from ... import textstyles

        seg = self._seg
        if seg is None or self._loading or index <= 0:
            return
        mat = self._text_mat(seg)
        if mat is None:
            return
        before = self._text_state(seg)
        preset = textstyles.names()[index - 1]
        textstyles.apply(mat, preset)
        after = self._text_state(seg)
        self.bridge.record_text(seg, before, after, f"套花字：{preset}")
        self.load_values()
        self.bridge.edited.emit()
        self.bridge.statusMessage.emit(f"已套用花字：{preset}")
        self.preset_box.blockSignals(True)
        self.preset_box.setCurrentIndex(0)
        self.preset_box.blockSignals(False)

    def _apply_text(self, key: str, value) -> None:
        seg = self._seg
        if seg is None or self._loading:
            return
        mat = self._text_mat(seg)
        if mat is None:
            return
        before = self._text_state(seg)
        mat[key] = value
        after = self._text_state(seg)
        self.bridge.record_text(seg, before, after, "编辑文字")
        if key == "text_color":
            self.colour_btn.setText(str(value))
        self.bridge.statusMessage.emit(
            f"文字：字号 {after['font_size']:.0f} · 描边 {after['border_width']:.1f} · "
            f"阴影 {after['shadow_alpha']:.0%} · 颜色 {after['text_color']}")
        self.bridge.edited.emit()

    def _build_audio_group(self, parent) -> QGroupBox:
        group = QGroupBox("声音", parent)
        form = QVBoxLayout(group)
        form.setContentsMargins(8, 8, 8, 8)
        form.setSpacing(2)

        self.row_volume = SliderRow("音量", 0.0, 200.0, 100.0, decimals=0, suffix="%",
                                    parent=group)
        self.row_fade_in = SliderRow("淡入", 0.0, 3000.0, 0.0, decimals=0, suffix=" ms",
                                     parent=group)
        self.row_fade_out = SliderRow("淡出", 0.0, 3000.0, 0.0, decimals=0, suffix=" ms",
                                      parent=group)
        self.row_denoise = SliderRow("降噪", 0.0, 100.0, 0.0, decimals=0, suffix="%",
                                     parent=group)
        for row in (self.row_volume, self.row_fade_in, self.row_fade_out, self.row_denoise):
            form.addWidget(row)

        self.row_eq_low = SliderRow("低频", -12.0, 12.0, 0.0, decimals=1,
                                    suffix=" dB", parent=group)
        self.row_eq_mid = SliderRow("中频", -12.0, 12.0, 0.0, decimals=1,
                                    suffix=" dB", parent=group)
        self.row_eq_high = SliderRow("高频", -12.0, 12.0, 0.0, decimals=1,
                                     suffix=" dB", parent=group)
        self.row_eq_low.setToolTip("120 Hz 低架")
        self.row_eq_mid.setToolTip("1 kHz 中频")
        self.row_eq_high.setToolTip("6 kHz 高架")
        for row in (self.row_eq_low, self.row_eq_mid, self.row_eq_high):
            form.addWidget(row)
            row.valueChanged.connect(lambda _v: self._apply_audio(eq=self._eq_tuple()))

        pitch_row = QHBoxLayout()
        pitch_row.addWidget(QLabel("变声", group))
        from PySide6.QtWidgets import QComboBox

        self.pitch_box = QComboBox(group)
        self.PITCH_PRESETS = (("原声", 1.0), ("升调", 1.15), ("降调", 0.87), ("低沉", 0.75))
        for label, _value in self.PITCH_PRESETS:
            self.pitch_box.addItem(label)
        pitch_row.addWidget(self.pitch_box, 1)
        form.addLayout(pitch_row)

        reset = QPushButton("声音复位", group)
        reset.clicked.connect(self.reset_audio)
        form.addWidget(reset)

        self.row_volume.valueChanged.connect(
            lambda v: self._apply_audio(volume=v / 100.0))
        self.row_fade_in.valueChanged.connect(
            lambda v: self._apply_audio(fade_in_us=int(v * 1000)))
        self.row_fade_out.valueChanged.connect(
            lambda v: self._apply_audio(fade_out_us=int(v * 1000)))
        self.row_denoise.valueChanged.connect(
            lambda v: self._apply_audio(denoise=v / 100.0))
        self.pitch_box.currentIndexChanged.connect(
            lambda idx: self._apply_audio(pitch=self.PITCH_PRESETS[idx][1]))
        return group

    def _audio_state(self, seg) -> dict:
        from ... import audio as audio_mod

        fade_in, fade_out = audio_mod._fade_durations(self.bridge.project, seg)
        denoise, pitch, eq = audio_mod._audio_effects(self.bridge.project, seg)
        return {"volume": float(seg.volume), "fade_in_us": int(fade_in),
                "fade_out_us": int(fade_out), "denoise": float(denoise),
                "pitch": float(pitch), "eq": tuple(float(x) for x in (eq or (0, 0, 0)))}

    def _eq_tuple(self):
        # SliderRow 没有 value()，值在 spin 上
        return (float(self.row_eq_low.spin.value()), float(self.row_eq_mid.spin.value()),
                float(self.row_eq_high.spin.value()))

    def _apply_audio(self, **changes) -> None:
        seg = self._seg
        if seg is None or self._loading:
            return
        from ... import builder

        before = self._audio_state(seg)
        if "volume" in changes:
            seg.volume = max(0.0, float(changes["volume"]))
        if "fade_in_us" in changes or "fade_out_us" in changes:
            builder.set_audio_fade(self.bridge.project, seg,
                                   fade_in_us=changes.get("fade_in_us"),
                                   fade_out_us=changes.get("fade_out_us"))
        if "denoise" in changes or "pitch" in changes or "eq" in changes:
            builder.set_audio_effects(self.bridge.project, seg,
                                      denoise=changes.get("denoise"),
                                      pitch=changes.get("pitch"),
                                      eq=changes.get("eq"))
        after = self._audio_state(seg)
        self.bridge.record_audio(seg, before, after, "调整声音")
        self._update_volume_db(seg)
        self.bridge.statusMessage.emit(
            f"声音：音量 {seg.volume:.0%} · 淡入 {after['fade_in_us'] / 1000:.0f}ms · "
            f"淡出 {after['fade_out_us'] / 1000:.0f}ms · 降噪 {after['denoise']:.0%} · "
            f"变声 {after['pitch']:.2f}")

    def _update_volume_db(self, seg) -> None:
        """剪映音频页显示 dB，不只显示百分比。"""
        label = getattr(self, "volume_db", None)
        if label is None or seg is None:
            return
        import math

        value = float(seg.volume)
        if value <= 1e-6:
            label.setText("音量 -∞ dB（静音）")
        else:
            label.setText(f"音量 {20.0 * math.log10(value):+.1f} dB")

    def reset_audio(self) -> None:
        seg = self._seg
        if seg is None:
            return
        from ... import builder

        before = self._audio_state(seg)
        seg.volume = 1.0
        builder.set_audio_fade(self.bridge.project, seg, fade_in_us=0, fade_out_us=0)
        builder.set_audio_effects(self.bridge.project, seg, denoise=0.0, pitch=1.0)
        after = self._audio_state(seg)
        self.bridge.record_audio(seg, before, after, "声音复位")
        self.load_values()
        self.bridge.edited.emit()

    def _adjust_of(self, seg) -> dict:
        try:
            mat = self.bridge.project.material(seg.material_id)
        except Exception:                              # noqa: BLE001
            return {}
        return dict(mat.get("adjust") or {}) if mat is not None else {}

    def _apply_adjust(self, key: str, value: float) -> None:
        seg = self._seg
        if seg is None or self._loading or self._seg_is_audio():
            return
        from ... import builder

        before = self._adjust_of(seg)
        builder.set_adjust(self.bridge.project, seg, **{key: float(value)})
        after = self._adjust_of(seg)
        self.bridge.record_adjust(seg, before, after, "调节")
        changed = "，".join(f"{ADJUST_LABELS[k]} {v:+.0f}" for k, v in after.items() if v)
        self.bridge.statusMessage.emit(f"调节：{changed or '无变化'}")

    def reset_adjust(self) -> None:
        seg = self._seg
        if seg is None or self._seg_is_audio():
            return
        before = self._adjust_of(seg)
        mat = self.bridge.project.material(seg.material_id)
        if mat is not None:
            mat["adjust"] = {}
        after = self._adjust_of(seg)
        self.bridge.record_adjust(seg, before, after, "调节归零")
        self.load_values()
        self.bridge.edited.emit()

    # -- 选择 ------------------------------------------------------------- #
    def tab_index(self, name: str) -> int:
        """页签名 → 页签序号（按名字找，不写死序号）。"""
        try:
            return list(self.TABS).index(name)
        except ValueError:
            return 0

    def focus(self, page_name: str, keyword: str = "") -> bool:
        """切到某一页，并尽量滚到含 ``keyword`` 的那一组（工具栏/右键菜单用）。

        匹配规则：先看分组标题，再看分组里控件的文字（滑杆名就是用户看到的名字，
        例如「音量」在「声音」组里）。找不到就只切页，返回 False。
        """
        index = self.tab_index(page_name)
        self.tabs.setCurrentIndex(index)
        scroll = self.tabs.widget(index)
        if not keyword or scroll is None:
            return True
        page = scroll.widget() if hasattr(scroll, "widget") else None
        if page is None:
            return False
        from PySide6.QtWidgets import QAbstractScrollArea, QGroupBox, QLabel

        def _scroll_to(group) -> None:
            if isinstance(scroll, QAbstractScrollArea):
                scroll.ensureWidgetVisible(group, 12, 12)

        for group in page.findChildren(QGroupBox):
            if keyword in group.title():
                _scroll_to(group)
                return True
        for label in page.findChildren(QLabel):
            if keyword not in label.text():
                continue
            parent = label.parent()
            while parent is not None and not isinstance(parent, QGroupBox):
                parent = parent.parent()
            if parent is not None:
                _scroll_to(parent)
                return True
        return False

    def _transition_of(self):
        seg = self._seg
        if seg is None:
            return None
        try:
            return self.bridge.project.first_ref(seg, "transition")
        except Exception:                              # noqa: BLE001
            return None

    def _load_transition_duration(self) -> None:
        ref = self._transition_of()
        if not hasattr(self, "transition_spin"):
            return
        self._loading = True
        try:
            if ref is None:
                self.transition_spin.setEnabled(False)
            else:
                self.transition_spin.setEnabled(True)
                self.transition_spin.setValue(max(0.1, float(ref.get("duration") or 500000) / 1e6))
        finally:
            self._loading = False

    def _apply_transition_duration(self, seconds: float) -> None:
        if self._loading:
            return
        ref = self._transition_of()
        if ref is None:
            return
        # Material 的字段都存在 extra 字典里（get/extra 是唯一读写通道）
        ref.extra["duration"] = int(round(float(seconds) * 1e6))
        self.bridge.touch()
        self.bridge.edited.emit()
        self.bridge.statusMessage.emit(f"转场时长 {float(seconds):.1f} 秒")

    def on_selection(self, seg) -> None:
        if seg is None:
            self._seg = None
            self.tabs.hide()
            self.empty.show()
            self.title.setText("属性")
            if hasattr(self, "blend_combo"):
                self._load_blend_mode()
            return
        self._seg = seg
        self.empty.hide()
        self._load_transition_duration()
        self.tabs.show()
        kind = self._kind_of(seg)
        self.title.setText(f"属性 · {kind}")
        # 选中文字/音频片段时自动切到对应的页（剪映也是这样）
        if kind == "文字":
            self.tabs.setCurrentIndex(self.tab_index("文字"))
        elif kind == "音频":
            self.tabs.setCurrentIndex(self.tab_index("音频"))
        else:
            self.tabs.setCurrentIndex(self.tab_index(self.DEFAULT_TAB))
        self.load_values()

    def _kind_of(self, seg) -> str:
        for track in self.bridge.project.tracks:
            for item in track.segments:
                if item is seg:
                    return {"video": "视频", "text": "文字", "audio": "音频",
                            "sticker": "贴纸", "effect": "特效"}.get(track.type, track.type)
        return "片段"

    def load_values(self) -> None:
        seg = self._seg
        if seg is None:
            return
        if hasattr(self, "blend_combo"):
            self._load_blend_mode()
        mat_text = self._text_mat(seg)
        if mat_text is not None and hasattr(self, "text_edit"):
            self._loading = True
            self.text_edit.setPlainText(str(mat_text.get("content") or ""))
            self.row_font_size.set_value(float(mat_text.get("font_size") or 12.0))
            self.row_bold.set_value(float(mat_text.get("bold_width") or 0.0))
            self.row_italic.set_value(float(mat_text.get("italic_degree") or 0.0))
            self.row_line_spacing.set_value(float(mat_text.get("line_spacing") or 1.0))
            self.row_letter_spacing.set_value(float(mat_text.get("letter_spacing") or 0.0))
            self.chk_underline.setChecked(bool(mat_text.get("underline")))
            colour = str(mat_text.get("background_color") or "")
            self.bg_colour_btn.setText(colour or "无")
            self.row_bg_alpha.set_value(float(mat_text.get("background_alpha") or 0.0) * 100.0)
            self.row_bg_radius.set_value(float(mat_text.get("background_round_radius") or 0.0))
            self.row_text_alpha.set_value(float(mat_text.get("text_alpha", 1.0) or 1.0) * 100.0)
            self.row_global_alpha.set_value(float(mat_text.get("global_alpha", 1.0) or 1.0) * 100.0)
            self.btn_highlight_colour.setText(str(mat_text.get("highlight_color") or "#FFE066"))
            self.chk_highlight.setChecked(bool(mat_text.get("highlight_mode") or mat_text.get("words")))
            self.btn_border_colour.setText(str(mat_text.get("border_color") or "#000000"))
            self.row_border_alpha.set_value(float(mat_text.get("border_alpha", 1.0) or 1.0) * 100.0)
            self.btn_shadow_colour.setText(str(mat_text.get("shadow_color") or "#000000"))
            self.row_shadow_distance.set_value(float(mat_text.get("shadow_distance") or 0.0))
            self.row_shadow_angle.set_value(float(mat_text.get("shadow_angle") or 45.0))
            self._load_font_choice(mat_text)
            self._load_align_choice(mat_text)
            self.row_stroke.set_value(float(mat_text.get("border_width") or 0.0))
            self.row_shadow.set_value(float(mat_text.get("shadow_alpha") or 0.0) * 100.0)
            self.colour_btn.setText(str(mat_text.get("text_color") or "#FFFFFF"))
            self._loading = False

        if self._seg_is_audio():
            self._loading = True
            state = self._audio_state(seg)
            self.row_volume.set_value(state["volume"] * 100.0)
            self.row_fade_in.set_value(state["fade_in_us"] / 1000.0)
            self.row_fade_out.set_value(state["fade_out_us"] / 1000.0)
            self.row_denoise.set_value(state["denoise"] * 100.0)
            self._update_volume_db(seg)
            for i, (_label, value) in enumerate(self.PITCH_PRESETS):
                if abs(value - state["pitch"]) < 0.01:
                    self.pitch_box.setCurrentIndex(i)
                    break
            self._loading = False
            return
        clip = seg.clip
        self._loading = True
        self.row_x.set_value(float(clip.tx))
        self.row_y.set_value(float(clip.ty))
        self.row_scale.set_value(float(clip.scale_x) * 100.0)
        self.row_rotation.set_value(float(clip.rotation))
        self.row_alpha.set_value(float(clip.alpha) * 100.0)
        adjust = self._adjust_of(seg)
        for key, row in getattr(self, "adjust_rows", {}).items():
            row.set_value(float(adjust.get(key, 0.0)))
        if hasattr(self, "curve"):
            self._load_color_controls()
        if hasattr(self, "row_crop_left"):
            self._load_crop()
        if hasattr(self, "kf_rows"):
            self._refresh_kf_buttons()
        if hasattr(self, "row_speed"):
            self._load_speed()
        if hasattr(self, "chroma_on"):
            self._load_mask_controls()
        if hasattr(self, "effect_box"):
            self._load_effects()
        if hasattr(self, "anim_boxes"):
            self._load_animations()
        if hasattr(self, "row_volume"):
            state = self._audio_state(seg)
            self.row_volume.set_value(state["volume"] * 100.0)
            self.row_fade_in.set_value(state["fade_in_us"] / 1000.0)
            self.row_fade_out.set_value(state["fade_out_us"] / 1000.0)
            self.row_denoise.set_value(state["denoise"] * 100.0)
        self._loading = False

    def _seg_is_audio(self) -> bool:
        for track in self.bridge.project.tracks:
            if self._seg in track.segments:
                return track.type == "audio"
        return False

    # -- 写回 ------------------------------------------------------------- #
    def _apply(self, field: str, value: float) -> None:
        seg = self._seg
        if seg is None or self._loading or self._seg_is_audio():
            return
        before = self._snapshot()
        if field == "scale":
            builder.set_transform(seg, scale=float(value))
        elif field == "tx":
            builder.set_transform(seg, x=float(value))
        elif field == "ty":
            builder.set_transform(seg, y=float(value))
        elif field == "rotation":
            builder.set_transform(seg, rotation=float(value))
        elif field == "alpha":
            builder.set_transform(seg, alpha=float(value))
        after = self._snapshot()
        self.bridge.record_transform(seg, before, after, "调整画面")
        self.bridge.statusMessage.emit(
            f"位置 {seg.clip.tx:+.2f},{seg.clip.ty:+.2f} · 缩放 {seg.clip.scale_x:.0%} · "
            f"旋转 {seg.clip.rotation:.0f}° · 不透明度 {seg.clip.alpha:.0%}")

    def _snapshot(self) -> dict:
        clip = self._seg.clip
        return {"tx": float(clip.tx), "ty": float(clip.ty), "scale": float(clip.scale_x),
                "rotation": float(clip.rotation), "alpha": float(clip.alpha)}

    def reset_transform(self) -> None:
        seg = self._seg
        if seg is None or self._seg_is_audio():
            return
        before = self._snapshot()
        builder.set_transform(seg, scale=1.0, x=0.0, y=-0.7, rotation=0.0, alpha=1.0)
        after = self._snapshot()
        self.bridge.record_transform(seg, before, after, "复位画面")
        self.load_values()
        self.bridge.edited.emit()
