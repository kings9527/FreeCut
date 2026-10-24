"""曲线变速编辑器（剪映「变速 → 曲线变速」里那张可以拖点的图）。

设计约束：
- 只做**一张速度曲线**：横轴 = 片段内位置 0..1，纵轴 = 速度倍数（0.1×..4×）；
- 点可以直接拖；双击空白加点；右键点删点（首尾两个不能删）；
- 数值语义完全交给 :mod:`freecut.speed`（``normalize`` / ``speed_at``），
  渲染端读的是 ``Segment.source_time`` → ``speed.source_fraction``，所以这里改完立刻生效。
"""

from __future__ import annotations

from typing import List, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ... import speed as speed_mod
from ... import theme

#: 纵轴范围（剪映的曲线变速是 0.1× 到 4×）
MIN_SPEED = 0.1
MAX_SPEED = 4.0
PAD = 18                     # 上下留白
HIT_RADIUS = 9               # 点中一个控制点的像素半径


class CurveEditor(QWidget):
    """可拖点的速度曲线。``pointsChanged`` 带的是 ``[(x, y), …]``。"""

    pointsChanged = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(150)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMouseTracking(True)
        self._points: List[Tuple[float, float]] = [(0.0, 1.0), (1.0, 1.0)]
        self._drag = -1
        self._hover = -1
        self.setToolTip("拖控制点改速度；双击空白加点；右键点删点")

    # -- 数据 ------------------------------------------------------------- #
    def points(self) -> List[Tuple[float, float]]:
        return list(self._points)

    def set_points(self, points) -> None:
        pts = speed_mod.normalize([{"x": x, "y": y} for x, y in points or []])
        if len(pts) < 2:
            pts = [(0.0, 1.0), (1.0, 1.0)]
        self._points = pts
        self.update()

    # -- 坐标 ------------------------------------------------------------- #
    def _plot_rect(self) -> QRectF:
        return QRectF(PAD, PAD, max(10, self.width() - PAD * 2),
                      max(10, self.height() - PAD * 2 - 8))

    def _to_px(self, x: float, y: float) -> QPointF:
        rect = self._plot_rect()
        ratio = (max(MIN_SPEED, min(MAX_SPEED, y)) - MIN_SPEED) / (MAX_SPEED - MIN_SPEED)
        return QPointF(rect.left() + x * rect.width(),
                       rect.bottom() - ratio * rect.height())

    def _from_px(self, pos: QPointF) -> Tuple[float, float]:
        rect = self._plot_rect()
        x = (pos.x() - rect.left()) / max(1.0, rect.width())
        ratio = (rect.bottom() - pos.y()) / max(1.0, rect.height())
        y = MIN_SPEED + ratio * (MAX_SPEED - MIN_SPEED)
        return min(1.0, max(0.0, x)), min(MAX_SPEED, max(MIN_SPEED, y))

    def _nearest(self, pos: QPointF) -> int:
        for index, (x, y) in enumerate(self._points):
            if (self._to_px(x, y) - pos).manhattanLength() <= HIT_RADIUS:
                return index
        return -1

    # -- 绘制 ------------------------------------------------------------- #
    def paintEvent(self, event):                        # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = self._plot_rect()
        painter.fillRect(self.rect(), QColor("#17171c"))
        painter.fillRect(rect, QColor("#1e1e25"))

        # 网格：横 4 格、纵每 1× 一条
        painter.setPen(QPen(QColor("#2b2b34"), 1))
        for i in range(1, 4):
            x = rect.left() + rect.width() * i / 4
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
        speed = 1.0
        while speed <= MAX_SPEED:
            y = self._to_px(0, speed).y()
            painter.setPen(QPen(QColor("#2b2b34" if speed != 1.0 else "#3d5a5f"), 1,
                                Qt.DashLine if speed != 1.0 else Qt.SolidLine))
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            painter.setPen(QColor(theme.FG_FAINT))
            painter.setFont(QFont("Consolas", 7))
            painter.drawText(QPointF(2, y - 2), f"{speed:g}×")
            speed += 1.0

        # 曲线（按 speed_at 采样，和渲染用的是同一个函数）
        painter.setPen(QPen(QColor(theme.ACCENT), 2))
        steps = 96
        prev = None
        for i in range(steps + 1):
            frac = i / steps
            y = speed_mod.speed_at(self._points, frac)
            point = self._to_px(frac, y)
            if prev is not None:
                painter.drawLine(prev, point)
            prev = point

        # 控制点
        for index, (x, y) in enumerate(self._points):
            centre = self._to_px(x, y)
            radius = 6 if index in (self._hover, self._drag) else 4.5
            painter.setPen(QPen(QColor("#0f0f12"), 1))
            painter.setBrush(QColor(theme.ACCENT if index != self._drag else "#ffffff"))
            painter.drawEllipse(centre, radius, radius)

        painter.setPen(QColor(theme.FG_DIM))
        painter.setFont(QFont("Microsoft YaHei", 7))
        painter.drawText(QPointF(rect.left(), self.height() - 4), "开头")
        painter.drawText(QPointF(rect.right() - 26, self.height() - 4), "结尾")

    # -- 交互 ------------------------------------------------------------- #
    def mousePressEvent(self, event):                   # noqa: N802
        pos = event.position()
        index = self._nearest(pos)
        if event.button() == Qt.RightButton and index >= 0:
            if 0 < index < len(self._points) - 1:
                self._points.pop(index)
                self.update()
                self.pointsChanged.emit(self.points())
            return
        if index >= 0:
            self._drag = index
            self.update()

    def mouseMoveEvent(self, event):                    # noqa: N802
        pos = event.position()
        if self._drag < 0:
            hover = self._nearest(pos)
            if hover != self._hover:
                self._hover = hover
                self.update()
            return
        x, y = self._from_px(pos)
        index = self._drag
        if index == 0:
            x = 0.0
        elif index == len(self._points) - 1:
            x = 1.0
        else:
            left = self._points[index - 1][0] + 0.01
            right = self._points[index + 1][0] - 0.01
            x = min(right, max(left, x))
        self._points[index] = (x, y)
        self.update()

    def mouseReleaseEvent(self, event):                 # noqa: N802
        if self._drag >= 0:
            self._drag = -1
            self._points = speed_mod.normalize(
                [{"x": x, "y": y} for x, y in self._points])
            self.update()
            self.pointsChanged.emit(self.points())

    def mouseDoubleClickEvent(self, event):             # noqa: N802
        pos = event.position()
        if self._nearest(pos) >= 0:
            return
        x, y = self._from_px(pos)
        self._points.append((x, y))
        self._points = speed_mod.normalize([{"x": px, "y": py} for px, py in self._points])
        self.update()
        self.pointsChanged.emit(self.points())
