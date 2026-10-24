"""预览 + 播放条。

两件事和 HANDOFF 直接相关：
1. **D2 播放卡死**：渲染走 ``RenderService``（工作线程 + 只渲染最新请求），
   界面线程不再自己算帧；播放时按 ``playback_scale`` 降分辨率，松手/暂停回到全尺寸。
2. **§5.4 界面复刻**：顶部「播放器-时间线01 ▾」标题条、帧号时间码
   （``00:00:03:00``）、右下「原图 / 缩放 / 全屏」。
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QPushButton,
                               QSizePolicy, QSlider, QStyle, QTabBar,
                               QToolButton, QVBoxLayout, QWidget)

from ... import theme
from ..timecode import format_timecode


class Viewer(QWidget):
    """节目监视器：显示当前帧；播放时降分辨率以保持流畅。"""

    def __init__(self, bridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.mode = "program"          # program | source（PR 的双监视器）
        self.setMinimumSize(320, 180)
        self._cache = None
        self._label = QLabel(self)
        self._label.setObjectName("Viewer")
        self._label.setAlignment(Qt.AlignCenter)
        self._label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._title_bar())

        self.tabs = QTabBar(self)
        self.tabs.addTab("节目")
        self.tabs.addTab("源")
        self.tabs.setExpanding(False)
        self.tabs.currentChanged.connect(self._on_tab)
        layout.addWidget(self.tabs)
        layout.addWidget(self._label, 1)
        layout.addWidget(self._viewer_tools())

        self._last_size = QSize(0, 0)
        self._dirty = True          # 画面内容变了（换帧/改参数），必须重渲
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self.render_now)
        self._pending = False
        self._service = None
        self._want_token = 0
        self._shown_token = -1
        self._playing = False
        #: 「流畅」开关打开时，播放中按 1/3 尺寸渲染换帧率。
        #: **默认关**：用户第 243 轮实测报过"播放时画质明显变糊、暂停又恢复"——
        #: 画质优先，掉帧可以接受，掉分辨率不行。
        self.playback_scale = 1.0 / 3.0
        self.smooth_preview = False
        self.native_size = False        # 「原图」= 1:1 像素
        self.zoom = 1.0                 # 「缩放」下拉：只影响显示大小
        bridge.playheadChanged.connect(lambda _us: self.schedule())
        bridge.projectReloaded.connect(self.schedule)
        bridge.projectReloaded.connect(self.refresh_sequences)
        self.refresh_sequences()        # 启动时也要有一项（否则下拉是空的）

    # -- 标题条 / 工具条（剪映预览区的外观）-------------------------------- #
    def _title_bar(self) -> QWidget:
        bar = QWidget(self)
        bar.setObjectName("ViewerBar")
        box = QHBoxLayout(bar)
        box.setContentsMargins(8, 2, 8, 2)
        box.setSpacing(6)
        self.panel_title = QComboBox(bar)
        self.panel_title.setToolTip("切换时间线（一个工程可以有多条）")
        self.panel_title.currentIndexChanged.connect(self._on_sequence)
        box.addWidget(self.panel_title)
        box.addStretch(1)
        self.quality_hint = QLabel("", bar)
        self.quality_hint.setObjectName("QualityHint")
        box.addWidget(self.quality_hint)
        return bar

    def _viewer_tools(self) -> QWidget:
        bar = QWidget(self)
        bar.setObjectName("ViewerTools")
        box = QHBoxLayout(bar)
        box.setContentsMargins(8, 2, 8, 4)
        box.setSpacing(6)
        box.addStretch(1)
        self.original_btn = QToolButton(bar)
        self.original_btn.setText("原图")
        self.original_btn.setCheckable(True)
        self.original_btn.setToolTip("按工程像素 1:1 显示（不缩放；播放也不降分辨率）")
        self.original_btn.toggled.connect(self._on_original)
        box.addWidget(self.original_btn)

        self.smooth_btn = QToolButton(bar)
        self.smooth_btn.setText("流畅")
        self.smooth_btn.setCheckable(True)
        self.smooth_btn.setToolTip(
            "播放时按 1/3 尺寸渲染，帧率高但画面会糊（默认关：画质优先）")
        self.smooth_btn.toggled.connect(self._on_smooth)
        box.addWidget(self.smooth_btn)

        self.zoom_box = QComboBox(bar)
        for label, value in (("适应", 1.0), ("50%", 0.5), ("75%", 0.75), ("100%", 1.0)):
            self.zoom_box.addItem(label, value)
        self.zoom_box.setToolTip("预览缩放（显示大小，不影响导出）")
        self.zoom_box.currentIndexChanged.connect(self._on_zoom)
        box.addWidget(self.zoom_box)

        self.fullscreen_btn = QToolButton(bar)
        self.fullscreen_btn.setText("全屏")
        self.fullscreen_btn.setToolTip("全屏预览（Esc 退出）")
        self.fullscreen_btn.toggled.connect(self._on_fullscreen)
        box.addWidget(self.fullscreen_btn)
        return bar

    def refresh_sequences(self) -> None:
        """把时间线下拉刷成当前工程的序列列表（一条时禁用）。"""
        import re

        from PySide6.QtCore import QSignalBlocker

        names = self.bridge.sequence_names()
        blocker = QSignalBlocker(self.panel_title)
        self.panel_title.clear()
        for index, name in enumerate(names):
            # 默认名"时间线 1"按剪映的写法显示成"播放器-时间线01"
            match = re.fullmatch(r"时间线\s*(\d+)", name)
            label = f"播放器-时间线{int(match.group(1)):02d}" if match else f"播放器-{name}"
            self.panel_title.addItem(label, index)
        self.panel_title.setCurrentIndex(self.bridge.project.active_sequence)
        del blocker
        self.panel_title.setEnabled(len(names) > 1)

    def _on_sequence(self, index: int) -> None:
        if index >= 0:
            self.bridge.switch_sequence(index)

    def _on_original(self, on: bool) -> None:
        self.native_size = bool(on)
        self.schedule()

    def _on_smooth(self, on: bool) -> None:
        self.smooth_preview = bool(on)
        self.quality_hint.setText("流畅预览（1/3 尺寸）" if on else "")
        self.schedule()

    def _on_zoom(self, _index: int) -> None:
        self.zoom = float(self.zoom_box.currentData() or 1.0)
        self.schedule()

    def _on_fullscreen(self, on: bool) -> None:
        window = self.window()
        if on:
            window.showFullScreen()
            self._fullscreen_was = True
        else:
            window.showNormal()

    # -- 调度 ------------------------------------------------------------- #
    def schedule(self) -> None:
        """请求重画。**每次都置脏**——只是尺寸没变时也要重渲，
        否则拖动播放头、改文字/调节之后预览会停在旧画面上。

        播放时不走那个 16ms 合并窗口：每帧都等 16ms 等于白白掉一半帧率。
        """
        self._dirty = True
        if self._playing:
            self.render_now()
            return
        if not self._pending:
            self._pending = True
            self._timer.start()

    def resizeEvent(self, event):                       # noqa: N802
        super().resizeEvent(event)
        self.schedule()

    def _on_tab(self, index: int) -> None:
        self.mode = "source" if index == 1 else "program"
        self.schedule()

    def set_playing(self, playing: bool) -> None:
        """播放/暂停：默认**不降分辨率**，只有勾了「流畅」才降到 1/3。"""
        self._playing = bool(playing)
        self.quality_hint.setText(
            "流畅预览（1/3 尺寸）" if self._playing and self.smooth_preview else "")
        self.schedule()

    def _source_cache(self):
        if self._cache is None:
            from ..media_cache import MediaCache

            self._cache = MediaCache(max_thumbs=32)
        return self._cache

    # -- 尺寸 ------------------------------------------------------------- #
    def _fit_size(self) -> QSize:
        """显示尺寸：默认按控件大小"适应"；勾了「原图」就是画布像素 1:1。"""
        project = self.bridge.project
        canvas = project.canvas
        cw, ch = int(canvas.width), int(canvas.height)
        if cw <= 0 or ch <= 0:
            cw, ch = 1280, 720
        if self.native_size:
            return QSize(max(32, cw), max(32, ch))
        avail_w = max(64, self._label.width())
        avail_h = max(64, self._label.height())
        scale = min(avail_w / cw, avail_h / ch) * max(0.1, float(self.zoom or 1.0))
        return QSize(max(32, int(cw * scale) // 2 * 2), max(32, int(ch * scale) // 2 * 2))

    def _render_size(self) -> QSize:
        """渲染尺寸：默认等于显示尺寸（**播放时也不降**），只有「流畅」才降。

        用户第 243 轮实测报过"播放时糊、暂停又清晰"——那是上一版播放自动降到
        1/3 造成的。现在画质优先：跟不上就掉帧，不改分辨率。
        """
        fit = self._fit_size()
        if self._playing and self.smooth_preview:
            scale = max(0.25, min(1.0, float(self.playback_scale)))
        else:
            scale = 1.0
        return QSize(max(32, int(fit.width() * scale) // 2 * 2),
                     max(32, int(fit.height() * scale) // 2 * 2))

    # -- 源监视器 --------------------------------------------------------- #
    def _render_source(self) -> None:
        """源监视器：直接解素材的某一帧，不经过工程。"""
        source = getattr(self.bridge, "source", None)
        if not source:
            self._label.setText("源监视器\n\n在左栏单击一个素材，按 I/O 标记入出点")
            return
        size = self._fit_size()
        arr = self._source_cache().frame(source["path"], int(source["playhead_us"]),
                                         size.width(), size.height())
        if arr is None:
            self._label.setText("（这个素材取不到画面）")
            return
        h, w = arr.shape[:2]
        image = QImage(arr.data, w, h, arr.strides[0], QImage.Format_RGB888).copy()
        self._label.setPixmap(QPixmap.fromImage(image))
        self._last_size = size
        self._dirty = False

    # -- 节目监视器 ------------------------------------------------------- #
    def _ensure_service(self):
        if self._service is None:
            service = self.bridge.render_service
            service.frameReady.connect(self._on_frame)
            self._service = service
        return self._service

    def _on_frame(self, token: int, image) -> None:
        if image is None:
            return
        # 渲染是一个接一个完成的，来不及显示的那几帧被服务层合并掉了；
        # 这里收到的每一帧都是"目前最新的"，所以照显——只在 token 比已显示的更旧时丢弃。
        if token < self._shown_token:
            return
        self._shown_token = token
        self._show(image)

    def _show(self, image: QImage) -> None:
        size = self._fit_size()
        pix = QPixmap.fromImage(image)
        if pix.size() != size:
            mode = Qt.FastTransformation if self._playing else Qt.SmoothTransformation
            pix = pix.scaled(size, Qt.KeepAspectRatio, mode)
        self._label.setPixmap(pix)
        self._last_size = size
        self._dirty = False

    def render_now(self) -> None:
        self._pending = False
        if self.mode == "source":
            self._render_source()
            return
        if (not self._dirty) and self._last_size == self._fit_size() \
                and self._label.pixmap() is not None:
            return
        size = self._render_size()
        try:
            service = self._ensure_service()
        except Exception:                              # noqa: BLE001
            service = None
        if service is None:                            # 没有事件循环时退回同步
            image = self.bridge.frame_image(size.width(), size.height())
            if image is not None:
                self._show(image)
            return
        self._want_token = service.request(size.width(), size.height(), self.bridge.playhead)


class ScrubBar(QWidget):
    """播放条下面那条可拖的时间轴（剪映有，比拖播放头方便）。"""

    seekRequested = Signal(int)          # 微秒

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(14)
        self.setCursor(Qt.PointingHandCursor)
        self._position = 0.0             # 0..1
        self._marks = []                 # 源监视器的入出点（0..1）
        self._dragging = False

    # -- 外部 ------------------------------------------------------------- #
    def set_progress(self, ratio: float) -> None:
        ratio = max(0.0, min(1.0, float(ratio)))
        if abs(ratio - self._position) > 1e-6:
            self._position = ratio
            self.update()

    def set_marks(self, marks) -> None:
        self._marks = [max(0.0, min(1.0, float(m))) for m in (marks or [])]
        self.update()

    def ratio_at(self, x: float) -> float:
        if self.width() <= 1:
            return 0.0
        return max(0.0, min(1.0, x / self.width()))

    # -- 绘制 ------------------------------------------------------------- #
    def paintEvent(self, event) -> None:            # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        track = QRectF(0, self.height() / 2 - 1.5, self.width(), 3)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(theme.BORDER))
        painter.drawRoundedRect(track, 1.5, 1.5)

        for mark in self._marks:
            x = mark * self.width()
            painter.setBrush(QColor(theme.FG_FAINT))
            painter.drawRect(QRectF(x - 0.5, 2, 1.5, self.height() - 4))

        filled = QRectF(0, track.top(), self._position * self.width(), track.height())
        painter.setBrush(QColor(theme.ACCENT))
        painter.drawRoundedRect(filled, 1.5, 1.5)

        handle_x = self._position * self.width()
        painter.setBrush(QColor(theme.ACCENT))
        painter.drawEllipse(QPointF(handle_x, self.height() / 2), 4.5, 4.5)

    # -- 交互 ------------------------------------------------------------- #
    def mousePressEvent(self, event) -> None:       # noqa: N802
        self._dragging = True
        self._emit(event.position().x())

    def mouseMoveEvent(self, event) -> None:        # noqa: N802
        if self._dragging:
            self._emit(event.position().x())

    def mouseReleaseEvent(self, event) -> None:     # noqa: N802
        if self._dragging:
            self._emit(event.position().x())
            self._dragging = False

    def _emit(self, x: float) -> None:
        ratio = self.ratio_at(x)
        self.set_progress(ratio)
        self.seekRequested.emit(int(round(ratio * 1_000_000)))   # 千分之一精度，调用方换算


class PlayerBar(QWidget):
    """播放条：上/下一帧、播放、帧号时间码、音量。"""

    def __init__(self, bridge, on_toggle_play, parent=None, on_seek=None):
        super().__init__(parent)
        self.setObjectName("PlayerBar")
        self.bridge = bridge
        #: 用户主动定位时的回调（主窗口用它先暂停再定位）
        self.on_seek = on_seek
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 2, 8, 4)
        outer.setSpacing(2)

        self.scrub = ScrubBar(self)
        self.scrub.seekRequested.connect(self._on_scrub)
        outer.addWidget(self.scrub)

        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        outer.addLayout(layout)

        self.play_btn = QPushButton(self)
        self.play_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        self.play_btn.setToolTip("播放 / 暂停（空格）")
        self.play_btn.clicked.connect(on_toggle_play)
        layout.addWidget(self.play_btn)

        prev_btn = QPushButton("◀", self)
        prev_btn.setToolTip("上一帧")
        prev_btn.clicked.connect(lambda: bridge.step_frames(-1))
        layout.addWidget(prev_btn)

        next_btn = QPushButton("▶", self)
        next_btn.setToolTip("下一帧")
        next_btn.clicked.connect(lambda: bridge.step_frames(1))
        layout.addWidget(next_btn)

        self.timecode = QLabel("00:00:00:00 / 00:00:00:00", self)
        self.timecode.setObjectName("Timecode")
        layout.addWidget(self.timecode)

        layout.addStretch(1)
        vol = QSlider(Qt.Horizontal, self)
        vol.setFixedWidth(theme.px(90))
        vol.setRange(0, 100)
        vol.setValue(100)
        layout.addWidget(QLabel("音量", self))
        layout.addWidget(vol)

        bridge.playheadChanged.connect(self.update_timecode)
        bridge.projectReloaded.connect(self.update_timecode)
        self.update_timecode()

    def _fps(self) -> float:
        try:
            return float(self.bridge.project.fps or 30)
        except Exception:                              # noqa: BLE001
            return 30.0

    def _on_scrub(self, permille: int) -> None:
        """拖动时间轴：节目模式改播放头，源模式改源素材位置。"""
        ratio = permille / 1_000_000
        if getattr(self, "source_mode", False) and getattr(self.bridge, "source", None):
            total = int(self.bridge.source["duration_us"])
            self.bridge.set_source_playhead(int(ratio * total))
        else:
            total = int(self.bridge.duration_us)
            target = int(ratio * total)
            if self.on_seek is not None:
                self.on_seek(target)              # 主窗口会先暂停再定位
            else:
                self.bridge.set_playhead(target)
        self.update_timecode()

    def update_timecode(self, *_args) -> None:
        self._sync_scrub()
        fps = self._fps()
        if getattr(self, "source_mode", False) and getattr(self.bridge, "source", None):
            source = self.bridge.source
            now = int(source["playhead_us"])
            total = int(source["duration_us"])
            span = self.bridge.source_span_us
            self.timecode.setText(
                f"{format_timecode(now, fps)} / {format_timecode(total, fps)}"
                f"   入 {source['in_us'] / 1e6:.2f}s 出 {source['out_us'] / 1e6:.2f}s"
                f"（{span / 1e6:.2f}s）")
            return
        self.timecode.setText(f"{format_timecode(self.bridge.playhead, fps)} / "
                              f"{format_timecode(self.bridge.duration_us, fps)}")

    def _sync_scrub(self) -> None:
        scrub = getattr(self, "scrub", None)
        if scrub is None:
            return
        if getattr(self, "source_mode", False) and getattr(self.bridge, "source", None):
            source = self.bridge.source
            total = max(1, int(source["duration_us"]))
            scrub.set_progress(int(source["playhead_us"]) / total)
            scrub.set_marks([int(source["in_us"]) / total, int(source["out_us"]) / total])
        else:
            total = max(1, int(self.bridge.duration_us))
            scrub.set_progress(int(self.bridge.playhead) / total)
            scrub.set_marks([])

    def set_source_mode(self, on: bool) -> None:
        self.source_mode = bool(on)
        self.update_timecode()

    def set_playing(self, playing: bool) -> None:
        icon = QStyle.SP_MediaPause if playing else QStyle.SP_MediaPlay
        self.play_btn.setIcon(self.style().standardIcon(icon))


def _fmt(seconds: float) -> str:
    m, s = divmod(max(0.0, float(seconds)), 60)
    return f"{int(m):02d}:{s:06.3f}"
