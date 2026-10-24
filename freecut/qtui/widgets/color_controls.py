"""自绘的调色控件：色调曲线、色轮。

剪映的曲线可以拖动控制点，色轮是"圆盘选色 + 旁边一根亮度"。Tk 那版用的是
画布重绘，这里用 QWidget 自绘 + 鼠标事件，逻辑一样但不用手算坐标偏移。
"""

from __future__ import annotations

import math
from typing import List, Optional, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QHBoxLayout, QToolButton, QWidget

from ... import theme

Point = Tuple[float, float]


class CurveWidget(QWidget):
    """一条 0..1 的色调曲线。左键加/拖控制点，右键删点。"""

    changed = Signal(list)                 # [(x, y), ...]

    def __init__(self, colour: str = "#e8e8ef", parent=None) -> None:
        super().__init__(parent)
        self.setMinimumSize(180, 180)
        self.points: List[Point] = [(0.0, 0.0), (1.0, 1.0)]
        self._drag: Optional[int] = None
        self._colour = colour

    # -- 外部 ------------------------------------------------------------- #
    def set_points(self, points) -> None:
        cleaned: List[Point] = []
        for item in points or []:
            try:
                x, y = float(item[0]), float(item[1])
            except (TypeError, ValueError, IndexError):
                continue
            cleaned.append((min(1.0, max(0.0, x)), min(1.0, max(0.0, y))))
        if len(cleaned) < 2:
            cleaned = [(0.0, 0.0), (1.0, 1.0)]
        self.points = sorted(cleaned)
        self.update()

    def reset(self) -> None:
        self.points = [(0.0, 0.0), (1.0, 1.0)]
        self.update()
        self.changed.emit(self.points)

    # -- 坐标 ------------------------------------------------------------- #
    def _plot_rect(self) -> QRectF:
        return QRectF(8, 8, self.width() - 16, self.height() - 16)

    def _to_px(self, point: Point) -> QPointF:
        rect = self._plot_rect()
        x = rect.left() + point[0] * rect.width()
        y = rect.bottom() - point[1] * rect.height()
        return QPointF(x, y)

    def _to_unit(self, pos: QPointF) -> Point:
        rect = self._plot_rect()
        x = (pos.x() - rect.left()) / max(1.0, rect.width())
        y = (rect.bottom() - pos.y()) / max(1.0, rect.height())
        return (min(1.0, max(0.0, x)), min(1.0, max(0.0, y)))

    # -- 绘制 ------------------------------------------------------------- #
    def paintEvent(self, event) -> None:                # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = self._plot_rect()
        painter.fillRect(rect, QColor("#15151a"))

        painter.setPen(QPen(QColor("#2e2e36"), 1))
        for i in range(1, 4):
            x = rect.left() + rect.width() * i / 4
            y = rect.top() + rect.height() * i / 4
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
        painter.setPen(QPen(QColor("#3a3a46"), 1))
        painter.drawRect(rect)

        painter.setPen(QPen(QColor(self._colour), 2))
        poly = QPolygonF([self._to_px(p) for p in self.points])
        painter.drawPolyline(poly)

        painter.setBrush(QColor("#ffffff"))
        painter.setPen(QPen(QColor("#0f0f12"), 1))
        for point in self.points:
            painter.drawEllipse(self._to_px(point), 4, 4)

        painter.setPen(QColor(theme.FG_DIM))
        painter.setFont(QFont("Consolas", 8))
        painter.drawText(QPointF(rect.left() + 2, rect.bottom() - 4), "暗")
        painter.drawText(QPointF(rect.right() - 16, rect.bottom() - 4), "亮")

    # -- 交互 ------------------------------------------------------------- #
    def mousePressEvent(self, event) -> None:           # noqa: N802
        pos = event.position()
        for index, point in enumerate(self.points):
            delta = self._to_px(point) - pos
            if abs(delta.x()) <= 7 and abs(delta.y()) <= 7:
                if event.button() == Qt.RightButton and len(self.points) > 2:
                    self.points.pop(index)
                    self.update()
                    self.changed.emit(self.points)
                    return
                self._drag = index
                return
        if event.button() == Qt.LeftButton:
            unit = self._to_unit(pos)
            self.points.append(unit)
            self.points.sort()
            self._drag = self.points.index(unit)
            self.update()

    def mouseMoveEvent(self, event) -> None:            # noqa: N802
        if self._drag is None:
            return
        unit = self._to_unit(event.position())
        # 首尾两点只允许上下动，保证曲线覆盖 0..1
        if self._drag == 0:
            unit = (0.0, unit[1])
        elif self._drag == len(self.points) - 1:
            unit = (1.0, unit[1])
        else:
            left = self.points[self._drag - 1][0] + 0.01
            right = self.points[self._drag + 1][0] - 0.01
            unit = (min(right, max(left, unit[0])), unit[1])
        self.points[self._drag] = unit
        self.update()

    def mouseReleaseEvent(self, event) -> None:         # noqa: N802
        if self._drag is not None:
            self._drag = None
            self.changed.emit(self.points)


class WheelWidget(QWidget):
    """色轮：角度=色相，半径=饱和度，旁边一根竖条=亮度偏移。"""

    changed = Signal(float, float, float)   # (r, g, b) 偏移，-0.5..0.5

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumSize(150, 150)
        self.setMaximumHeight(190)
        self._point = QPointF(0.0, 0.0)      # 归一化 -1..1（圆心为 0）
        self._lift = 0.0                     # 亮度偏移 -0.5..0.5

    # -- 颜色换算 --------------------------------------------------------- #
    def rgb(self) -> Tuple[float, float, float]:
        x, y = self._point.x(), self._point.y()
        radius = min(1.0, math.hypot(x, y))
        angle = math.degrees(math.atan2(y, x)) % 360.0
        r, g, b = _hsv_to_rgb(angle, radius, self._lift)
        return (r, g, b)

    def set_rgb(self, value) -> None:
        try:
            r, g, b = [float(v) for v in value][:3]
        except (TypeError, ValueError):
            return
        hue, sat, val = _rgb_to_hsv(r, g, b)
        radius = sat
        angle = math.radians(hue)
        self._point = QPointF(math.cos(angle) * radius, math.sin(angle) * radius)
        self._lift = val
        self.update()

    def set_lift(self, value: float) -> None:
        self._lift = max(-0.5, min(0.5, float(value)))
        self.update()

    # -- 绘制 ------------------------------------------------------------- #
    def paintEvent(self, event) -> None:                # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        side = min(self.width(), self.height())
        rect = QRectF((self.width() - side) / 2 + 4, 4, side - 8, side - 8)
        centre = rect.center()
        radius = rect.width() / 2

        gradient = QColor(0, 0, 0)
        gradient.setAlpha(0)
        painter.setPen(QPen(QColor("#2e2e36"), 1))
        painter.setBrush(gradient)
        painter.drawEllipse(centre, radius, radius)

        # 色相环：用 72 段扇形近似
        painter.setPen(Qt.NoPen)
        for i in range(72):
            angle0 = i * 5
            colour = QColor.fromHsv(int(angle0), 220, 255)
            painter.setBrush(colour)
            painter.drawPie(rect.toRect(), int(angle0 * 16), int(5 * 16))

        painter.setBrush(QColor(20, 20, 24, 210))
        painter.drawEllipse(centre, radius * 0.72, radius * 0.72)

        handle = centre + QPointF(self._point.x() * radius, self._point.y() * radius)
        painter.setBrush(QColor("#ffffff"))
        painter.setPen(QPen(QColor("#0f0f12"), 2))
        painter.drawEllipse(handle, 5, 5)

    # -- 交互 ------------------------------------------------------------- #
    def mousePressEvent(self, event) -> None:           # noqa: N802
        self._update_from(event.position())

    def mouseMoveEvent(self, event) -> None:            # noqa: N802
        if event.buttons() & Qt.LeftButton:
            self._update_from(event.position())

    def _update_from(self, pos: QPointF) -> None:
        side = min(self.width(), self.height())
        rect = QRectF((self.width() - side) / 2 + 4, 4, side - 8, side - 8)
        centre = rect.center()
        radius = max(1.0, rect.width() / 2)
        dx = (pos.x() - centre.x()) / radius
        dy = (pos.y() - centre.y()) / radius
        length = math.hypot(dx, dy)
        if length > 1.0:
            dx, dy = dx / length, dy / length
        self._point = QPointF(dx, dy)
        self.update()
        self.changed.emit(*self.rgb())


def _hsv_to_rgb(angle: float, saturation: float, value: float) -> Tuple[float, float, float]:
    """把色轮上的位置换成 -0.5..0.5 的 RGB 偏移。"""
    import colorsys

    r, g, b = colorsys.hsv_to_rgb((angle % 360.0) / 360.0, 1.0, 1.0)
    scale = 0.5 * max(0.0, min(1.0, saturation))
    return ((r - 1 / 3) * scale, (g - 1 / 3) * scale, (b - 1 / 3) * scale + value * 0.5)


def _rgb_to_hsv(r: float, g: float, b: float) -> Tuple[float, float, float]:
    import colorsys

    base = 1 / 3
    rr, gg, bb = r + base, g + base, b + base
    hue, sat, val = colorsys.rgb_to_hsv(max(0.0, min(1.0, rr)),
                                        max(0.0, min(1.0, gg)),
                                        max(0.0, min(1.0, bb)))
    return (hue * 360.0, sat, val)


# ---------------------------------------------------------------- 色圈 / 渐变

def band_colour(hue_deg: float):
    """把 HSL 色带的角度变成显示用颜色（饱和一点，便于分辨）。"""
    from PySide6.QtGui import QColor

    return QColor.fromHsvF(max(0.0, min(1.0, hue_deg / 360.0)), 0.85, 0.95)


class ColorSwatchRow(QWidget):
    """一排圆形色块：剪映 HSL 页那种（红橙黄绿青蓝紫洋红）。"""

    bandPicked = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._buttons = {}
        self._current = ""
        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(0, 2, 0, 2)
        self._row.setSpacing(6)
        self.setMinimumHeight(26)

    def set_bands(self, bands) -> None:
        for button in self._buttons.values():
            button.deleteLater()
        self._buttons.clear()
        for key, hue in bands:
            button = QToolButton(self)
            button.setCheckable(True)
            button.setAutoRaise(True)
            button.setFixedSize(24, 24)
            colour = band_colour(float(hue))
            button.setStyleSheet(
                "QToolButton{border:2px solid transparent;border-radius:12px;"
                f"background:{colour.name()};}}"
                "QToolButton:checked{border:2px solid #ffffff;}")
            button.setToolTip(key)
            button.clicked.connect(lambda _c=False, k=str(key): self._pick(k))
            self._row.addWidget(button)
            self._buttons[str(key)] = button
        self._row.addStretch(1)

    def _pick(self, key: str) -> None:
        self.set_current(key)
        self.bandPicked.emit(key)

    def set_current(self, key: str) -> None:
        self._current = str(key)
        for name, button in self._buttons.items():
            button.setChecked(name == self._current)

    def current(self) -> str:
        return self._current

    def count(self) -> int:
        return len(self._buttons)

    def click(self, key: str) -> None:
        """测试/脚本用：等价于用户点了一下。"""
        if key in self._buttons:
            self._buttons[key].click()


def gradient_stops(kind: str, hue_deg: float = 0.0):
    """返回 [(位置0-1, QColor), …]：色相彩虹 / 饱和度 / 亮度。"""
    from PySide6.QtGui import QColor

    if kind == "hue":
        return [(0.00, QColor("#ff2d2d")), (0.17, QColor("#ffd52d")),
                (0.33, QColor("#39d94a")), (0.50, QColor("#26d7d7")),
                (0.66, QColor("#2d6bff")), (0.83, QColor("#c22dff")),
                (1.00, QColor("#ff2d2d"))]
    if kind == "sat":
        base = band_colour(hue_deg)
        grey = QColor(base)
        grey.setHsvF(base.hsvHueF(), 0.05, 0.55)
        return [(0.0, grey), (1.0, base)]
    return [(0.0, QColor("#0a0a0c")), (1.0, QColor("#f5f5f7"))]


def gradient_qss(stops) -> str:
    """把色标变成 QSlider 槽的 qlineargradient 样式。"""
    parts = []
    for position, colour in stops:
        parts.append(f"stop:{position:.3f} {colour.name()}")
    gradient = "qlineargradient(x1:0,y1:0,x2:1,y2:0," + ",".join(parts) + ")"
    return ("QSlider::groove:horizontal{height:8px;border-radius:4px;"
            f"background:{gradient};}}"
            "QSlider::handle:horizontal{width:12px;margin:-3px 0;"
            "border-radius:6px;background:#ffffff;}"
            "QSlider::sub-page:horizontal{background:transparent;}"
            "QSlider::add-page:horizontal{background:transparent;}")


def apply_gradient(slider, stops) -> None:
    """给 SliderRow.slider 这类 QSlider 套渐变槽。"""
    slider.setStyleSheet(gradient_qss(stops))
