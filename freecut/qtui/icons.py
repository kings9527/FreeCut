"""左栏类别图标：全部用 QPainter 现画。

为什么不放 PNG：剪映的图标是单色线性图标，按状态换色；贴图就得为每个颜色
各存一份。现画只依赖画笔颜色，尺寸也能跟着 DPI 走。
"""

from __future__ import annotations

from typing import Dict

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap


def _canvas(size: int) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    return pixmap


def _painter(pixmap: QPixmap, colour: str) -> QPainter:
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    pen = QPen(QColor(colour))
    pen.setWidthF(max(1.0, pixmap.width() / 14.0))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    return painter


def _grid(painter: QPainter, size: int) -> None:
    """素材：四宫格。"""
    pad = size * 0.22
    cell = (size - pad * 2) / 2 - size * 0.04
    for row in range(2):
        for col in range(2):
            x = pad + col * (cell + size * 0.08)
            y = pad + row * (cell + size * 0.08)
            painter.drawRoundedRect(QRectF(x, y, cell, cell), 2, 2)


def _note(painter: QPainter, size: int) -> None:
    """音频：八分音符。"""
    cx, cy = size * 0.42, size * 0.70
    painter.drawEllipse(QPointF(cx, cy), size * 0.12, size * 0.09)
    painter.drawLine(QPointF(cx + size * 0.11, cy), QPointF(cx + size * 0.11, size * 0.24))
    path = QPainterPath(QPointF(cx + size * 0.11, size * 0.24))
    path.quadTo(QPointF(cx + size * 0.36, size * 0.30),
                QPointF(cx + size * 0.28, size * 0.44))
    painter.drawPath(path)


def _letter_t(painter: QPainter, size: int) -> None:
    """文本：T 字。"""
    painter.drawLine(QPointF(size * 0.26, size * 0.28), QPointF(size * 0.74, size * 0.28))
    painter.drawLine(QPointF(size * 0.50, size * 0.28), QPointF(size * 0.50, size * 0.76))


def _sticker(painter: QPainter, size: int) -> None:
    """贴纸：圆脸 + 两点。"""
    painter.drawEllipse(QPointF(size / 2, size / 2), size * 0.26, size * 0.26)
    painter.setBrush(QColor(painter.pen().color()))
    painter.drawEllipse(QPointF(size * 0.42, size * 0.44), size * 0.035, size * 0.035)
    painter.drawEllipse(QPointF(size * 0.58, size * 0.44), size * 0.035, size * 0.035)
    painter.setBrush(Qt.NoBrush)
    painter.drawArc(QRectF(size * 0.38, size * 0.46, size * 0.24, size * 0.20), 200 * 16, 140 * 16)


def _sparkle(painter: QPainter, size: int) -> None:
    """特效：四角星。"""
    cx, cy = size / 2, size / 2
    r, inner = size * 0.30, size * 0.09
    path = QPainterPath(QPointF(cx, cy - r))
    for dx, dy in ((inner, -inner), (r, 0), (inner, inner), (0, r),
                   (-inner, inner), (-r, 0), (-inner, -inner)):
        path.lineTo(QPointF(cx + dx, cy + dy))
    path.closeSubpath()
    painter.drawPath(path)


def _transition(painter: QPainter, size: int) -> None:
    """转场：两个交叠的方块。"""
    painter.drawRoundedRect(QRectF(size * 0.18, size * 0.30, size * 0.34, size * 0.34), 2, 2)
    painter.drawRoundedRect(QRectF(size * 0.48, size * 0.40, size * 0.34, size * 0.34), 2, 2)


def _caption(painter: QPainter, size: int) -> None:
    """字幕：方框里两条横线。"""
    painter.drawRoundedRect(QRectF(size * 0.18, size * 0.30, size * 0.64, size * 0.40), 3, 3)
    painter.drawLine(QPointF(size * 0.30, size * 0.44), QPointF(size * 0.70, size * 0.44))
    painter.drawLine(QPointF(size * 0.30, size * 0.56), QPointF(size * 0.58, size * 0.56))


PAINTERS: Dict[str, callable] = {
    "素材": _grid,
    "音频": _note,
    "文本": _letter_t,
    "贴纸": _sticker,
    "特效": _sparkle,
    "转场": _transition,
    "字幕": _caption,
}


def icon(name: str, size: int = 20, colour: str = "#e8e8ef") -> QPixmap:
    pixmap = _canvas(size)
    painter = _painter(pixmap, colour)
    try:
        painter_fn = PAINTERS.get(name)
        if painter_fn is not None:
            painter_fn(painter, size)
    finally:
        painter.end()
    return pixmap


def qicon(name: str, size: int = 20, colour: str = "#e8e8ef",
          active: str = "#0f0f12") -> QIcon:
    """带选中态变色的图标（选中时底是青色，图标得用深色）。"""
    result = QIcon()
    result.addPixmap(icon(name, size, colour), QIcon.Normal, QIcon.Off)
    result.addPixmap(icon(name, size, active), QIcon.Normal, QIcon.On)
    result.addPixmap(icon(name, size, active), QIcon.Selected, QIcon.On)
    return result
