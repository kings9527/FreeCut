"""Curve speed (曲线变速).

JianyingPro models this as a ``MaterialSpeed`` with ``mode`` = curve and a
``curve_speed`` node whose points are ``{"x", "y"}`` — verified from the
``CurveSpeed@lvve`` / ``SpeedPoint@lvve`` class layout, where ``SpeedPoint``
exposes exactly ``x`` and ``y`` (see ``docs/REPORT.md`` §D.3).

FreeCut's interpretation, stated explicitly because it cannot be confirmed
without a real curve-speed draft to compare against:

* ``x`` is the **normalised position inside the clip** (0 = clip start,
  1 = clip end);
* ``y`` is the **speed multiplier** at that position (1.0 = normal).

The mapper integrates that multiplier so the clip still consumes exactly its
``source_timerange``: a curve only *redistributes* the source across the clip.
"""

from __future__ import annotations

from typing import Iterable, List, Sequence, Tuple

Point = Tuple[float, float]

#: integration resolution — 256 slices is well under one frame at any sane fps
_STEPS = 256


def normalize(points: Iterable[dict]) -> List[Point]:
    """Coerce ``[{"x","y"}, …]`` into a sorted, de-duplicated point list."""
    out: List[Point] = []
    for p in points or []:
        try:
            x = float(p.get("x", 0.0))
            y = float(p.get("y", 1.0))
        except (AttributeError, TypeError, ValueError):
            continue
        out.append((min(1.0, max(0.0, x)), max(0.01, y)))
    out.sort(key=lambda p: p[0])
    merged: List[Point] = []
    for x, y in out:
        if merged and abs(merged[-1][0] - x) < 1e-6:
            merged[-1] = (x, y)
        else:
            merged.append((x, y))
    return merged


def speed_at(points: Sequence[Point], x: float) -> float:
    """Piecewise-linear speed multiplier at normalised position ``x``."""
    if not points:
        return 1.0
    x = min(1.0, max(0.0, x))
    if x <= points[0][0]:
        return points[0][1]
    if x >= points[-1][0]:
        return points[-1][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x0 <= x <= x1:
            if x1 - x0 < 1e-9:
                return y1
            t = (x - x0) / (x1 - x0)
            return y0 + (y1 - y0) * t
    return points[-1][1]


def cumulative(points: Sequence[Point], steps: int = _STEPS) -> List[float]:
    """Cumulative integral of the speed curve, normalised to end at 1.0."""
    if not points:
        return [i / steps for i in range(steps + 1)]
    acc = [0.0]
    total = 0.0
    for i in range(steps):
        x0 = i / steps
        x1 = (i + 1) / steps
        v = 0.5 * (speed_at(points, x0) + speed_at(points, x1))
        total += v / steps
        acc.append(total)
    if total <= 1e-9:
        return [i / steps for i in range(steps + 1)]
    return [a / total for a in acc]


def source_fraction(points: Sequence[Point], x: float, table: Sequence[float] = None) -> float:
    """Fraction of the source consumed by normalised position ``x``."""
    if not points:
        return min(1.0, max(0.0, x))
    table = table if table is not None else cumulative(points)
    x = min(1.0, max(0.0, x))
    pos = x * (len(table) - 1)
    i = int(pos)
    if i >= len(table) - 1:
        return table[-1]
    frac = pos - i
    return table[i] * (1.0 - frac) + table[i + 1] * frac


def average_speed(points: Sequence[Point]) -> float:
    """Mean multiplier over the clip (used for audio, which cannot follow a curve)."""
    if not points:
        return 1.0
    steps = _STEPS
    total = 0.0
    for i in range(steps):
        x0, x1 = i / steps, (i + 1) / steps
        total += 0.5 * (speed_at(points, x0) + speed_at(points, x1)) / steps
    return total


def to_cube_points(points: Sequence[Point]) -> List[dict]:
    return [{"x": round(x, 6), "y": round(y, 6)} for x, y in points]


# --------------------------------------------------------------------------- #
# editor <-> model mapping (the GUI works in 0..1 for both axes)
# --------------------------------------------------------------------------- #
SPEED_MIN = 0.25
SPEED_MAX = 4.0


def to_display(points: Sequence[Point]) -> List[Point]:
    """Map (x, speed) to (x, 0..1) for the curve widget."""
    span = SPEED_MAX - SPEED_MIN
    return [(x, min(1.0, max(0.0, (y - SPEED_MIN) / span))) for x, y in points or []]


def from_display(points: Sequence[Point]) -> List[Point]:
    """Map (x, 0..1) back to (x, speed)."""
    span = SPEED_MAX - SPEED_MIN
    return normalize([{"x": x, "y": SPEED_MIN + v * span} for x, v in points or []])


#: 几个常用预设（x, speed）
PRESETS = {
    "匀速": [(0.0, 1.0), (1.0, 1.0)],
    "慢 → 快": [(0.0, 0.5), (1.0, 2.5)],
    "快 → 慢": [(0.0, 2.5), (1.0, 0.5)],
    "慢-快-慢": [(0.0, 0.5), (0.5, 2.5), (1.0, 0.5)],
    "快-慢-快": [(0.0, 2.5), (0.5, 0.5), (1.0, 2.5)],
}
