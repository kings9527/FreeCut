"""Small interactive widgets used by the properties panel.

* :class:`CurveEditor` - draggable RGB / per-channel tone curve
* :class:`WheelPad`    - a 2D lift / gamma / gain pad

Both are plain ``tk.Canvas`` subclasses so they work on any Tk install without
extra dependencies.
"""

from __future__ import annotations

import math
from typing import Callable, List, Optional, Sequence, Tuple

import tkinter as tk

Point = Tuple[float, float]


class CurveEditor(tk.Canvas):
    """A tone-curve editor.

    ``points`` are in 0..1 with y up (0 = black at the bottom).  Click to add a
    point, drag to move, right-click to remove.  The first and last points are
    pinned horizontally.
    """

    PAD = 8

    def __init__(self, master, on_change: Optional[Callable[[List[Point]], None]] = None,
                 width: int = 240, height: int = 160, **kw):
        super().__init__(master, width=width, height=height, bg="#15151a",
                         highlightthickness=1, highlightbackground="#2e2e36", **kw)
        self.on_change = on_change
        self.points: List[Point] = [(0.0, 0.0), (0.5, 0.5), (1.0, 1.0)]
        self._drag: Optional[int] = None
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Button-3>", self._on_right)
        self.bind("<Configure>", lambda e: self.redraw())

    # -- geometry -----------------------------------------------------------
    def _box(self):
        w = max(20, self.winfo_width())
        h = max(20, self.winfo_height())
        return self.PAD, self.PAD, w - self.PAD, h - self.PAD

    def _to_px(self, p: Point) -> Tuple[float, float]:
        x0, y0, x1, y1 = self._box()
        return x0 + p[0] * (x1 - x0), y1 - p[1] * (y1 - y0)

    def _to_val(self, x: float, y: float) -> Point:
        x0, y0, x1, y1 = self._box()
        px = (x - x0) / max(1e-6, (x1 - x0))
        py = (y1 - y) / max(1e-6, (y1 - y0))
        return (min(1.0, max(0.0, px)), min(1.0, max(0.0, py)))

    # -- drawing ------------------------------------------------------------
    def redraw(self) -> None:
        self.delete("all")
        x0, y0, x1, y1 = self._box()
        for i in range(1, 4):
            x = x0 + (x1 - x0) * i / 4.0
            y = y0 + (y1 - y0) * i / 4.0
            self.create_line(x, y0, x, y1, fill="#24242c")
            self.create_line(x0, y, x1, y, fill="#24242c")
        self.create_line(x0, y1, x1, y0, fill="#2a2a34", dash=(3, 3))

        ordered = sorted(self.points)
        for a, b in zip(ordered, ordered[1:]):
            self.create_line(*self._to_px(a), *self._to_px(b), fill="#4a8fd0", width=2)
        for i, p in enumerate(self.points):
            px, py = self._to_px(p)
            r = 4
            self.create_oval(px - r, py - r, px + r, py + r,
                             fill="#e0a020" if i == self._drag else "#7ec0ff",
                             outline="#101014")

    # -- interaction --------------------------------------------------------
    def _hit(self, x: float, y: float) -> Optional[int]:
        for i, p in enumerate(self.points):
            px, py = self._to_px(p)
            if (px - x) ** 2 + (py - y) ** 2 <= 64:
                return i
        return None

    def _on_press(self, ev) -> None:
        idx = self._hit(ev.x, ev.y)
        if idx is None:
            vx, vy = self._to_val(ev.x, ev.y)
            self.points.append((vx, vy))
            self.points.sort()
            idx = self.points.index((vx, vy))
        self._drag = idx
        self.redraw()

    def _on_drag(self, ev) -> None:
        if self._drag is None:
            return
        vx, vy = self._to_val(ev.x, ev.y)
        i = self._drag
        if i == 0:
            vx = 0.0
        elif i == len(self.points) - 1:
            vx = 1.0
        else:
            lo = self.points[i - 1][0] + 0.01
            hi = self.points[i + 1][0] - 0.01
            vx = min(hi, max(lo, vx))
        self.points[i] = (vx, vy)
        self.redraw()
        self._emit()

    def _on_release(self, ev) -> None:
        self._drag = None
        self.redraw()
        self._emit()

    def _on_right(self, ev) -> None:
        idx = self._hit(ev.x, ev.y)
        if idx is None or idx in (0, len(self.points) - 1) or len(self.points) <= 2:
            return
        self.points.pop(idx)
        self.redraw()
        self._emit()

    def _emit(self) -> None:
        if self.on_change:
            self.on_change(sorted(self.points))

    # -- api ----------------------------------------------------------------
    def set_points(self, points: Sequence[Point]) -> None:
        pts = [(float(x), float(y)) for x, y in points if len((x, y)) == 2]
        if len(pts) < 2:
            pts = [(0.0, 0.0), (1.0, 1.0)]
        self.points = sorted(pts)
        self.redraw()

    def get_points(self) -> List[Point]:
        return sorted(self.points)

    def reset(self) -> None:
        self.set_points([(0.0, 0.0), (0.5, 0.5), (1.0, 1.0)])


class WheelPad(tk.Canvas):
    """A round colour-balance pad: the dot's offset is returned in [-1, 1]."""

    def __init__(self, master, color: str = "#7ec0ff",
                 on_change: Optional[Callable[[float, float], None]] = None,
                 size: int = 96, **kw):
        super().__init__(master, width=size, height=size, bg="#15151a",
                         highlightthickness=0, **kw)
        self.size = size
        self.color = color
        self.on_change = on_change
        self.offset = (0.0, 0.0)
        self._dragging = False
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Double-Button-1>", lambda e: self.set_offset(0.0, 0.0))
        self.bind("<Configure>", lambda e: self.redraw())

    def _centre(self):
        w = max(20, self.winfo_width())
        h = max(20, self.winfo_height())
        return w / 2.0, h / 2.0, min(w, h) / 2.0 - 6

    def _to_offset(self, x: float, y: float) -> Tuple[float, float]:
        cx, cy, r = self._centre()
        ox = (x - cx) / max(1e-6, r)
        oy = (cy - y) / max(1e-6, r)
        d = math.hypot(ox, oy)
        if d > 1.0:
            ox, oy = ox / d, oy / d
        return ox, oy

    def redraw(self) -> None:
        self.delete("all")
        cx, cy, r = self._centre()
        self.create_oval(cx - r, cy - r, cx + r, cy + r, outline="#2e2e36")
        for k in (0.33, 0.66):
            self.create_oval(cx - r * k, cy - r * k, cx + r * k, cy + r * k, outline="#24242c")
        self.create_line(cx - r, cy, cx + r, cy, fill="#24242c")
        self.create_line(cx, cy - r, cx, cy + r, fill="#24242c")
        dx, dy = self.offset
        px, py = cx + dx * r, cy - dy * r
        self.create_oval(px - 6, py - 6, px + 6, py + 6, fill=self.color, outline="#101014")

    def _on_press(self, ev) -> None:
        self._dragging = True
        self._apply(ev.x, ev.y)

    def _on_drag(self, ev) -> None:
        if self._dragging:
            self._apply(ev.x, ev.y)

    def _on_release(self, ev) -> None:
        self._dragging = False

    def _apply(self, x: float, y: float) -> None:
        self.offset = self._to_offset(x, y)
        self.redraw()
        if self.on_change:
            self.on_change(*self.offset)

    def set_offset(self, x: float, y: float) -> None:
        self.offset = (float(x), float(y))
        self.redraw()

    def get_offset(self) -> Tuple[float, float]:
        return self.offset
