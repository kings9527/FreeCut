"""Timeline data model.

Field names and semantics follow the JianyingPro ``draft_content.json`` schema
that the shipped 11.3.0 application writes (evidence:
``docs/measurements/drafts/subdraft-plaintext.draft_content.json``).

Time is stored in **microseconds** exactly like JianyingPro (``target_timerange
= {start, duration}``), which keeps round-tripping lossless and matches the
``98833333`` style values observed in real drafts.
"""

from __future__ import annotations

import copy
import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional


# --------------------------------------------------------------------------- #
# identifiers
# --------------------------------------------------------------------------- #
def new_id() -> str:
    """JianyingPro uses upper-case UUID4 strings for every entity id."""
    return str(uuid.uuid4()).upper()


# --------------------------------------------------------------------------- #
# timerange
# --------------------------------------------------------------------------- #
@dataclass
class Timerange:
    """Inclusive start + duration, both in microseconds."""

    start: int = 0
    duration: int = 0

    @property
    def end(self) -> int:
        return self.start + self.duration

    def contains(self, t: int) -> bool:
        return self.start <= t < self.end

    def overlap(self, other: "Timerange") -> int:
        return max(0, min(self.end, other.end) - max(self.start, other.start))

    def to_dict(self) -> Dict[str, int]:
        return {"duration": int(self.duration), "start": int(self.start)}

    @staticmethod
    def from_dict(d: Optional[Dict[str, Any]]) -> "Timerange":
        d = d or {}
        return Timerange(int(d.get("start", 0) or 0), int(d.get("duration", 0) or 0))

    def copy(self) -> "Timerange":
        return Timerange(self.start, self.duration)


# --------------------------------------------------------------------------- #
# keyframes
# --------------------------------------------------------------------------- #
#: JianyingPro property_type values observed in ``common_keyframes``.
KEYFRAME_PROPERTIES = (
    "KFTypePositionX",
    "KFTypePositionY",
    "KFTypeScaleX",
    "KFTypeScaleY",
    "KFTypeRotation",
    "KFTypeAlpha",
    "KFTypeVolume",
    "KFTypeSaturation",
    "KFTypeBrightness",
    "KFTypeContrast",
    "KFTypeShadow",
    "KFTypeHighlight",
    "KFTypeTemperature",
    "KFTypeTint",
    "KFTypeFilter",
    "KFTypeFigure1",
)

#: ``curveType`` values recovered from videoeditor.dll (exact .rdata order).
CURVE_TYPES = ("CurveIn", "CurveInOut", "FreeCurveIn", "FreeCurveOut", "FreeCurveInOut")


@dataclass
class Keyframe:
    time_offset: int  # microseconds, relative to the segment start
    value: float
    curve_type: str = "CurveInOut"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "time_offset": int(self.time_offset),
            "values": [{"x": float(self.value), "y": 0.0}],
            "curveType": self.curve_type,
            "graphID": "",
            "string_value": "",
            "left_control": {"x": 0.0, "y": 0.0},
            "right_control": {"x": 0.0, "y": 0.0},
            "id": new_id(),
        }

    @staticmethod
    def _coerce(values: Any) -> float:
        """``values`` is a list of ``CommonPoint{x,y}`` (or plain numbers)."""
        if not values:
            return 0.0
        first = values[0]
        if isinstance(first, dict):
            for key in ("x", "y", "value"):
                if key in first:
                    try:
                        return float(first[key])
                    except (TypeError, ValueError):
                        continue
            return 0.0
        try:
            return float(first)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Keyframe":
        return Keyframe(
            time_offset=int(d.get("time_offset", 0) or 0),
            value=Keyframe._coerce(d.get("values")),
            curve_type=str(d.get("curveType") or "CurveInOut"),
        )


# --------------------------------------------------------------------------- #
# clip transform (matches Segment.clip in the draft schema)
# --------------------------------------------------------------------------- #
@dataclass
class Clip:
    """Placement of a segment inside the canvas.

    ``transform.x`` / ``transform.y`` are normalised: ``0`` is the canvas
    centre and ``1`` is one full canvas width/height offset to the right/up
    (JianyingPro stores the same normalised values, e.g. ``y = 0.654`` for a
    caption near the bottom of a 16:9 canvas).
    """

    alpha: float = 1.0
    rotation: float = 0.0
    scale_x: float = 1.0
    scale_y: float = 1.0
    tx: float = 0.0
    ty: float = 0.0
    flip_h: bool = False
    flip_v: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "alpha": float(self.alpha),
            "flip": {"horizontal": bool(self.flip_h), "vertical": bool(self.flip_v)},
            "rotation": float(self.rotation),
            "scale": {"x": float(self.scale_x), "y": float(self.scale_y)},
            "transform": {"x": float(self.tx), "y": float(self.ty)},
        }

    @staticmethod
    def from_dict(d: Optional[Dict[str, Any]]) -> "Clip":
        d = d or {}
        flip = d.get("flip") or {}
        scale = d.get("scale") or {}
        tr = d.get("transform") or {}
        return Clip(
            alpha=float(d.get("alpha", 1.0) or 1.0),
            rotation=float(d.get("rotation", 0.0) or 0.0),
            scale_x=float(scale.get("x", 1.0) or 1.0),
            scale_y=float(scale.get("y", 1.0) or 1.0),
            tx=float(tr.get("x", 0.0) or 0.0),
            ty=float(tr.get("y", 0.0) or 0.0),
            flip_h=bool(flip.get("horizontal")),
            flip_v=bool(flip.get("vertical")),
        )

    def copy(self) -> "Clip":
        return copy.copy(self)


# --------------------------------------------------------------------------- #
# canvas
# --------------------------------------------------------------------------- #
@dataclass
class Canvas:
    width: int = 1920
    height: int = 1080
    ratio: str = "16:9"
    background: Optional[str] = None  # "#RRGGBB" or None for black

    def to_dict(self) -> Dict[str, Any]:
        return {
            "background": self.background,
            "height": int(self.height),
            "ratio": self.ratio,
            "width": int(self.width),
        }

    @staticmethod
    def from_dict(d: Optional[Dict[str, Any]]) -> "Canvas":
        d = d or {}
        w = int(d.get("width", 0) or 0)
        h = int(d.get("height", 0) or 0)
        if w <= 0 or h <= 0:
            w, h = 1920, 1080
        return Canvas(w, h, str(d.get("ratio") or "16:9"), d.get("background"))


# --------------------------------------------------------------------------- #
# material base
# --------------------------------------------------------------------------- #
class Material:
    """Loose, schema-preserving material record.

    Keeping materials as dictionaries (plus typed accessors) mirrors the real
    draft format, where each material kind has a few dozen optional keys.
    """

    type = "material"

    def __init__(self, **kw: Any) -> None:
        self.id: str = kw.pop("id", None) or new_id()
        self.type: str = kw.pop("type", self.type)
        self.extra: Dict[str, Any] = kw

    # -- generic dict access -------------------------------------------------
    def __getitem__(self, k: str) -> Any:
        return self.extra[k]

    def get(self, k: str, default: Any = None) -> Any:
        return self.extra.get(k, default)

    def __setitem__(self, k: str, v: Any) -> None:
        self.extra[k] = v

    def to_dict(self) -> Dict[str, Any]:
        d = {"id": self.id, "type": self.type}
        d.update(self.extra)
        return d


class VideoMaterial(Material):
    type = "video"

    @property
    def path(self) -> str:
        return self.get("path", "")

    @property
    def duration(self) -> int:
        return int(self.get("duration", 0) or 0)

    @property
    def width(self) -> int:
        return int(self.get("width", 0) or 0)

    @property
    def height(self) -> int:
        return int(self.get("height", 0) or 0)

    @property
    def has_audio(self) -> bool:
        return bool(self.get("has_audio", True))


class ImageMaterial(Material):
    type = "image"

    @property
    def path(self) -> str:
        return self.get("path", "")


class AudioMaterial(Material):
    type = "audio"

    @property
    def path(self) -> str:
        return self.get("path", "")

    @property
    def duration(self) -> int:
        return int(self.get("duration", 0) or 0)


class TextMaterial(Material):
    """Text material.

    Field names are the ones ``videoeditor.dll``'s ``MaterialText`` class
    exposes (recovered statically): note there is **no** ``bold`` / ``italic``
    field - the app stores ``bold_width`` and ``italic_degree`` - and the
    outline is ``border_color`` / ``border_width`` / ``border_alpha``.
    """

    type = "text"

    DEFAULTS = {
        "content": "",
        "font_name": "Microsoft YaHei",
        "font_path": "",
        "font_size": 15.0,
        "text_color": "#FFFFFF",
        "text_alpha": 1.0,
        "global_alpha": 1.0,
        "alignment": 1,           # 0 left, 1 center, 2 right
        "vertical": False,
        "line_spacing": 0.02,
        "letter_spacing": 0.0,
        "line_feed": 0,
        "bold_width": 0.0,
        "italic_degree": 0.0,
        "underline": False,
        "underline_width": 0.0,
        "border_color": "#000000",
        "border_width": 0.0,      # relative to font size
        "border_alpha": 1.0,
        "shadow_color": "#000000",
        "shadow_alpha": 0.0,
        "shadow_distance": 0.0,
        "shadow_angle": 45.0,
        "shadow_smoothing": 0.0,
        "background_color": "",
        "background_alpha": 0.0,
        "background_round_radius": 0.0,
        "background_width": 0.0,
        "background_height": 0.0,
        "background_horizontal_offset": 0.0,
        "background_vertical_offset": 0.0,
        # 逐字 (karaoke) subtitles: [{"start_time", "end_time", "text"}]
        "words": [],
        "highlight_color": "",       # 高亮色默认留空：是否高亮由 highlight_mode 决定
        "highlight_mode": 0,
    }

    def __init__(self, **kw: Any) -> None:
        kw.setdefault("type", "text")
        super().__init__(**kw)
        for k, v in self.DEFAULTS.items():
            self.extra.setdefault(k, v)


class FilterMaterial(Material):
    """A filter: either a local ``.cube`` LUT or a JianyingPro cloud filter.

    The app stores filters in ``materials.effects`` with ``type: "filter"`` and
    a single ``value`` (0..1 intensity); the look itself is a downloaded
    resource identified by ``resource_id``.
    """

    type = "filter"

    @property
    def lut_path(self) -> str:
        return self.get("path", "")

    @property
    def intensity(self) -> float:
        v = self.get("value", self.get("intensity_value", 1.0))
        try:
            return float(v)
        except (TypeError, ValueError):
            return 1.0

    @property
    def resource_id(self) -> str:
        return str(self.get("resource_id", "") or "")


class MixModeMaterial(Material):
    type = "mix_mode"

    @property
    def name_id(self) -> str:
        return str(self.get("name_id") or self.get("resource_id") or "normal")


class CurveMaterial(Material):
    """RGB / per-channel tone curves.

    ``curves`` maps a channel to control points in 0..1, e.g.
    ``{"rgb": [[0, 0], [0.5, 0.55], [1, 1]], "r": [[0, 0], [1, 1]]}``.
    The app keeps this in ``materials.color_curves``; we do not export it yet
    because the shipped drafts we could observe never contained one.
    """

    type = "curve"

    @property
    def curves(self) -> Dict[str, Any]:
        return dict(self.get("curves") or {})


class HslMaterial(Material):
    """Per-hue-band hue/saturation/luminance offsets (``materials.hsl``)."""

    type = "hsl"

    @property
    def bands(self) -> Dict[str, Any]:
        return dict(self.get("bands") or {})

    @property
    def fallback(self) -> Dict[str, Any]:
        return dict(self.get("fallback") or {})


class WheelMaterial(Material):
    """Primary colour wheels: ``wheels = {"lift": [r,g,b], "gamma": [...], "gain": [...]}``."""

    type = "color_wheel"

    @property
    def wheels(self) -> Dict[str, Any]:
        return dict(self.get("wheels") or {})


class EffectMaterial(Material):
    """A procedural video effect (特效).  ``kind`` names a function in
    :mod:`freecut.effects`; ``params`` holds its sliders."""

    type = "effect"

    @property
    def kind(self) -> str:
        return str(self.get("kind") or "")

    @property
    def params(self) -> Dict[str, Any]:
        return dict(self.get("params") or {})


class AdjustmentMaterial(Material):
    """An adjustment layer: applies its colour pipeline to everything below it.

    Premiere Pro treats this as a normal track item with ``isAdjustmentLayer``;
    JianyingPro has no equivalent, so a FreeCut project containing one exports to
    a 剪映 draft with those segments dropped (the validator says so).
    """

    type = "adjustment"


class AudioEffectMaterial(Material):
    """Audio treatment attached to a segment: 降噪 / 变声 / 三段均衡."""

    type = "audio_effect"

    DEFAULTS = {
        "denoise": 0.0,
        "pitch": 1.0,
        "eq": (0.0, 0.0, 0.0),
    }


class AnimationMaterial(Material):
    """A bundle of in/out/loop animations, ``materials.material_animations[]``.

    Each entry is ``{"name": "渐显", "type": "in", "start": 0, "duration": 500000,
    "resource_id": "…", "effect_id": "…"}``.
    """

    type = "material_animation"

    @property
    def animations(self) -> List[Dict[str, Any]]:
        return list(self.get("animations") or [])


class MaskMaterial(Material):
    """A mask, stored in ``materials.common_mask`` (11.x) and ``masks`` (<7).

    ``config`` follows the app's own key names:
    ``centerX``/``centerY``/``width``/``height`` are expressed in half-material
    units (1.0 = half the clip), ``feather`` and ``roundCorner`` are 0..1,
    ``rotation`` is degrees, ``invert`` flips the visible side.
    """

    type = "mask"

    DEFAULTS = {
        "name": "圆形",
        "resource_type": "circle",
        "resource_id": "6791700663249146381",
        "config": {
            "aspectRatio": 1.0,
            "centerX": 0.0,
            "centerY": 0.0,
            "feather": 0.0,
            "height": 1.0,
            "invert": False,
            "rotation": 0.0,
            "roundCorner": 0.0,
            "width": 1.0,
        },
    }

    def __init__(self, **kw: Any) -> None:
        kw.setdefault("type", "mask")
        super().__init__(**kw)
        for k, v in self.DEFAULTS.items():
            if k == "config":
                cfg = dict(self.DEFAULTS["config"])
                cfg.update(self.extra.get("config") or {})
                self.extra["config"] = cfg
            else:
                self.extra.setdefault(k, v)

    @property
    def config(self) -> Dict[str, Any]:
        return dict(self.extra.get("config") or {})

    def set(self, **values: Any) -> "MaskMaterial":
        cfg = dict(self.extra.get("config") or {})
        cfg.update({k: v for k, v in values.items()})
        self.extra["config"] = cfg
        return self


class TransitionMaterial(Material):
    type = "transition"


class SpeedMaterial(Material):
    type = "speed"

    @property
    def speed(self) -> float:
        return float(self.get("speed", 1.0) or 1.0)


# --------------------------------------------------------------------------- #
# segment
# --------------------------------------------------------------------------- #
@dataclass
class Segment:
    """One clip on a track.

    ``target_timerange`` is where it sits on the timeline,
    ``source_timerange`` is the slice taken from the source material; the
    ratio of the two is ``speed`` (JianyingPro keeps ``speed`` as a separate
    redundant field, and so do we).
    """

    id: str = field(default_factory=new_id)
    material_id: str = ""
    target_timerange: Timerange = field(default_factory=Timerange)
    source_timerange: Timerange = field(default_factory=Timerange)
    speed: float = 1.0
    volume: float = 1.0
    visible: bool = True
    render_index: int = 0
    clip: Clip = field(default_factory=Clip)
    extra_material_refs: List[str] = field(default_factory=list)
    keyframes: Dict[str, List[Keyframe]] = field(default_factory=dict)
    #: references to material ids, resolved by Project
    transition_id: str = ""
    source: str = "segmentsourcenormal"
    #: adjustment layers colour the composite below them (Premiere Pro concept)
    is_adjustment: bool = False
    #: 曲线变速的采样点 [(x, y), …]（见 freecut/speed.py）；空表示匀速
    speed_curve: List[Tuple[float, float]] = field(default_factory=list)
    #: 剪映草稿里的 enable_* 开关（缺省视为开）。导出的开关由实际效果反推，
    #: 渲染器读同一份值，避免"写了开关但没人读"（第 83 轮审计发现）
    enable: Dict[str, bool] = field(default_factory=dict)
    #: 编组 id：同一组的片段一起选中、一起移动（剪映的"编组"）
    group_id: str = ""

    # -- helpers ------------------------------------------------------------
    @property
    def start(self) -> int:
        return self.target_timerange.start

    @property
    def end(self) -> int:
        return self.target_timerange.end

    @property
    def duration(self) -> int:
        return self.target_timerange.duration

    def contains(self, t: int) -> bool:
        return self.visible and self.target_timerange.contains(t)

    def source_time(self, timeline_time: int) -> int:
        """Map a timeline instant to an instant inside the source material.

        With a speed curve the mapping integrates the curve, so the clip still
        consumes exactly ``source_timerange`` (the curve only redistributes it).
        """
        offset = timeline_time - self.target_timerange.start
        if not self.speed_curve:
            return self.source_timerange.start + int(offset * self.speed)
        from .speed import source_fraction

        duration = max(1, self.target_timerange.duration)
        x = min(1.0, max(0.0, offset / float(duration)))
        frac = source_fraction(self.speed_curve, x)
        return self.source_timerange.start + int(frac * self.source_timerange.duration)

    def to_dict(self) -> Dict[str, Any]:
        kfs = {}
        for prop, items in self.keyframes.items():
            if items:
                kfs[prop] = [k.to_dict() for k in items]
        return {
            "id": self.id,
            "material_id": self.material_id,
            "target_timerange": self.target_timerange.to_dict(),
            "source_timerange": self.source_timerange.to_dict(),
            "speed": float(self.speed),
            "volume": float(self.volume),
            "visible": bool(self.visible),
            "render_index": int(self.render_index),
            "clip": self.clip.to_dict(),
            "extra_material_refs": list(self.extra_material_refs),
            "common_keyframes": [
                {"property_type": p, "keyframe_list": kfs[p]} for p in kfs
            ],
            "source": self.source,
            "is_adjustment": bool(self.is_adjustment),
            "speed_curve": [[float(x), float(y)] for x, y in (self.speed_curve or [])],
            "group_id": self.group_id,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Segment":
        seg = Segment(
            id=d.get("id") or new_id(),
            material_id=d.get("material_id", ""),
            target_timerange=Timerange.from_dict(d.get("target_timerange")),
            source_timerange=Timerange.from_dict(d.get("source_timerange")),
            speed=float(d.get("speed", 1.0) or 1.0),
            volume=float(d.get("volume", 1.0) or 1.0),
            visible=bool(d.get("visible", True)),
            render_index=int(d.get("render_index", 0) or 0),
            clip=Clip.from_dict(d.get("clip")),
            extra_material_refs=list(d.get("extra_material_refs") or []),
            source=d.get("source") or "segmentsourcenormal",
            is_adjustment=bool(d.get("is_adjustment", False)),
            speed_curve=[(float(p[0]), float(p[1])) for p in (d.get("speed_curve") or [])
                         if isinstance(p, (list, tuple)) and len(p) == 2],
            group_id=str(d.get("group_id") or ""),
        )
        for kf in d.get("common_keyframes") or []:
            prop = kf.get("property_type", "")
            if not prop:
                continue
            seg.keyframes[prop] = [
                Keyframe.from_dict(x) for x in (kf.get("keyframe_list") or [])
            ]
        return seg


# --------------------------------------------------------------------------- #
# track
# --------------------------------------------------------------------------- #
@dataclass
class Track:
    id: str = field(default_factory=new_id)
    type: str = "video"  # video | audio | text | effect | filter
    segments: List[Segment] = field(default_factory=list)
    name: str = ""
    flag: int = 0
    attribute: int = 0
    is_default_name: bool = True

    def sorted_segments(self) -> List[Segment]:
        return sorted(self.segments, key=lambda s: s.target_timerange.start)

    def segment_at(self, t: int) -> Optional[Segment]:
        for s in self.segments:
            if s.contains(t):
                return s
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "attribute": self.attribute,
            "flag": self.flag,
            "id": self.id,
            "is_default_name": self.is_default_name,
            "name": self.name,
            "segments": [s.to_dict() for s in self.sorted_segments()],
            "type": self.type,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Track":
        return Track(
            id=d.get("id") or new_id(),
            type=d.get("type") or "video",
            segments=[Segment.from_dict(s) for s in (d.get("segments") or [])],
            name=d.get("name") or "",
            flag=int(d.get("flag", 0) or 0),
            attribute=int(d.get("attribute", 0) or 0),
            is_default_name=bool(d.get("is_default_name", True)),
        )


# --------------------------------------------------------------------------- #
# project
# --------------------------------------------------------------------------- #
VIDEO_TRACK_TYPES = ("video",)
AUDIO_TRACK_TYPES = ("audio",)
OVERLAY_TRACK_TYPES = ("text", "effect", "filter", "sticker")


@dataclass
class Sequence:
    """一条时间线（剪映里的"时间线 1/2/…"，一个工程可以有多条）。

    ``Project.tracks`` 始终指向**当前**这条序列的轨道，这样老代码不用改。
    """

    id: str = field(default_factory=new_id)
    name: str = "时间线 1"
    tracks: List["Track"] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "tracks": [t.to_dict() for t in self.tracks],
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Sequence":
        seq = Sequence(id=d.get("id") or new_id(), name=str(d.get("name") or "时间线"))
        seq.tracks = [Track.from_dict(td) for td in (d.get("tracks") or [])]
        return seq


@dataclass
class Project:
    canvas: Canvas = field(default_factory=Canvas)
    fps: float = 30.0
    materials: Dict[str, Material] = field(default_factory=dict)
    name: str = "未命名项目"
    id: str = field(default_factory=new_id)
    version: int = 360000
    new_version: str = "177.0.0"
    #: 封面帧在时间线上的时间（微秒）；0 = 用第一帧。剪映的"封面"就是这个
    cover_time: int = 0
    #: 主轨磁吸：草稿级开关（剪映 DRAFT_SET_MAIN_TRACK_ADSORB_ACTION）
    main_track_adsorb: bool = True
    #: 多条时间线；``tracks`` 是当前那条的轨道（属性，见下）
    sequences: List[Sequence] = field(default_factory=list)
    active_sequence: int = 0

    def __post_init__(self) -> None:
        if not self.sequences:
            self.sequences = [Sequence(name="时间线 1")]

    # -- 序列（多时间线）--------------------------------------------------- #
    def sequence(self) -> Sequence:
        """当前序列；索引越界时钳回去。"""
        if not self.sequences:
            self.sequences = [Sequence(name="时间线 1")]
        self.active_sequence = max(0, min(int(self.active_sequence),
                                         len(self.sequences) - 1))
        return self.sequences[self.active_sequence]

    @property
    def tracks(self) -> List[Track]:
        return self.sequence().tracks

    @tracks.setter
    def tracks(self, value: List[Track]) -> None:
        self.sequence().tracks = list(value)

    # -- materials ----------------------------------------------------------
    def add_material(self, mat: Material) -> Material:
        self.materials[mat.id] = mat
        return mat

    def material(self, mid: str) -> Optional[Material]:
        return self.materials.get(mid)

    @staticmethod
    def path_key(path: str) -> str:
        """路径归一化键：Windows 大小写不敏感，统一 normcase + abspath。

        （剪映把去重放在草稿保存层 asyncDedup 里做；我们在导入时就挡住，
        见 docs/measurements/PLAYBACK-AUDIO-RE.md §4。）
        """
        import os

        return os.path.normcase(os.path.abspath(str(path or "")))

    def material_by_path(self, path: str) -> Optional[Material]:
        """按路径找已导入的素材（视频/图片/音频都算）。"""
        key = self.path_key(path)
        if not key:
            return None
        for mat in self.materials.values():
            raw = getattr(mat, "path", None) or mat.get("path")
            if raw and self.path_key(str(raw)) == key:
                return mat
        return None

    def refs_of(self, seg: Segment) -> List[Material]:
        out = []
        for rid in seg.extra_material_refs:
            m = self.materials.get(rid)
            if m is not None:
                out.append(m)
        return out

    def first_ref(self, seg: Segment, mtype: str) -> Optional[Material]:
        for m in self.refs_of(seg):
            if m.type == mtype:
                return m
        return None

    # -- tracks -------------------------------------------------------------
    def add_track(self, ttype: str, name: str = "") -> Track:
        t = Track(type=ttype, name=name)
        self.tracks.append(t)
        return t

    def tracks_of(self, *types: str) -> List[Track]:
        return [t for t in self.tracks if t.type in types]

    def duration(self) -> int:
        end = 0
        for t in self.tracks:
            for s in t.segments:
                end = max(end, s.end)
        return end

    # -- lookup -------------------------------------------------------------
    def find_segment(self, sid: str):
        for t in self.tracks:
            for s in t.segments:
                if s.id == sid:
                    return t, s
        return None, None

    # -- serialisation ------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        mats: Dict[str, List[Dict[str, Any]]] = {}
        for m in self.materials.values():
            mats.setdefault(m.type, []).append(m.to_dict())
        return {
            "canvas_config": self.canvas.to_dict(),
            "create_time": 0,
            "duration": self.duration(),
            "fps": float(self.fps),
            "id": self.id,
            "materials": mats,
            "name": self.name,
            "new_version": self.new_version,
            "tracks": [t.to_dict() for t in self.tracks],
            "version": self.version,
            "cover_time": int(self.cover_time),
            "main_track_adsorb": bool(self.main_track_adsorb),
            # 多时间线：tracks 保留当前序列（剪映能认），sequences 存全部（我们自己读）
            "sequences": [s.to_dict() for s in self.sequences],
            "active_sequence": int(self.active_sequence),
            "platform": {"os": "windows", "app_version": "freecut"},
            "config": {},
        }

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, ensure_ascii=False, indent=1, default=_json_default)

    @classmethod
    def load(cls, path: str) -> "Project":
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Project":
        p = cls(
            canvas=Canvas.from_dict(data.get("canvas_config")),
            fps=float(data.get("fps", 30.0) or 30.0),
            name=data.get("name") or "未命名项目",
            id=data.get("id") or new_id(),
            version=int(data.get("version", 360000) or 360000),
            new_version=str(data.get("new_version") or "177.0.0"),
            cover_time=int(data.get("cover_time", 0) or 0),
            main_track_adsorb=bool(data.get("main_track_adsorb", True)),
        )
        matmap = data.get("materials") or {}
        for mtype, items in matmap.items():
            for item in items or []:
                m = _material_from_dict(mtype, item)
                p.materials[m.id] = m
        seqs = data.get("sequences") or []
        if seqs:
            p.sequences = [Sequence.from_dict(sd) for sd in seqs]
            p.active_sequence = int(data.get("active_sequence", 0) or 0)
        else:
            # 老工程 / 剪映草稿：只有一份 tracks，包成一条序列
            p.sequences = [Sequence(name="时间线 1")]
            for td in data.get("tracks") or []:
                p.sequences[0].tracks.append(Track.from_dict(td))
            p.active_sequence = 0
        return p

    def copy(self) -> "Project":
        return Project.from_dict(copy.deepcopy(self.to_dict()))


# --------------------------------------------------------------------------- #
# material factory
# --------------------------------------------------------------------------- #
_BUILTINS = {
    "video": VideoMaterial,
    "image": ImageMaterial,
    "audio": AudioMaterial,
    "text": TextMaterial,
    "filter": FilterMaterial,
    "transition": TransitionMaterial,
    "speed": SpeedMaterial,
    "mix_mode": MixModeMaterial,
    "mask": MaskMaterial,
    "curve": CurveMaterial,
    "hsl": HslMaterial,
    "color_wheel": WheelMaterial,
    "adjustment": AdjustmentMaterial,
    "audio_effect": AudioEffectMaterial,
    "effect": EffectMaterial,
    "material_animation": AnimationMaterial,
}


def _material_from_dict(mtype: str, item: Dict[str, Any]) -> Material:
    klass = _BUILTINS.get(mtype, Material)
    payload = dict(item)
    mid = payload.pop("id", None)
    payload.pop("type", None)
    mat = klass(id=mid, **payload) if klass is not Material else Material(id=mid, **payload)
    if klass is Material:
        mat.type = mtype
    return mat


def _json_default(o: Any) -> Any:
    if isinstance(o, Timerange):
        return o.to_dict()
    if hasattr(o, "to_dict"):
        return o.to_dict()
    raise TypeError(f"not serialisable: {type(o)!r}")
