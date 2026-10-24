"""Keyframe evaluation.

JianyingPro stores keyframes per property inside ``common_keyframes``::

    {"property_type": "KFTypePositionX",
     "keyframe_list": [{"time_offset": 0, "values": [0.0],
                        "curveType": "Line", "graphID": "", ...}]}

``time_offset`` is relative to the segment start.  ``curveType`` selects the
easing applied on the interval *starting* at that keyframe.
"""

from __future__ import annotations

from bisect import bisect_right
from typing import List, Optional, Sequence, Tuple

from .model import Keyframe

#: ``curveType`` in a draft may only be one of five values; map friendly aliases.
CURVE_ALIASES = {
    "line": "CurveInOut",
    "linear": "CurveInOut",
    "easein": "CurveIn",
    "ease_in": "CurveIn",
    "easeinout": "CurveInOut",
    "ease_in_out": "CurveInOut",
    "bezier": "CurveInOut",
    "easeout": "FreeCurveOut",
    "ease_out": "FreeCurveOut",
    "hold": "CurveIn",
    "constant": "CurveIn",
    "step": "CurveIn",
}


def normalize_curve(name: str) -> str:
    """Coerce any friendly easing name into one of the app's five values."""
    if not name:
        return "CurveInOut"
    if name in ("CurveIn", "CurveInOut", "FreeCurveIn", "FreeCurveOut", "FreeCurveInOut"):
        return name
    return CURVE_ALIASES.get(name.lower(), "CurveInOut")


# --------------------------------------------------------------------------- #
# easing
# --------------------------------------------------------------------------- #
def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)


def ease(curve_type: str, u: float) -> float:
    """Map a normalised interval position ``u`` in [0, 1] to an eased weight.

    ``curveType`` in the draft format has exactly five values (recovered from
    ``videoeditor.dll``): ``CurveIn, CurveInOut, FreeCurveIn, FreeCurveOut,
    FreeCurveInOut``.  The ``Free*`` variants are user-editable bezier curves;
    we approximate them with the equivalent fixed easing because the control
    points live in a sibling ``graph`` object we do not import.
    """
    u = _clamp01(u)
    ct = (curve_type or "CurveInOut").lower()
    if ct in ("curvein", "easein", "ease_in"):
        return u * u * u
    if ct in ("curveinout", "easeinout", "ease_in_out", "bezier"):
        return u * u * (3.0 - 2.0 * u)
    if ct in ("freecurvein", "freecurveinout"):
        return u * u
    if ct in ("freecurveout", "easeout", "ease_out"):
        return 1.0 - (1.0 - u) ** 2
    if ct in ("hold", "constant", "step"):
        return 0.0
    if ct in ("line", "linear", ""):
        return u
    return u


# --------------------------------------------------------------------------- #
# animated property
# --------------------------------------------------------------------------- #
class AnimatedProperty:
    """A time-varying float property backed by an optional keyframe list."""

    __slots__ = ("static", "keys", "_times")

    def __init__(self, static: float = 0.0, keys: Optional[Sequence[Keyframe]] = None):
        self.static = float(static)
        self.keys: List[Keyframe] = sorted(keys or [], key=lambda k: k.time_offset)
        self._times = [k.time_offset for k in self.keys]

    # -- construction -------------------------------------------------------
    @classmethod
    def from_keyframes(cls, static: float, keys: Sequence[Keyframe]) -> "AnimatedProperty":
        return cls(static, keys)

    @property
    def animated(self) -> bool:
        return len(self.keys) >= 1

    # -- evaluation ---------------------------------------------------------
    def value_at(self, offset: int) -> float:
        """``offset`` is microseconds relative to the segment start."""
        keys = self.keys
        if not keys:
            return self.static
        if offset <= keys[0].time_offset:
            return keys[0].value
        if offset >= keys[-1].time_offset:
            return keys[-1].value
        i = bisect_right(self._times, offset) - 1
        if i < 0:
            return keys[0].value
        if i >= len(keys) - 1:
            return keys[-1].value
        a, b = keys[i], keys[i + 1]
        span = b.time_offset - a.time_offset
        if span <= 0:
            return b.value
        u = (offset - a.time_offset) / span
        w = ease(a.curve_type, u)
        return a.value + (b.value - a.value) * w

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"AnimatedProperty(static={self.static}, keys={len(self.keys)})"


def build(static: float, keys: Optional[Sequence[Keyframe]]) -> AnimatedProperty:
    return AnimatedProperty(static, keys)


# --------------------------------------------------------------------------- #
# segment property bundle
# --------------------------------------------------------------------------- #
class SegmentAnimation:
    """Resolves every animatable property of one segment at a timeline instant.

    Base values come from the segment's ``clip`` and material; keyframes
    override them per property.
    """

    def __init__(self, segment, extras: Optional[dict] = None):
        self.segment = segment
        self.extras = extras or {}

    def _prop(self, name: str, static: float) -> float:
        keys = self.segment.keyframes.get(name)
        if not keys:
            return static
        return AnimatedProperty(static, keys).value_at(self._offset)

    def at(self, timeline_time: int) -> "SegmentAnimation":
        self._offset = timeline_time - self.segment.target_timerange.start
        return self

    # -- transform ----------------------------------------------------------
    @property
    def position_x(self) -> float:
        return self._prop("KFTypePositionX", self.segment.clip.tx)

    @property
    def position_y(self) -> float:
        return self._prop("KFTypePositionY", self.segment.clip.ty)

    @property
    def scale_x(self) -> float:
        return self._prop("KFTypeScaleX", self.segment.clip.scale_x)

    @property
    def scale_y(self) -> float:
        return self._prop("KFTypeScaleY", self.segment.clip.scale_y)

    @property
    def rotation(self) -> float:
        return self._prop("KFTypeRotation", self.segment.clip.rotation)

    @property
    def alpha(self) -> float:
        return self._prop("KFTypeAlpha", self.segment.clip.alpha)

    @property
    def volume(self) -> float:
        return self._prop("KFTypeVolume", self.segment.volume)


def keyframes_for(props: dict, name: str) -> Tuple[float, List[Keyframe]]:
    keys = props.get(name) or []
    return (keys[0].value if keys else 0.0), list(keys)
