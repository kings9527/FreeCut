"""时间线交互：选中、拖动、两端裁剪、吸附、轨道目标。

编辑模型**借鉴 Premiere**（剪映没有）：每条视频/音频轨前有一个「目标」灯，
插入与覆盖只会落在点亮的轨道上。P1 先把灯做出来，P3 接三点编辑。

**性能约定（HANDOFF D1）**：绘制路径上不许解码、不许全屏重绘。
- 缩略图/波形只读缓存（``MediaCache.pixmap`` / ``waveform``），没命中就跳过，
  解码在工作线程，解好发 ``ready`` 再重画一次；
- 拖动只重画"旧位置 ∪ 新位置"两块；悬停高亮只重画涉及的两个片段；
- 播放头只重画两条竖条。
这三条都有测试守着（``TestUserPathDragPerf``）。
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QPainter, QPen, QPixmap,
                       QPolygonF)
from PySide6.QtWidgets import QGraphicsScene, QGraphicsView

from ... import theme
from ..library import LIBRARY_MIME
from ..media_cache import MediaCache
from ..theme_qss import graphicsscene_colors

ROW_H = 40
HEADER_H = 24
TRIM_PX = 7                  # 两端各 7px 是裁剪把手
SNAP_PX = 6                  # 吸附阈值（像素）
HEADER_W = 120               # 左侧固定轨头（封面 + 目标灯 + S/锁/静音）
TARGET_W = HEADER_W          # 兼容旧名字：时间内容的左边界
CONTENT_X = HEADER_W         # 时间内容的左边界：片段从这之后开始，不和轨头重叠
TARGET_DOT = (48, 29)        # 目标灯在轨头里的相对位置
HEADER_BUTTONS = (("solo", 68), ("lock", 84), ("mute", 100))
BUTTON_R = 7

CLIP_LABELS = {"video": "视频", "text": "文字", "sticker": "贴纸",
               "audio": "音频", "effect": "特效", "filter": "滤镜"}
TRACK_ORDER = {"video": 0, "effect": 1, "filter": 2, "text": 3, "sticker": 4, "audio": 5}
TARGETABLE = ("video", "audio")

#: 每个片段最多画几张缩略图（超出的不画：画得多不等于看得见）
MAX_THUMBS_PER_CLIP = 8


class TimelineView(QGraphicsView):
    playheadRequested = Signal(int)        # 微秒
    segmentPicked = Signal(object)         # Segment | None
    edited = Signal(str)                   # 状态栏文案
    contextRequested = Signal(object, object)   # (segment|None, QPoint 全局坐标)
    #: 从素材库拖进来的特效/转场：({kind, value}, 落点所在的片段)
    libraryDropped = Signal(dict, object)

    def __init__(self, bridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.colors = graphicsscene_colors()
        self.cache = MediaCache.shared()
        self.ppm = 60.0                    # 每秒像素
        self.scroll_us = 0
        self.snap_enabled = True           # 工具栏「自动吸附」
        self._mode: Optional[str] = None    # move / trim_l / trim_r
        self._scrubbing = False            # 正在拖播放头
        self._drag_seg = None
        self._drag_row = -1
        self._hover_seg = None
        self._group_origins = []        # 编组一起拖动时的 (片段, 原起点)
        self._group_before = None       # 编组拖动前的轨道快照（撤销用）
        self._band_origin = None        # 框选起点（按下时的场景坐标）
        self._band_rect = None          # 框选矩形
        self.tool = "select"            # select | razor（切割工具）
        self._drop_row = -1             # 拖动时指针所在的轨道行（跨轨用）
        #: 轨道行高（剪映可以调；菜单里切换紧凑/标准/高）
        self.row_h = ROW_H
        self._drop_us = None            # 外部拖入时的插入位置（时间轴坐标）
        self._drop_clip = None          # 素材库拖入时高亮的片段
        self._drag_origin = QPointF()
        self._orig_start = 0
        self._orig_dur = 0
        self._orig_source_start = 0
        self._last_playhead = 0

        scene = QGraphicsScene(self)
        self.setScene(scene)
        self.setFrameShape(QGraphicsView.NoFrame)
        # 场景比视口小的时候 Qt 默认居中；时间线必须顶端对齐，
        # 否则第一行轨道会被顶下去（轨头点击也会跟着错位）
        self.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.setRenderHint(QPainter.Antialiasing, False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setBackgroundBrush(QBrush(QColor(self.colors["bg"])))
        # 局部重绘：拖动时只重画受影响的那一块（FullViewportUpdate 会整屏重画）
        self.setViewportUpdateMode(QGraphicsView.MinimalViewportUpdate)
        self.setOptimizationFlag(QGraphicsView.DontSavePainterState, False)

        bridge.projectReloaded.connect(self.refresh)
        bridge.playheadChanged.connect(self._on_playhead)
        bridge.selectionChanged.connect(self._on_selection)
        self.cache.ready.connect(self._on_cache_ready)
        self.refresh()

    # -- 数据 ------------------------------------------------------------- #
    def rows(self) -> List:
        tracks = [t for t in self.bridge.project.tracks if t.type in TRACK_ORDER]
        return sorted(tracks, key=lambda t: TRACK_ORDER.get(t.type, 9))

    def _duration_us(self) -> int:
        try:
            value = self.bridge.project.duration
            value = value() if callable(value) else value
            return max(3_000_000, int(value))
        except Exception:                              # noqa: BLE001
            return 3_000_000

    def _scene_size(self):
        rows = self.rows()
        width = max(600.0, self._duration_us() / 1e6 * self.ppm + 120.0)
        height = HEADER_H + max(1, len(rows)) * self.row_h + 8
        return width, height

    def refresh(self) -> None:
        width, height = self._scene_size()
        self.scene().setSceneRect(0, 0, width, height)
        self._last_playhead = self.bridge.playhead
        self.viewport().update()

    def resizeEvent(self, event):                       # noqa: N802
        super().resizeEvent(event)
        width, height = self._scene_size()
        self.scene().setSceneRect(0, 0, max(width, self.viewport().width() + 40), height)

    # -- 坐标 ------------------------------------------------------------- #
    def _us_to_x(self, us: int) -> float:
        return CONTENT_X + (us - self.scroll_us) / 1e6 * self.ppm

    def _x_to_us(self, x: float) -> int:
        return int(self.scroll_us + max(0.0, x - CONTENT_X) / self.ppm * 1e6)

    def fit_to_window(self, width: int | None = None) -> None:
        """把所有片段缩放到可视宽度内（ppm = 像素/秒）。"""
        ends = [int(seg.target_timerange.start) + int(seg.target_timerange.duration)
                for track in self.rows() for seg in track.segments]
        total_s = max(ends) / 1e6 if ends else 10.0
        total_s = max(0.5, total_s)
        if width is None:
            width = self.viewport().width()
            if width < 200:                # 还没显示/被隐藏时 viewport 宽度不可靠
                width = max(self.width(), 400)
        available = max(80, width - CONTENT_X - 16)
        self.ppm = max(8.0, min(600.0, available / total_s))
        self.scroll_us = 0
        self.refresh()

    def _track_y(self, index: int) -> int:
        return HEADER_H + index * self.row_h

    def _row_at(self, y: float) -> int:
        return int((y - HEADER_H) // self.row_h)

    def _track_index(self, track) -> int:
        try:
            return self.rows().index(track)
        except ValueError:
            return -1

    def _seg_rect(self, seg, row: int) -> QRectF:
        """片段在场景里的矩形（绘制与局部重绘共用同一份定义）。"""
        start = int(seg.target_timerange.start)
        dur = int(seg.target_timerange.duration)
        x1 = self._us_to_x(start)
        x2 = self._us_to_x(start + dur)
        y = self._track_y(row)
        return QRectF(x1 + 1, y + 4, max(3.0, x2 - x1 - 2), self.row_h - 9)

    def _invalidate(self, scene_rect: QRectF, pad: int = 4) -> None:
        """把场景矩形换成视口矩形并只重画这一块。"""
        if scene_rect is None or scene_rect.isNull():
            return
        poly = self.mapFromScene(scene_rect)
        rect = poly.boundingRect().adjusted(-pad, -pad, pad, pad)
        self.viewport().update(rect)

    def _invalidate_seg(self, seg, pad: int = 4) -> None:
        if seg is None:
            return
        for index, track in enumerate(self.rows()):
            if seg in track.segments:
                self._invalidate(self._seg_rect(seg, index), pad)
                return

    # -- 缓存回调 --------------------------------------------------------- #
    def _on_cache_ready(self) -> None:
        # 解好一张缩略图/一段波形：重画（只重画可视区）
        self.viewport().update()

    def _on_playhead(self, us: int) -> None:
        """只重画两条竖条，不整屏重画。"""
        old = self._last_playhead
        self._last_playhead = us
        height = self.viewport().height()
        for value in (old, us):
            x = self.mapFromScene(QPointF(self._us_to_x(value), 0)).x()
            self.viewport().update(QRect(int(x) - 3, 0, 7, height))

    def _on_selection(self, old_seg) -> None:
        self.viewport().update()

    # -- 命中 ------------------------------------------------------------- #
    def _hit(self, pos: QPointF):
        """返回 (seg, mode, track_index)。mode ∈ move / trim_l / trim_r。"""
        rows = self.rows()
        if pos.y() < HEADER_H or pos.y() > self.sceneRect().height():
            return None, None, -1
        index = self._row_at(pos.y())
        if not (0 <= index < len(rows)):
            return None, None, index
        for seg in rows[index].segments:
            x1 = self._us_to_x(int(seg.target_timerange.start))
            x2 = self._us_to_x(int(seg.target_timerange.start + seg.target_timerange.duration))
            if x1 <= pos.x() <= x2:
                if pos.x() - x1 <= TRIM_PX:
                    return seg, "trim_l", index
                if x2 - pos.x() <= TRIM_PX:
                    return seg, "trim_r", index
                return seg, "move", index
        return None, None, index

    # -- 绘制 ------------------------------------------------------------- #
    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:   # noqa: N802
        c = self.colors
        rows = self.rows()
        width = self.sceneRect().width()
        right = min(width, rect.right())

        # 表头（只在暴露区画）
        if rect.top() < HEADER_H:
            painter.fillRect(QRectF(rect.left(), 0, rect.width(), HEADER_H),
                             QColor(c["header"]))
            self._draw_ruler(painter, rect)

        font = QFont("Microsoft YaHei", 8)
        mono = QFont("Consolas", 8)
        painter.setFont(font)
        for i, track in enumerate(rows):
            y = self._track_y(i)
            if y + self.row_h < rect.top() or y > rect.bottom():
                continue
            painter.fillRect(QRectF(rect.left(), y, rect.width(), self.row_h), QColor(c["row"]))
            painter.setPen(QPen(QColor(c["row_line"]), 1))
            painter.drawLine(QPointF(rect.left(), y + self.row_h),
                             QPointF(rect.right(), y + self.row_h))

            for seg in track.segments:
                clip_rect = self._seg_rect(seg, i)
                if clip_rect.right() < rect.left() or clip_rect.left() > right:
                    continue
                colour = {
                    "video": c["clip_video"], "text": c["clip_text"],
                    "audio": c["clip_audio"],
                }.get(track.type, c["clip_other"])
                painter.setBrush(QColor(colour))
                selected = self.bridge.is_selected(seg)
                primary = seg is self.bridge.selected
                hovered = seg is self._hover_seg
                dropping = seg is self._drop_clip
                if dropping:
                    # 从素材库拖效果/转场过来：整块片段描青边，告诉用户"会套在这"
                    painter.setPen(QPen(QColor(theme.ACCENT), 3))
                elif primary:
                    painter.setPen(QPen(QColor(c["selected"]), 2))
                elif selected:
                    # 多选里非主选：同样描边但要能看出区别
                    painter.setPen(QPen(QColor(theme.ACCENT).lighter(115), 2))
                elif hovered:
                    painter.setPen(QPen(QColor(c["selected"]).lighter(120), 2))
                else:
                    painter.setPen(QPen(QColor("#0f0f12"), 1))
                painter.drawRoundedRect(clip_rect, 4, 4)

                if track.type in ("video", "sticker") and clip_rect.width() > 60:
                    self._draw_thumbnails(painter, seg, clip_rect)
                elif track.type == "audio" and clip_rect.width() > 40:
                    self._draw_waveform(painter, seg, clip_rect)

                if clip_rect.width() > 34:
                    painter.setPen(QColor("#f2f2f7"))
                    painter.setFont(font)
                    text = self._clip_text(seg, track.type)
                    painter.drawText(clip_rect.adjusted(6, 0, -4, 0),
                                     Qt.AlignVCenter | Qt.AlignLeft, text)

                self._draw_keyframes(painter, seg, clip_rect)
                # 有前一段时，转场由下面的"覆盖块"统一画；没有前一段才画角标提示
                if self._transition_span(seg) is None:
                    self._draw_transition_badge(painter, seg, clip_rect)

            self._draw_transition_blocks(painter, i)

        painter.setFont(mono)
        painter.setPen(QPen(QColor(c["row_line"]), 1))
        if HEADER_H >= rect.top() and HEADER_H <= rect.bottom():
            painter.drawLine(QPointF(rect.left(), HEADER_H), QPointF(rect.right(), HEADER_H))

    def _draw_ruler(self, painter: QPainter, rect: QRectF) -> None:
        c = self.colors
        painter.setFont(QFont("Consolas", 8))
        painter.setPen(QColor(c["ruler_text"]))
        tick = 2.0
        # 只画暴露区里的刻度：整条时间线可能有几万个刻度
        first = max(0, int((rect.left() - CONTENT_X) / self.ppm + self.scroll_us / 1e6) - 1)
        last = int((rect.right() - CONTENT_X) / self.ppm + self.scroll_us / 1e6) + 1
        t = float(first - first % int(tick)) if tick >= 1 else float(first)
        while t <= last:
            x = self._us_to_x(int(t * 1e6))
            if x >= CONTENT_X:
                painter.drawLine(QPointF(x, HEADER_H - 6), QPointF(x, HEADER_H))
                painter.drawText(QPointF(x + 3, 12), self._fmt(t))
            t += tick

    # -- 关键帧标记 --------------------------------------------------------- #
    @staticmethod
    def keyframe_marks(seg) -> list:
        """片段上所有关键帧的**相对位置**（0..1，按时间排序、去重）。

        剪映会在片段顶部画小菱形；同一个时间点上有多个属性时只画一颗。
        """
        try:
            duration = int(seg.target_timerange.duration)
        except Exception:                              # noqa: BLE001
            return []
        if duration <= 0:
            return []
        seen = set()
        for keys in (seg.keyframes or {}).values():
            for key in keys:
                offset = int(key.time_offset)
                if 0 <= offset <= duration:
                    seen.add(round(offset / duration, 6))
        return sorted(seen)

    def _has_transition(self, seg) -> bool:
        try:
            for ref in self.bridge.project.refs_of(seg):
                if getattr(ref, "type", "") == "transition":
                    return True
        except Exception:                              # noqa: BLE001
            return False
        return False

    # -- 转场：覆盖接缝前后的一块（剪映的做法） --------------------------- #
    def _transition_span(self, seg):
        """返回 (start_us, end_us, kind, name, dur) 或 None。

        区间以切点为中心：``[切点 - dur/2, 切点 + dur/2]``，正好压住前后两段。
        """
        try:
            from ...render import transition_kind

            trans = None
            dur = 0
            for ref in self.bridge.project.refs_of(seg):
                if getattr(ref, "type", "") == "transition":
                    trans = ref
                    dur = int(ref.get("duration") or 0)
                    break
            if trans is None or dur <= 0:
                return None
            if self._neighbour_before(seg) is None:
                return None
            kind = transition_kind(trans)
            name = str(trans.get("name") or kind)
            cut = int(seg.target_timerange.start)
            return (cut - dur // 2, cut + dur // 2, kind, name, dur)
        except Exception:                              # noqa: BLE001
            return None

    def _neighbour_before(self, seg):
        """同轨道上紧挨在 seg 左边、且 2 秒以内的片段。"""
        track = self.bridge.track_of_segment(seg)
        if track is None:
            return None
        start = int(seg.target_timerange.start)
        best = None
        for other in track.sorted_segments():
            if other is seg:
                continue
            end = int(other.target_timerange.start + other.target_timerange.duration)
            if end > start + 1 or start - end > 2_000_000:
                continue
            if best is None or end > int(best.target_timerange.start + best.target_timerange.duration):
                best = other
        return best

    def transition_spans(self, track_index: int):
        """该轨道上所有"覆盖块"：(seg, start_us, end_us, kind, name, dur)。"""
        rows = self.rows()
        if not (0 <= track_index < len(rows)):
            return []
        out = []
        for seg in rows[track_index].segments:
            span = self._transition_span(seg)
            if span is not None:
                out.append((seg,) + span)
        return out

    def _transition_block_rect(self, seg, track_index: int):
        span = self._transition_span(seg)
        if span is None:
            return None
        start_us, end_us, _kind, _name, _dur = span
        y = self._track_y(track_index)
        x1 = self._us_to_x(start_us)
        x2 = self._us_to_x(end_us)
        width = max(16.0, x2 - x1)
        return QRectF(x1, y + self.row_h * 0.16, width, self.row_h * 0.68)

    def transition_at(self, pos):
        """命中的转场块 -> (seg, track_index, rect)，没有就 None。"""
        for i, _track in enumerate(self.rows()):
            for seg, *_rest in self.transition_spans(i):
                rect = self._transition_block_rect(seg, i)
                if rect is not None and rect.contains(pos):
                    return seg, i, rect
        return None

    def _draw_transition_blocks(self, painter: QPainter, track_index: int) -> None:
        """转场块：压住接缝前后各一半，带名字（太窄就只画图标）。"""
        font = QFont("Microsoft YaHei", 8)
        for seg, start_us, end_us, _kind, name, _dur in self.transition_spans(track_index):
            rect = self._transition_block_rect(seg, track_index)
            if rect is None:
                continue
            painter.save()
            painter.setPen(QPen(QColor(theme.ACCENT), 1.6))
            painter.setBrush(QColor(12, 18, 22, 235))
            painter.drawRoundedRect(rect, 3, 3)
            painter.setFont(font)
            painter.setPen(QColor(theme.ACCENT))
            label = "⇄" if rect.width() < 54 else f"⇄ {name}"
            painter.drawText(rect.adjusted(3, 0, -2, 0),
                             Qt.AlignVCenter | Qt.AlignLeft, label)
            painter.restore()

    def _draw_transition_badge(self, painter: QPainter, seg, clip_rect: QRectF) -> None:
        """片段左端的转场标记：加了转场之后时间线上看得见（否则像没生效）。"""
        if not self._has_transition(seg):
            return
        if clip_rect.width() < 16:
            return
        painter.save()
        painter.setClipRect(clip_rect)
        w, h = 14.0, min(11.0, clip_rect.height() - 2)
        box = QRectF(clip_rect.left() + 1, clip_rect.bottom() - h - 1, w, h)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#0f0f12"))
        painter.drawRoundedRect(box.adjusted(-1, -1, 1, 1), 2, 2)
        painter.setBrush(QColor(theme.ACCENT))
        painter.drawRoundedRect(box, 2, 2)
        painter.setPen(QColor("#08222a"))
        painter.setFont(QFont("Microsoft YaHei", 7))
        painter.drawText(box, Qt.AlignCenter, "⇄")
        painter.restore()

    def _draw_keyframes(self, painter: QPainter, seg, clip_rect: QRectF) -> None:
        marks = self.keyframe_marks(seg)
        if not marks:
            return
        if clip_rect.width() < 8:
            return
        painter.save()
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(theme.ACCENT))
        cy = clip_rect.top() + 4.5
        r = 3.0
        for frac in marks:
            cx = clip_rect.left() + frac * clip_rect.width()
            cx = min(max(cx, clip_rect.left() + r), clip_rect.right() - r)
            painter.drawPolygon(QPolygonF([
                QPointF(cx, cy - r), QPointF(cx + r, cy),
                QPointF(cx, cy + r), QPointF(cx - r, cy),
            ]))
        painter.restore()

    def _media_path(self, seg) -> str:
        try:
            mat = self.bridge.project.material(seg.material_id)
        except Exception:                              # noqa: BLE001
            return ""
        return str(getattr(mat, "path", None) or getattr(mat, "file_path", None) or "")

    def _draw_thumbnails(self, painter: QPainter, seg, clip_rect: QRectF) -> None:
        """片段里平铺缩略图。**只读缓存**，没命中就留纯色块（工作线程在解）。"""
        path = self._media_path(seg)
        if not path:
            return
        thumb_w = int(min(96, max(48, clip_rect.height() * 16 / 9)))
        thumb_h = int(clip_rect.height()) - 2
        count = int(clip_rect.width() // max(1, thumb_w))
        count = max(1, min(count, MAX_THUMBS_PER_CLIP))
        painter.save()
        painter.setClipRect(clip_rect)
        start_us = int(seg.target_timerange.start)
        dur_us = int(seg.target_timerange.duration)
        for i in range(count):
            at = start_us + int(dur_us * (i + 0.5) / count)
            pix = self.cache.pixmap(path, at, thumb_w, thumb_h)
            x = clip_rect.left() + i * thumb_w
            if pix is None:
                continue
            painter.drawPixmap(QRectF(x, clip_rect.top() + 1, thumb_w, thumb_h), pix,
                               QRectF(0, 0, pix.width(), pix.height()))
        painter.restore()

    def _draw_waveform(self, painter: QPainter, seg, clip_rect: QRectF) -> None:
        path = self._media_path(seg)
        if not path:
            return
        buckets = max(8, min(400, int(clip_rect.width() / 2)))
        peaks = self.cache.waveform(path, int(seg.source_timerange.start),
                                    int(seg.source_timerange.duration), buckets)
        if peaks is None or len(peaks) == 0:
            return
        painter.save()
        painter.setClipRect(clip_rect)
        painter.setPen(QPen(QColor("#cfe9dd"), 1))
        mid = clip_rect.center().y()
        half = clip_rect.height() / 2 - 3
        step = clip_rect.width() / len(peaks)
        for i, value in enumerate(peaks):
            x = clip_rect.left() + i * step
            h = float(value) * half
            painter.drawLine(QPointF(x, mid - h), QPointF(x, mid + h))
        painter.restore()

    def _clip_text(self, seg, track_type: str) -> str:
        name = ""
        try:
            mat = self.bridge.project.material(seg.material_id)
            path = getattr(mat, "path", None) or getattr(mat, "file_path", None)
            if path:
                name = os.path.basename(str(path))
        except Exception:                              # noqa: BLE001
            pass
        if track_type == "text":
            mat = self.bridge.project.material(seg.material_id)
            content = str(getattr(mat, "content", "") or "")
            return content.replace("\n", " ")[:18] or "文字"
        return name or seg.id[:6]

    def drawForeground(self, painter: QPainter, rect: QRectF) -> None:    # noqa: N802
        x = self._us_to_x(self.bridge.playhead)
        if rect.left() - 2 <= x <= rect.right() + 2:
            painter.setPen(QPen(QColor(self.colors["playhead"]), 2))
            painter.drawLine(QPointF(x, 0), QPointF(x, self.sceneRect().height()))

        # 外部素材拖进来时，画出会插在哪里的竖线（剪映同样有这个指示）
        if self._drop_us is not None:
            drop_x = self._us_to_x(self._drop_us)
            painter.setPen(QPen(QColor(theme.ACCENT), 2, Qt.DashLine))
            painter.drawLine(QPointF(drop_x, HEADER_H),
                             QPointF(drop_x, self.sceneRect().height()))
            painter.setBrush(QColor(theme.ACCENT))
            painter.setPen(Qt.NoPen)
            painter.drawPolygon(QPolygonF([
                QPointF(drop_x - 6, HEADER_H - 10), QPointF(drop_x + 6, HEADER_H - 10),
                QPointF(drop_x, HEADER_H - 1),
            ]))
            painter.setPen(QColor(theme.ACCENT))
            painter.setFont(QFont("Consolas", 8))
            # 时间标签放在轨道区里，否则会和标尺文字叠在一起
            painter.drawText(QPointF(drop_x + 5, HEADER_H + 13),
                             self._fmt(self._drop_us / 1e6))

        # 框选：半透明矩形（剪映/PR 的框选也是这个观感）
        if self._band_rect is not None:
            painter.save()
            painter.setPen(QPen(QColor(theme.ACCENT), 1, Qt.DashLine))
            fill = QColor(theme.ACCENT)
            fill.setAlpha(40)
            painter.setBrush(fill)
            painter.drawRect(self._band_rect.intersected(self.sceneRect()))
            painter.restore()

        self._draw_track_headers(painter)

    # -- 固定轨头（封面 / S / 锁 / 静音）----------------------------------- #
    def _segments_in(self, rect: QRectF) -> list:
        """框选矩形覆盖到的片段（整块相交就算）。"""
        hits = []
        rows = self.rows()
        for index, track in enumerate(rows):
            y = self._track_y(index)
            if y + self.row_h < rect.top() or y > rect.bottom():
                continue
            for seg in track.segments:
                start = int(seg.target_timerange.start)
                x1 = self._us_to_x(start)
                x2 = self._us_to_x(start + int(seg.target_timerange.duration))
                if x2 < rect.left() or x1 > rect.right():
                    continue
                hits.append(seg)
        return hits

    def _draw_track_headers(self, painter: QPainter) -> None:
        """轨头横向滚动时不跟着走：这里用视口坐标画（剪映/PR 都这样）。"""
        c = self.colors
        rows = self.rows()
        painter.save()
        painter.resetTransform()                       # 切到视口像素坐标
        height = self.viewport().height()
        painter.fillRect(QRect(0, 0, HEADER_W, height), QColor(c["bg"]))
        font = QFont("Microsoft YaHei", 7)
        mono = QFont("Consolas", 7)
        for i, track in enumerate(rows):
            y = self.mapFromScene(QPointF(0, self._track_y(i))).y()
            if y + self.row_h < 0 or y > height:
                continue
            locked = self.bridge.is_locked(track)
            painter.fillRect(QRect(0, int(y), HEADER_W - 4, self.row_h - 1),
                             QColor("#202027" if not locked else "#2a2420"))
            painter.setPen(QPen(QColor(c["row_line"]), 1))
            painter.drawLine(QPointF(HEADER_W - 4, y), QPointF(HEADER_W - 4, y + self.row_h))

            # 封面：轨上第一个片段的缩略图
            cover = self._header_cover(track)
            if cover is not None:
                painter.drawPixmap(QRect(6, int(y) + 8, 36, 22), cover)
            else:
                painter.fillRect(QRect(6, int(y) + 8, 36, 22), QColor("#2b2b33"))

            painter.setFont(font)
            painter.setPen(QColor(c["label"]))
            # 自定义了名字就显示名字（双击轨头改名），否则显示轨道类型
            label = track.name or CLIP_LABELS.get(track.type, track.type)
            metrics = painter.fontMetrics()
            painter.drawText(QPointF(46, y + 15),
                             metrics.elidedText(str(label), Qt.ElideRight,
                                                int(HEADER_W - 50)))

            # 目标灯（PR 的 track targeting）
            if track.type in TARGETABLE:
                on = self.bridge.is_targeted(track)
                tx, ty = TARGET_DOT
                painter.setBrush(QColor(theme.ACCENT if on else "#00000000"))
                painter.setPen(QPen(QColor(theme.ACCENT if on else c["label"]), 1))
                painter.drawEllipse(QPointF(tx, y + ty), 4, 4)

            # S / 锁 / 静音
            states = (("S", self.bridge.is_solo(track), theme.ACCENT),
                      ("锁", locked, "#e0a13c"),
                      ("M", self.bridge.is_muted(track), "#e2574c"))
            painter.setFont(mono)
            for (glyph, active, colour), (_key, cx) in zip(states, HEADER_BUTTONS):
                cy = y + self.row_h / 2
                painter.setBrush(QColor(colour if active else "#00000000"))
                painter.setPen(QPen(QColor(colour if active else c["label"]), 1))
                painter.drawEllipse(QPointF(cx, cy), BUTTON_R, BUTTON_R)
                painter.setPen(QColor("#10202a" if active else c["label"]))
                painter.drawText(QRectF(cx - BUTTON_R, cy - BUTTON_R,
                                        BUTTON_R * 2, BUTTON_R * 2),
                                 Qt.AlignCenter, glyph)
        painter.restore()

    def _header_cover(self, track):
        # 主视频轨显示**封面帧**：设过封面就取那一刻的画面（剪映的"封面"）
        rows = self.rows()
        if rows and rows[0] is track:
            cover_us = int(getattr(self.bridge.project, "cover_time", 0) or 0)
            if cover_us > 0:
                for seg in sorted(track.segments,
                                  key=lambda s: int(s.target_timerange.start)):
                    start = int(seg.target_timerange.start)
                    end = start + int(seg.target_timerange.duration)
                    if not (start <= cover_us < end):
                        continue
                    path = self._media_path(seg)
                    if path:
                        offset = start + int((cover_us - start) * float(seg.speed or 1.0))
                        return self.cache.pixmap(path, offset, 36, 22)
        for seg in sorted(track.segments, key=lambda s: int(s.target_timerange.start)):
            path = self._media_path(seg)
            if not path:
                continue
            return self.cache.pixmap(path, int(seg.target_timerange.start), 36, 22)
        return None

    def _header_click(self, pos) -> bool:
        """点轨头：S / 锁 / 静音 / 目标灯。返回 True 表示这一下被吃掉了。"""
        if pos.x() >= HEADER_W:
            return False
        # mapToScene 在这个绑定里只收 QPoint：QPointF 要显式转（Qt6 的坑）
        point = pos.toPoint() if hasattr(pos, "toPoint") else QPoint(int(pos.x()), int(pos.y()))
        scene_y = self.mapToScene(point).y()
        if scene_y < HEADER_H:
            return False                      # 标尺那一行不是轨头，交给定位播放头
        index = self._row_at(scene_y)
        rows = self.rows()
        if not (0 <= index < len(rows)):
            return True
        track = rows[index]
        for key, cx in HEADER_BUTTONS:
            if abs(pos.x() - cx) <= BUTTON_R + 3:
                if key == "solo":
                    self.bridge.toggle_track_solo(track)
                elif key == "lock":
                    self.bridge.toggle_track_lock(track)
                else:
                    self.bridge.toggle_track_mute(track)
                self.viewport().update()
                return True
        tx, ty = TARGET_DOT
        if track.type in TARGETABLE and abs(pos.x() - tx) <= 8 \
                and abs(scene_y - (self._track_y(index) + ty)) <= 8:
            self.bridge.toggle_target(track)
            self.viewport().update()
            return True
        return True

    @staticmethod
    def _fmt(seconds: float) -> str:
        m, s = divmod(int(seconds), 60)
        return f"{m:02d}:{s:02d}"

    # -- 吸附 ------------------------------------------------------------- #
    def _snap(self, us: int, ignore=None) -> int:
        """吸附到 0 点、播放头、以及其它片段的两端。"""
        if not getattr(self, "snap_enabled", True):
            return int(us)
        best, best_d = us, SNAP_PX / self.ppm * 1e6
        candidates = [0, self.bridge.playhead]
        for track in self.rows():
            for seg in track.segments:
                if seg is ignore:
                    continue
                start = int(seg.target_timerange.start)
                candidates.extend([start, start + int(seg.target_timerange.duration)])
        for cand in candidates:
            delta = abs(us - cand)
            if delta < best_d:
                best, best_d = cand, delta
        return int(best)

    # -- 交互 ------------------------------------------------------------- #
    def mousePressEvent(self, event):                   # noqa: N802
        if self._header_click(event.position()):
            return
        pos = self.mapToScene(event.position().toPoint())
        additive = bool(event.modifiers() & Qt.ControlModifier)
        # 标尺上按下 = 定位并开始拖动播放头（剪映/PR 都是拖标尺，不用反复点）。
        # 轨道区里**不做**"靠近播放头就拖播放头"：那样会抢掉片段的拖动/裁剪。
        if pos.y() < HEADER_H:
            self._start_scrub(pos)
            return

        hit_transition = self.transition_at(pos)
        if hit_transition is not None:
            seg_t, index_t, _rect = hit_transition
            self.bridge.select(seg_t)
            self.bridge.set_playhead(int(seg_t.target_timerange.start))
            self.bridge.statusMessage.emit(
                f"转场：{self._transition_span(seg_t)[3]} — 播放头已放到接缝")
            self.refresh()
            return

        seg, mode, _index = self._hit(pos)
        if self.tool == "razor" and seg is not None:
            # 切割工具：点哪切哪，不用先单选再按 Ctrl+K
            self.bridge.split_segment(seg, self._x_to_us(pos.x()))
            self.refresh()
            return
        if seg is None:
            # 空白处按下：开始框选（拖出矩形），松手时没有拖动就当"定位播放头"
            self._band_origin = pos
            self._band_rect = None
            if not additive:
                self.bridge.select(None)
            self.viewport().update()
            return

        self.bridge.select(seg, additive=additive)
        self.segmentPicked.emit(seg)
        rows = self.rows()
        if 0 <= _index < len(rows) and self.bridge.is_locked(rows[_index]):
            self.bridge.statusMessage.emit("这条轨道已锁定（点轨头的「锁」解锁）")
            return
        self._drag_seg = seg
        self._drag_row = _index
        self._mode = mode
        self._drag_origin = pos
        self._orig_start = int(seg.target_timerange.start)
        self._orig_dur = int(seg.target_timerange.duration)
        self._orig_source_start = int(getattr(seg.source_timerange, "start", 0) or 0)
        # 编组：整组一起移动，撤销用按下时的快照（一次 Ctrl+Z 全回来）
        self._group_origins = []
        self._group_before = None
        members = self.bridge.group_members(seg)
        if mode == "move" and len(members) > 1:
            tracks = []
            for member in members:
                track = self.bridge.track_of_segment(member)
                if track is not None and track not in tracks and not self.bridge.is_locked(track):
                    tracks.append(track)
            self._group_before = self.bridge._snapshot(tracks)
            self._group_origins = [
                (member, int(member.target_timerange.start))
                for member in members
                if self.bridge.track_of_segment(member) in tracks
            ]
        self._invalidate_seg(seg)

    def _start_scrub(self, pos: QPointF) -> None:
        """开始拖播放头：按下就定位，移动时连续跟随。"""
        self._scrubbing = True
        self.viewport().setCursor(Qt.SplitHCursor)
        self.playheadRequested.emit(self._x_to_us(pos.x()))

    def set_tool(self, name: str) -> None:
        """切换工具：选择（点选/框选）/ 切割（点一下切开）。"""
        self.tool = "razor" if name == "razor" else "select"
        if self.tool == "razor":
            self.viewport().setCursor(Qt.CrossCursor)
            self.bridge.statusMessage.emit("切割工具：在片段上点一下就从那里切开（A 换回选择）")
        else:
            self.viewport().setCursor(Qt.ArrowCursor)
            self.bridge.statusMessage.emit("选择工具：点选 / Ctrl+点加选 / 空白处拖出框选")

    def set_row_h(self, height: int) -> None:
        """调轨道行高（紧凑 28 / 标准 40 / 高 56）。"""
        self.row_h = max(28, min(72, int(height)))
        self.refresh()
        self.bridge.statusMessage.emit(f"轨道高度：{self.row_h}px")

    def mouseMoveEvent(self, event):                    # noqa: N802
        pos = self.mapToScene(event.position().toPoint())

        if self._scrubbing:
            self.playheadRequested.emit(self._x_to_us(pos.x()))
            self.bridge.statusMessage.emit(
                f"播放头：{self.bridge.playhead / 1e6:.2f}s")
            return

        if self._band_origin is not None:
            # 框选：记录矩形并整屏重画（只发生在拖动过程中，可以接受）
            self._band_rect = QRectF(self._band_origin, pos).normalized()
            self.viewport().update()
            return

        if self._mode is None or self._drag_seg is None:
            # 悬停高亮：只重画涉及的两个片段，不整屏
            seg_hit, _mode, _index = self._hit(pos) if pos.y() >= HEADER_H else (None, None, -1)
            if seg_hit is not self._hover_seg:
                old = self._hover_seg
                self._hover_seg = seg_hit
                self._invalidate_seg(old)
                self._invalidate_seg(seg_hit)
            cursor = Qt.SizeHorCursor if _mode in ("trim_l", "trim_r") else Qt.ArrowCursor
            if self.viewport().cursor().shape() != cursor:
                self.viewport().setCursor(cursor)
            # 预览轴（工具栏开关）：鼠标扫过时间线就预览该处画面
            if getattr(self, "playhead_axis", False) and pos.y() >= HEADER_H:
                self.playheadRequested.emit(self._x_to_us(pos.x()))
            return

        delta_us = int((pos.x() - self._drag_origin.x()) / self.ppm * 1e6)
        seg = self._drag_seg
        row = self._drag_row
        before_rect = self._seg_rect(seg, row)
        tr = seg.target_timerange
        src = seg.source_timerange

        if self._mode == "move":
            new_start = max(0, self._orig_start + delta_us)
            snapped = self._snap(new_start, ignore=seg)
            tr.start = snapped
            src.start = self._orig_source_start
            # 编组：整组按同一个位移一起走
            if self._group_origins:
                shift = snapped - self._orig_start
                for member, origin in self._group_origins:
                    member.target_timerange.start = max(0, origin + shift)
            # 跨轨拖动：指针落到另一条同类轨道上就记下来，松手时真的挪过去
            row_now = self._row_at(pos.y())
            rows = self.rows()
            if 0 <= row_now < len(rows) and not self._group_origins:
                target = rows[row_now]
                if (target.type == rows[row].type and row_now != row
                        and not self.bridge.is_locked(target)):
                    self._drop_row = row_now
                elif row_now == row:
                    self._drop_row = -1
        elif self._mode == "trim_l":
            new_start = max(0, self._orig_start + delta_us)
            new_start = min(new_start, self._orig_start + self._orig_dur - 100_000)
            new_start = self._snap(new_start, ignore=seg)
            shift = new_start - self._orig_start
            tr.start = new_start
            tr.duration = max(100_000, self._orig_dur - shift)
            src.start = max(0, self._orig_source_start + shift)
        elif self._mode == "trim_r":
            tr.duration = max(100_000, self._orig_dur + delta_us)

        self._invalidate(before_rect.united(self._seg_rect(seg, row)))
        verb = "移动" if self._mode == "move" else "裁剪"
        self.bridge.statusMessage.emit(
            f"{verb}：{tr.start / 1e6:.2f}s 起 · 时长 {tr.duration / 1e6:.2f}s")

    def leaveEvent(self, event):                         # noqa: N802
        if self._hover_seg is not None:
            old = self._hover_seg
            self._hover_seg = None
            self._invalidate_seg(old)
        super().leaveEvent(event)

    def dragLeaveEvent(self, event):                     # noqa: N802
        self._drop_us = None
        self._drop_clip = None
        self.viewport().update()
        super().dragLeaveEvent(event)

    def mouseReleaseEvent(self, event):                 # noqa: N802
        if self._scrubbing:
            self._scrubbing = False
            self.viewport().setCursor(Qt.ArrowCursor)
            self.viewport().update()
            return
        if self._band_origin is not None:
            origin = self._band_origin
            rect = self._band_rect
            self._band_origin = None
            self._band_rect = None
            pos = self.mapToScene(event.position().toPoint())
            if rect is None or (abs(pos.x() - origin.x()) < 4 and abs(pos.y() - origin.y()) < 4):
                # 只是点了一下空白：定位播放头（保持老行为）
                self.playheadRequested.emit(self._x_to_us(pos.x()))
            else:
                hits = self._segments_in(rect)
                self.bridge.select_many(hits, additive=bool(
                    event.modifiers() & Qt.ControlModifier))
                if hits:
                    self.edited.emit(f"框选了 {len(hits)} 个片段")
                else:
                    self.edited.emit("框选：这一段里没有片段")
            self.viewport().update()
            return
        if self._mode is None or self._drag_seg is None:
            return
        seg = self._drag_seg
        before = (self._orig_start, self._orig_dur, self._orig_source_start)
        after = (int(seg.target_timerange.start), int(seg.target_timerange.duration),
                 int(getattr(seg.source_timerange, "start", 0) or 0))
        moved_row = getattr(self, "_drop_row", -1)
        rows = self.rows()
        if self._group_before is not None and self._mode == "move":
            # 编组整体移动：用按下时的快照撤销（一次 Ctrl+Z 全回来）
            moved = any(origin != int(member.target_timerange.start)
                        for member, origin in self._group_origins)
            if moved:
                self.bridge.record_snapshot(
                    self._group_before, f"移动编组（{len(self._group_origins)} 段）")
                self.edited.emit(f"已移动编组 {len(self._group_origins)} 段")
        elif (self._mode == "move" and 0 <= moved_row < len(rows)
                and moved_row != self._drag_row
                and rows[moved_row].type == rows[self._drag_row].type):
            # 跨轨 + 时间一起改，算成一次撤销
            self.bridge.move_segment_to_track(seg, rows[moved_row], restore=before)
            self.edited.emit("已移到另一条轨道")
        elif before != after:
            verb = "移动片段" if self._mode == "move" else "裁剪片段"
            self.bridge.record_edit(seg, before, after, verb)
            self.edited.emit(f"已{verb[:2]}")
        self._mode = None
        self._drag_seg = None
        self._drag_row = -1
        self._drop_row = -1
        self._group_origins = []
        self._group_before = None
        self.refresh()

    def mouseDoubleClickEvent(self, event):             # noqa: N802
        """双击轨头 = 重命名轨道（剪映右键里也有）。"""
        pos = event.position()
        if pos.x() < HEADER_W:
            point = pos.toPoint()
            scene_y = self.mapToScene(point).y()
            if scene_y >= HEADER_H:
                self._rename_track(self._row_at(scene_y))
                return
        super().mouseDoubleClickEvent(event)

    def _rename_track(self, row: int) -> None:
        from PySide6.QtWidgets import QInputDialog

        rows = self.rows()
        if not (0 <= row < len(rows)):
            return
        track = rows[row]
        current = track.name or CLIP_LABELS.get(track.type, track.type)
        text, ok = QInputDialog.getText(self, "重命名轨道", "轨道名称：", text=current)
        if not ok:
            return
        with self.bridge.render_lock:
            track.name = str(text).strip()[:40]
            track.is_default_name = not bool(track.name)
            self.bridge.touch()
        self.refresh()
        self.bridge.statusMessage.emit(
            f"轨道已重命名：{track.name}" if track.name else "轨道名已清空（恢复默认）")

    def contextMenuEvent(self, event):                  # noqa: N802
        """右键菜单：轨头上是轨道菜单，片段上是片段菜单，空白处是粘贴/新建。"""
        pos = self.mapToScene(event.pos())
        if event.pos().x() < HEADER_W and pos.y() >= HEADER_H:
            self._track_menu(self._row_at(pos.y()), event.globalPos())
            return
        seg, _mode, _index = self._hit(pos) if pos.y() >= HEADER_H else (None, None, -1)
        if seg is not None:
            self.segmentPicked.emit(seg)
        self.contextRequested.emit(seg, event.globalPos())

    def _build_track_menu(self, track):
        """轨头菜单（不弹出来，方便测试）：返回 (menu, 删除项, 清空项)。"""
        from PySide6.QtWidgets import QMenu

        menu = QMenu(self)
        is_main = track is self.bridge.main_track()
        delete_act = menu.addAction("删除轨道")
        delete_act.setEnabled(not is_main)
        delete_act.setToolTip("主轨不能删除" if is_main else "连同片段一起删除，可撤销")
        clear_act = menu.addAction("清空轨道")
        return menu, delete_act, clear_act

    def _track_menu(self, row: int, global_pos) -> None:
        """轨头右键：删除轨道 / 清空轨道（剪映的轨道菜单）。"""
        rows = self.rows()
        if not (0 <= row < len(rows)):
            return
        track = rows[row]
        menu, delete_act, clear_act = self._build_track_menu(track)
        chosen = menu.exec(global_pos)
        if chosen is None:
            return
        index = list(self.bridge.project.tracks).index(track)
        if chosen is delete_act:
            self.bridge.delete_track(index)
        elif chosen is clear_act:
            self.bridge.clear_track(index)
        self.refresh()

    # -- 拖拽入时间线 ------------------------------------------------------- #
    @staticmethod
    def library_payload(mime):
        """素材库拖过来的 {kind, value}（不是就返回 None）。"""
        if mime is None or not hasattr(mime, "hasFormat"):
            return None
        if not mime.hasFormat(LIBRARY_MIME):
            return None
        try:
            data = json.loads(bytes(mime.data(LIBRARY_MIME)).decode("utf-8"))
        except Exception:                              # noqa: BLE001
            return None
        if not isinstance(data, list) or not data:
            return None
        return data[0] if isinstance(data[0], dict) else None

    def _clip_at(self, pos: QPointF):
        """落点下的片段（不看裁剪把手，整块都算）。"""
        rows = self.rows()
        if pos.y() < HEADER_H:
            return None
        index = self._row_at(pos.y())
        if not (0 <= index < len(rows)):
            return None
        for seg in rows[index].segments:
            x1 = self._us_to_x(int(seg.target_timerange.start))
            x2 = self._us_to_x(int(seg.target_timerange.start + seg.target_timerange.duration))
            if x1 <= pos.x() <= x2:
                return seg
        return None

    def dragEnterEvent(self, event):                     # noqa: N802
        if self.library_payload(event.mimeData()) is not None:
            pos = self.mapToScene(event.position().toPoint())
            self._drop_clip = self._clip_at(pos)
            self.viewport().update()
            event.acceptProposedAction()
            return
        if event.mimeData().hasText() or event.mimeData().hasUrls():
            pos = self.mapToScene(event.position().toPoint())
            self._drop_us = self._snap(self._x_to_us(pos.x()))
            self.viewport().update()
            event.acceptProposedAction()

    def dragMoveEvent(self, event):                      # noqa: N802
        if self.library_payload(event.mimeData()) is not None:
            pos = self.mapToScene(event.position().toPoint())
            clip = self._clip_at(pos)
            if clip is not self._drop_clip:
                self._drop_clip = clip
                self.viewport().update()
            event.acceptProposedAction()
            return
        if event.mimeData().hasText() or event.mimeData().hasUrls():
            pos = self.mapToScene(event.position().toPoint())
            snapped = self._snap(self._x_to_us(pos.x()))
            if snapped != self._drop_us:
                self._drop_us = snapped
                self.viewport().update()
            event.acceptProposedAction()

    def dropEvent(self, event):                         # noqa: N802
        mime = event.mimeData()
        payload = self.library_payload(mime)
        if payload is not None:
            pos = self.mapToScene(event.position().toPoint())
            clip = self._clip_at(pos)
            self._drop_clip = None
            self.viewport().update()
            if clip is None:
                label = "特效" if payload.get("kind") == "effect" else "转场"
                self.bridge.statusMessage.emit(
                    f"把{label}拖到某个片段上才会生效（现在拖在空白处）")
                return
            self.libraryDropped.emit(payload, clip)
            event.acceptProposedAction()
            return
        path = ""
        if mime.hasUrls():
            path = mime.urls()[0].toLocalFile()
        elif mime.hasText():
            path = mime.text()
        if not path or not os.path.isfile(path):
            return
        ext = os.path.splitext(path)[1].lower()
        if ext in (".png", ".jpg", ".jpeg", ".bmp", ".webp"):
            kind = "image"
        elif ext in (".wav", ".mp3", ".m4a", ".aac", ".flac"):
            kind = "audio"
        else:
            kind = "video"
        pos = self.mapToScene(event.position().toPoint())
        target = self._drop_us if self._drop_us is not None else self._x_to_us(pos.x())
        track_index = self.track_index_for_drop(kind, pos)
        self._drop_us = None
        self.bridge.add_media_to_timeline(path, kind, target, track_index=track_index)
        self.viewport().update()
        event.acceptProposedAction()

    def track_index_for_drop(self, kind: str, pos: QPointF) -> int:
        """落在第几行就放到第几条同类轨道；落在空白处 = 新建一条。

        以前不管拖到哪一行都塞进第一条轨道——用户问过"是不是只能有一条视频轨"。
        """
        want = "audio" if kind == "audio" else "video"
        rows = self.rows()
        same = [t for t in self.bridge.project.tracks if t.type == want]
        if pos.y() >= HEADER_H:
            index = self._row_at(pos.y())
            if 0 <= index < len(rows) and rows[index].type == want and rows[index] in same:
                return same.index(rows[index])
        # 落在空白 / 别的类型行上：放到最后一条同类轨道**后面**（会自动新建一条）
        return len(same)

    def wheelEvent(self, event):                        # noqa: N802
        if event.modifiers() & Qt.ControlModifier:
            factor = 1.2 if event.angleDelta().y() > 0 else 1 / 1.2
            self.ppm = min(600.0, max(8.0, self.ppm * factor))
            self.refresh()
            event.accept()
            return
        super().wheelEvent(event)
