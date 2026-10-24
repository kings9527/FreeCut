"""High level authoring API.

    from freecut import builder, export

    p = builder.new_project(1920, 1080, 30)
    v = builder.add_video(p, "a.mp4", start=0, duration=5_000_000)
    builder.set_transform(v, scale=1.2, x=0.1)
    builder.add_text(p, "你好", start=500_000, duration=2_000_000, y=-0.7)
    export.export(p, "out.mp4")
"""

from __future__ import annotations

import os
from typing import Optional, Sequence

from .media import probe
from .model import (
    AdjustmentMaterial,
    AudioEffectMaterial,
    AnimationMaterial,
    AudioMaterial,
    Canvas,
    Clip,
    CurveMaterial,
    EffectMaterial,
    FilterMaterial,
    HslMaterial,
    ImageMaterial,
    Keyframe,
    MaskMaterial,
    MixModeMaterial,
    Project,
    Segment,
    SpeedMaterial,
    TextMaterial,
    Timerange,
    Track,
    TransitionMaterial,
    VideoMaterial,
    WheelMaterial,
    new_id,
)


# --------------------------------------------------------------------------- #
# project
# --------------------------------------------------------------------------- #
def new_project(width: int = 1920, height: int = 1080, fps: float = 30.0, name: str = "未命名项目") -> Project:
    ratio = "original"
    if width and height:
        a, b = width / height, 16 / 9
        if abs(a - b) < 0.02:
            ratio = "16:9"
        elif abs(a - 9 / 16) < 0.02:
            ratio = "9:16"
        elif abs(a - 1.0) < 0.02:
            ratio = "1:1"
        elif abs(a - 4 / 3) < 0.02:
            ratio = "4:3"
    return Project(canvas=Canvas(width, height, ratio), fps=fps, name=name)


def track_of(project: Project, ttype: str, index: int = 0) -> Track:
    existing = [t for t in project.tracks if t.type == ttype]
    while len(existing) <= index:
        existing.append(project.add_track(ttype))
    return existing[index]


def free_start(track: Track, preferred: int = 0) -> int:
    """First instant at or after ``preferred`` where ``track`` is empty."""
    pos = preferred
    for seg in sorted(track.segments, key=lambda s: s.target_timerange.start):
        if seg.target_timerange.end <= pos:
            continue
        if seg.target_timerange.start > pos:
            break
        pos = seg.target_timerange.end
    return pos


# --------------------------------------------------------------------------- #
# media
# --------------------------------------------------------------------------- #
def add_video(
    project: Project,
    path: str,
    start: int = 0,
    source_in: int = 0,
    duration: Optional[int] = None,
    track_index: int = 0,
    speed: float = 1.0,
    volume: float = 1.0,
    name: str = "",
) -> Segment:
    info = probe(path)
    src_dur = info.duration_us or 1_000_000
    if duration is None:
        duration = int((src_dur - source_in) / max(speed, 1e-6))
    mat = project.material_by_path(path)      # 同一个文件只建一份材质
    if mat is None or getattr(mat, "type", "") != "video":
        mat = VideoMaterial(
            path=os.path.abspath(path),
            duration=src_dur,
            width=info.width,
            height=info.height,
            has_audio=info.has_audio,
            material_name=name or os.path.basename(path),
        )
        project.add_material(mat)
    speed_mat = None
    if abs(speed - 1.0) > 1e-6:
        from .model import SpeedMaterial

        speed_mat = SpeedMaterial(speed=speed)
        project.add_material(speed_mat)
    seg = Segment(
        material_id=mat.id,
        target_timerange=Timerange(start, duration),
        source_timerange=Timerange(source_in, int(duration * speed)),
        speed=speed,
        volume=volume,
        extra_material_refs=[speed_mat.id] if speed_mat else [],
    )
    _place(project, track_of(project, "video", track_index), seg)
    return seg


def add_image(
    project: Project,
    path: str,
    start: int = 0,
    duration: int = 5_000_000,
    track_index: int = 0,
) -> Segment:
    mat = project.material_by_path(path)          # 同一张图只建一份材质
    if mat is None or getattr(mat, "type", "") != "image":
        mat = ImageMaterial(path=os.path.abspath(path),
                            material_name=os.path.basename(path))
        project.add_material(mat)
    seg = Segment(
        material_id=mat.id,
        target_timerange=Timerange(start, duration),
        source_timerange=Timerange(0, duration),
    )
    _place(project, track_of(project, "video", track_index), seg)
    return seg


def add_audio(
    project: Project,
    path: str,
    start: int = 0,
    source_in: int = 0,
    duration: Optional[int] = None,
    volume: float = 1.0,
    speed: float = 1.0,
    fade_in_ms: int = 0,
    fade_out_ms: int = 0,
    track_index: int = 0,
) -> Segment:
    info = probe(path)
    if duration is None:
        duration = int((info.duration_us - source_in) / max(speed, 1e-6))
    mat = project.material_by_path(path)          # 同一个音频只建一份材质
    if mat is None or getattr(mat, "type", "") != "audio":
        mat = AudioMaterial(
            path=os.path.abspath(path),
            duration=info.duration_us,
            name=os.path.basename(path),
        )
        project.add_material(mat)
    refs = []
    if fade_in_ms or fade_out_ms:
        from .model import Material

        fade = Material(type="audio_fade", fade_in_duration=fade_in_ms * 1000, fade_out_duration=fade_out_ms * 1000)
        project.add_material(fade)
        refs.append(fade.id)
    seg = Segment(
        material_id=mat.id,
        target_timerange=Timerange(start, duration),
        source_timerange=Timerange(source_in, int(duration * speed)),
        volume=volume,
        speed=speed,
        extra_material_refs=refs,
    )
    _place(project, track_of(project, "audio", track_index), seg)
    return seg


def add_text(
    project: Project,
    content: str,
    start: int = 0,
    duration: int = 3_000_000,
    x: float = 0.0,
    y: float = -0.7,
    size: float = 15.0,
    color: str = "#FFFFFF",
    font: str = "msyh.ttc",
    stroke: float = 0.0,
    stroke_color: str = "#000000",
    shadow: float = 0.0,
    bold: bool = False,
    track_index: int = 0,
    **extra,
) -> Segment:
    """Add a text clip.

    Parameters are expressed in the app's own vocabulary: ``stroke`` becomes
    ``border_width``, ``bold`` becomes ``bold_width``, ``shadow`` becomes
    ``shadow_alpha`` (with a small ``shadow_distance``).
    """
    mat = TextMaterial(
        content=content,
        font_name=font,
        font_size=size,
        text_color=color,
        border_width=stroke,
        border_color=stroke_color,
        shadow_alpha=shadow,
        shadow_distance=0.055 if shadow else 0.0,
        bold_width=1.0 if bold else 0.0,
        **extra,
    )
    project.add_material(mat)
    seg = Segment(
        material_id=mat.id,
        target_timerange=Timerange(start, duration),
        source_timerange=Timerange(0, duration),
        clip=Clip(tx=x, ty=y),
    )
    _place(project, track_of(project, "text", track_index), seg)
    return seg


def _place(project: Project, track: Track, seg: Segment) -> None:
    track.segments.append(seg)
    for i, s in enumerate(sorted(track.segments, key=lambda s: s.target_timerange.start)):
        s.render_index = i


# --------------------------------------------------------------------------- #
# properties
# --------------------------------------------------------------------------- #
def set_transform(seg: Segment, scale=None, x=None, y=None, rotation=None, alpha=None) -> Segment:
    if scale is not None:
        seg.clip.scale_x = float(scale)
        seg.clip.scale_y = float(scale)
    if x is not None:
        seg.clip.tx = float(x)
    if y is not None:
        seg.clip.ty = float(y)
    if rotation is not None:
        seg.clip.rotation = float(rotation)
    if alpha is not None:
        seg.clip.alpha = float(alpha)
    return seg


def set_speed(seg: Segment, speed: float) -> Segment:
    old = seg.speed if seg.speed > 0 else 1.0
    seg.source_timerange.duration = int(seg.source_timerange.duration * speed / old)
    seg.target_timerange.duration = int(seg.target_timerange.duration * old / speed)
    seg.speed = float(speed)
    return seg


def set_speed_curve(project: Project, seg: Segment, points) -> SpeedMaterial:
    """Attach a curve-speed ramp (曲线变速).

    ``points`` is ``[{"x": 0..1, "y": speed}, …]``; ``y`` is a **multiplier**
    (2.0 = twice as fast at that point).  The clip keeps its current duration —
    the curve only redistributes where the source time goes, which is how the
    app behaves.
    """
    from . import speed as speed_mod

    pts = speed_mod.normalize(points)
    if len(pts) < 2:
        raise ValueError("曲线变速至少需要两个点")
    seg.speed_curve = list(pts)
    seg.speed = float(speed_mod.average_speed(pts))   # 供音频/导出使用的等效速度
    mat = None
    for ref in project.refs_of(seg):
        if ref.type == "speed":
            mat = ref
            break
    payload = {
        "mode": "curve",
        "curve_speed": {
            "speed_points": speed_mod.to_cube_points(pts),
            "source_platform": 0,
            "name": "",
        },
        "speed": float(speed_mod.average_speed(pts)),
    }
    if mat is None:
        mat = SpeedMaterial(**payload)
        project.add_material(mat)
        seg.extra_material_refs.append(mat.id)
    else:
        for k, v in payload.items():
            mat[k] = v
    return mat


def set_volume(seg: Segment, volume: float) -> Segment:
    seg.volume = float(volume)
    return seg


def add_filter(project: Project, seg: Segment, lut_path: str, intensity: float = 1.0) -> FilterMaterial:
    """Attach a local ``.cube`` LUT filter."""
    mat = FilterMaterial(path=os.path.abspath(lut_path), intensity_value=float(intensity),
                         value=float(intensity))
    project.add_material(mat)
    seg.extra_material_refs.append(mat.id)
    return mat


def add_cloud_filter(project: Project, seg: Segment, name: str, intensity: float = 1.0) -> FilterMaterial:
    """Attach one of the 1052 JianyingPro filters by name.

    FreeCut cannot download the app's filter package, so this preserves the
    filter for a draft we export; our own renderer ignores it unless a local
    ``.cube`` was supplied as well.
    """
    from . import catalog

    hit = catalog.find_filter(name=name)
    mat = FilterMaterial(
        name=name,
        value=float(intensity),
        intensity_value=float(intensity),
        resource_id=(hit or {}).get("resource_id", ""),
        effect_id=(hit or {}).get("effect_id", ""),
        platform="all",
        source_platform=1,
        type="filter",
    )
    project.add_material(mat)
    seg.extra_material_refs.append(mat.id)
    return mat


def set_mix_mode(project: Project, seg: Segment, name: str) -> MixModeMaterial:
    """Set the blend mode using the app's Chinese label (e.g. ``"滤色"``)."""
    from . import catalog
    from .color import BLEND_NAME_TO_ID

    if name in BLEND_NAME_TO_ID and name not in ("正常",):
        # an internal id was passed: turn it back into the app label
        for label, ident in BLEND_NAME_TO_ID.items():
            if ident == name:
                name = label
                break
    hit = None
    for item in catalog.mix_modes():
        if item["name"] == name:
            hit = item
            break
    mat = MixModeMaterial(
        name=name,
        resource_id=(hit or {}).get("resource_id", ""),
        effect_id=(hit or {}).get("effect_id", ""),
    )
    project.add_material(mat)
    seg.extra_material_refs.append(mat.id)
    return mat


def add_chroma(
    project: Project,
    seg: Segment,
    key: str = "#00ff00",
    similarity: float = 0.25,
    smoothness: float = 0.12,
    spill: float = 0.6,
) -> MaskMaterial:
    """Attach a chroma key (色度抠图) to ``seg``.

    ``key`` is the background colour to remove, ``similarity`` how aggressively,
    ``smoothness`` the softness of the edge, ``spill`` how much key colour to
    pull out of the remaining pixels.
    """
    existing = project.first_ref(seg, "mask")
    if existing is not None and str(existing.get("resource_type")) == "chroma":
        mat = existing
        cfg = dict(mat.get("config") or {})
        cfg.update({"key": key, "similarity": float(similarity),
                    "smoothness": float(smoothness), "spill": float(spill)})
        mat["config"] = cfg
        return mat
    mat = MaskMaterial(
        name="色度抠图",
        resource_type="chroma",
        resource_id="chroma_key",
        config={
            "key": key,
            "similarity": float(similarity),
            "smoothness": float(smoothness),
            "spill": float(spill),
            "invert": False,
        },
    )
    project.add_material(mat)
    seg.extra_material_refs.append(mat.id)
    return mat


def add_mask(
    project: Project,
    seg: Segment,
    name: str = "圆形",
    center_x: float = 0.0,
    center_y: float = 0.0,
    width: float = 1.0,
    height: float = 1.0,
    feather: float = 0.05,
    rotation: float = 0.0,
    invert: bool = False,
    round_corner: float = 0.0,
    aspect_ratio: float = 1.0,
    stroke_width: int = 0,
    stroke_color: str = "#FFFFFF",
    stroke_softness: float = 1.0,
    shadow_opacity: float = 0.0,
    shadow_offset: tuple = (4, 4),
    shadow_blur: int = 6,
    shadow_color: str = "#000000",
) -> MaskMaterial:
    """Attach a mask. ``name`` is one of 线性 / 镜面 / 圆形 / 矩形 / 爱心 / 星形."""
    from . import catalog

    hit = catalog.mask_by_name(name) or {}
    mat = MaskMaterial(
        name=name,
        resource_type=hit.get("kind", "circle"),
        resource_id=hit.get("resource_id", ""),
        config={
            "aspectRatio": float(aspect_ratio),
            "centerX": float(center_x),
            "centerY": float(center_y),
            "feather": float(feather),
            "height": float(height),
            "invert": bool(invert),
            "rotation": float(rotation),
            "roundCorner": float(round_corner),
            "width": float(width),
        },
        # 蒙版描边（剪映 draft 里的 video_mask_stroke），0 表示不描边
        stroke=(
            {"width": int(stroke_width), "color": str(stroke_color),
             "softness": float(stroke_softness)}
            if int(stroke_width) > 0 else {}
        ),
        # 蒙版投影（剪映 draft 里的 video_mask_shadow）
        shadow=(
            {"opacity": float(shadow_opacity),
             "dx": int(shadow_offset[0]), "dy": int(shadow_offset[1]),
             "blur": int(shadow_blur), "color": str(shadow_color)}
            if float(shadow_opacity) > 0 else {}
        ),
    )
    project.add_material(mat)
    seg.extra_material_refs.append(mat.id)
    return mat


def add_animation(
    project: Project,
    seg: Segment,
    name: str,
    anim_type: str = "",
    duration_us: Optional[int] = None,
    start_us: int = 0,
    amount: float = 1.0,
) -> AnimationMaterial:
    """Attach an in/out/loop animation.

    ``name`` is either one of 614 app names (见 :mod:`freecut.catalog`) or one of
    FreeCut's kinds (``fade_in``/``slide_in_right``/…).  ``anim_type`` is
    ``in`` / ``out`` / ``loop``; when omitted it is derived from the name.
    """
    from . import catalog
    from .animation import KIND_TO_NAME, classify

    kind, anim_type = classify(name, anim_type)
    if kind in KIND_TO_NAME:
        name = KIND_TO_NAME[kind][0]
    category = {"in": "video_in", "out": "video_out", "loop": "text_loop"}.get(anim_type, "video_in")
    if isinstance(seg.material_id, str):
        mat = project.material(seg.material_id)
        if mat is not None and getattr(mat, "type", "") == "text":
            category = {"in": "text_in", "out": "text_out", "loop": "text_loop"}.get(anim_type, "text_in")
    hit = catalog.find_animation(name, category) or {}
    if duration_us is None:
        duration_us = int(round(float(hit.get("duration_s", 0.5) or 0.5) * 1_000_000))

    entry = {
        "name": name,
        "type": anim_type,
        "start": int(start_us),
        "duration": int(duration_us),
        "resource_id": hit.get("resource_id", ""),
        "effect_id": hit.get("effect_id", ""),
        "amount": float(amount),
        "kind": kind,
    }

    existing = None
    for ref in project.refs_of(seg):
        if ref.type == "material_animation":
            existing = ref
            break
    if existing is None:
        amat = AnimationMaterial(animations=[entry], multi_language_current="none")
        project.add_material(amat)
        seg.extra_material_refs.append(amat.id)
        return amat
    items = [a for a in (existing.get("animations") or []) if a.get("type") != anim_type]
    items.append(entry)
    existing["animations"] = items
    return existing


def set_curves(project: Project, seg: Segment, **channels) -> CurveMaterial:
    """Attach tone curves.

    ``builder.set_curves(p, seg, rgb=[[0, 0], [0.5, 0.6], [1, 1]], b=[[0, 0.05], [1, 0.95]])``
    """
    mat = CurveMaterial(curves={k: v for k, v in channels.items() if v})
    project.add_material(mat)
    seg.extra_material_refs.append(mat.id)
    return mat


def set_wheels(project: Project, seg: Segment, lift=None, gamma=None, gain=None,
               log: bool = False) -> WheelMaterial:
    """Attach colour wheels.

    ``lift`` is additive (0 = neutral), ``gamma``/``gain`` are multipliers
    (1.0 = neutral).  Each may be a 3-sequence or a single number.

    ``log=True`` writes to the app's *log* wheel slot (``log_color_wheels``,
    zorder 5020) instead of the primary one (``primary_color_wheels``, 5019);
    the maths is the same, the slot differs.
    """
    def norm(v, neutral):
        if v is None:
            return None
        if isinstance(v, (int, float)):
            return [float(v)] * 3
        vals = [float(x) for x in v]
        return (vals * 3)[:3] if len(vals) < 3 else vals[:3]

    wheels = {}
    for key, val, neutral in (("lift", lift, 0.0), ("gamma", gamma, 1.0), ("gain", gain, 1.0)):
        n = norm(val, neutral)
        if n is not None:
            wheels[key] = n
    mat = WheelMaterial(wheels=wheels, log=bool(log))
    project.add_material(mat)
    seg.extra_material_refs.append(mat.id)
    return mat


def add_adjustment(
    project: Project,
    start: int,
    duration: int,
    track_index: Optional[int] = None,
    **params,
):
    """Add an adjustment layer (调节图层) spanning ``[start, start+duration)``.

    Same semantics as Premiere Pro: it recolours everything composited below it
    within its time range.  ``params`` are the usual slider values
    (``brightness``/``contrast``/``saturation``/…, see :data:`freecut.color.ADJUST_SPEC`).
    """
    mat = AdjustmentMaterial(adjust={k: v for k, v in params.items() if v is not None})
    project.add_material(mat)

    tracks = [t for t in project.tracks if t.type == "video"]
    if track_index is None:
        track = Track(type="video")
        # 调节层要放在**被调节的轨道上面**（列表里越靠前越在上），
        # 这样它最后合成、作用在下方所有画面上。剪映/PR 都是这个位置。
        first_video = next((i for i, t in enumerate(project.tracks)
                            if t.type == "video"), len(project.tracks))
        project.tracks.insert(first_video, track)
    else:
        track = track_of(project, "video", track_index)

    seg = Segment(
        material_id=mat.id,
        target_timerange=Timerange(int(start), int(duration)),
        source_timerange=Timerange(0, int(duration)),
        is_adjustment=True,
    )
    track.segments.append(seg)
    return seg


def add_effect(project: Project, seg: Segment, kind: str, **params) -> EffectMaterial:
    """Attach a procedural effect (see :data:`freecut.effects.EFFECT_SPEC`)."""
    from .effects import EFFECT_SPEC

    spec = EFFECT_SPEC.get(kind)
    if spec is None:
        raise ValueError(f"未知特效: {kind}（可用: {', '.join(EFFECT_SPEC)}）")
    merged = dict(spec["params"])
    merged.update({k: v for k, v in params.items() if v is not None})
    mat = EffectMaterial(kind=kind, params=merged)
    project.add_material(mat)
    seg.extra_material_refs.append(mat.id)
    return mat


def set_audio_fade(project: Project, seg: Segment,
                   fade_in_us: Optional[int] = None,
                   fade_out_us: Optional[int] = None) -> Material:
    """为已有片段设置淡入/淡出（毫秒级精度由调用方换算）。

    以前只有 ``add_audio`` 时能带淡入淡出，改不了——界面上的「声音」页需要能改。
    """
    from .model import Material

    existing = None
    for ref in project.refs_of(seg):
        if ref.type == "audio_fade":
            existing = ref
            break
    if existing is None:
        existing = Material(type="audio_fade", fade_in_duration=0, fade_out_duration=0)
        project.add_material(existing)
        seg.extra_material_refs.append(existing.id)
    if fade_in_us is not None:
        existing["fade_in_duration"] = max(0, int(fade_in_us))
    if fade_out_us is not None:
        existing["fade_out_duration"] = max(0, int(fade_out_us))
    return existing


def set_audio_effects(
    project: Project,
    seg: Segment,
    denoise: Optional[float] = None,
    pitch: Optional[float] = None,
    eq: Optional[Sequence[float]] = None,
) -> AudioEffectMaterial:
    """Attach 降噪 / 变声 / 三段均衡 to a segment.

    ``denoise`` 0..1, ``pitch`` a rate multiplier (0.5..2.0; 1.0 = untouched, >1
    higher voice, <1 lower), ``eq`` = (low, mid, high) in dB.
    """
    existing = None
    for ref in project.refs_of(seg):
        if ref.type == "audio_effect":
            existing = ref
            break
    if existing is not None:
        if denoise is not None:
            existing["denoise"] = float(denoise)
        if pitch is not None:
            existing["pitch"] = float(pitch)
        if eq is not None:
            vals = [float(x) for x in eq][:3]
            while len(vals) < 3:
                vals.append(0.0)
            existing["eq"] = vals
        return existing
    mat = AudioEffectMaterial(
        denoise=float(denoise or 0.0),
        pitch=float(pitch if pitch is not None else 1.0),
        eq=[float(x) for x in (eq or (0.0, 0.0, 0.0))][:3],
    )
    project.add_material(mat)
    seg.extra_material_refs.append(mat.id)
    return mat


def set_hsl(project: Project, seg: Segment, bands: Optional[dict] = None, **fallback) -> HslMaterial:
    """Attach HSL band offsets.

    ``builder.set_hsl(p, seg, bands={"orange": {"sat": 0.3, "lum": 0.05}}, sat=0.05)``
    """
    mat = HslMaterial(bands=bands or {}, fallback={k: v for k, v in fallback.items() if v})
    project.add_material(mat)
    seg.extra_material_refs.append(mat.id)
    return mat


def set_adjust(project: Project, seg: Segment, **params) -> Segment:
    """Attach slider values (see :data:`freecut.color.ADJUST_SPEC`) to a segment.

    The values live on the segment's own material under ``adjust``, which is the
    field the renderer reads; JianyingPro instead packs them into a dedicated
    ``filters``/``material_colors`` material, and the importer translates.
    """
    mat = project.material(seg.material_id)
    if mat is None:
        raise ValueError("片段没有关联素材")
    current = dict(mat.get("adjust") or {})
    for k, v in params.items():
        current[k] = float(v)
    mat["adjust"] = current
    return seg


#: our transition kinds -> the app's transition name (for round-tripping)
KIND_TO_NAME = {
    "cross_dissolve": "叠化",
    "fade_black": "闪黑",
    "fade_white": "闪白",
    "wipe_left": "向左擦除",
    "wipe_right": "向右擦除",
    "wipe_up": "向上擦除",
    "wipe_down": "向下擦除",
    "slide_left": "向左滑动",
    "slide_right": "向右滑动",
    "slide_up": "向上滑动",
    "slide_down": "向下滑动",
    "circle": "圆形",
}


def add_transition(
    project: Project,
    seg: Segment,
    kind: str = "cross_dissolve",
    duration_us: Optional[int] = None,
    name: Optional[str] = None,
) -> TransitionMaterial:
    """Attach a transition to ``seg`` (which plays it at its *start*).

    ``kind`` uses FreeCut's vocabulary ("cross_dissolve", "fade_black",
    "wipe_left", ...).  ``name`` may be any of the 453 JianyingPro transition
    names in :mod:`freecut.catalog`; when given, the catalog supplies the
    default duration and the overlap flag, and the draft round-trips with the
    original name.
    """
    info = None
    if name:
        from . import catalog

        info = catalog.find_transition(name=name)
    if info is None:
        # 只有没给名字时才用映射表；给了名字就尊重调用方（例如界面只选引擎时
        # 传的是「斜向擦除」这种中文标签，不该被改写成 wipe_diagonal）
        if not name:
            name = KIND_TO_NAME.get(kind, kind)
        try:
            from . import catalog

            info = catalog.find_transition(name=name)
        except Exception:
            info = None
    if duration_us is None:
        duration_us = int(round(float(info["duration_s"]) * 1_000_000)) if info else 500_000
    mat = TransitionMaterial(
        name=name or kind,
        # 把引擎也存下来：剪映名可能只是标签（如只选引擎时的「斜向擦除」），
        # 只靠名字反推会丢掉引擎
        kind=kind,
        duration=int(duration_us),
        is_overlap=bool(info.get("is_overlap")) if info else False,
        resource_id=str(info.get("resource_id", "")) if info else "",
        effect_id=str(info.get("effect_id", "")) if info else "",
        third_resource_id=str(info.get("resource_id", "")) if info else "",
        platform="all",
    )
    project.add_material(mat)
    seg.extra_material_refs.append(mat.id)
    return mat


def add_keyframe(seg: Segment, prop: str, offset_us: int, value: float, curve: str = "CurveInOut") -> Keyframe:
    from .keyframe import normalize_curve

    kf = Keyframe(int(offset_us), float(value), normalize_curve(curve))
    seg.keyframes.setdefault(prop, []).append(kf)
    seg.keyframes[prop] = sorted(seg.keyframes[prop], key=lambda k: k.time_offset)
    return kf


def trim(seg: Segment, start: Optional[int] = None, duration: Optional[int] = None) -> Segment:
    if start is not None:
        delta = start - seg.target_timerange.start
        seg.target_timerange.start = int(start)
        seg.source_timerange.start = max(0, int(seg.source_timerange.start + delta * seg.speed))
        if duration is None:
            seg.target_timerange.duration = max(0, seg.target_timerange.duration - delta)
            seg.source_timerange.duration = max(0, int(seg.target_timerange.duration * seg.speed))
    if duration is not None:
        seg.target_timerange.duration = int(duration)
        seg.source_timerange.duration = int(duration * seg.speed)
    return seg


def split(project: Project, seg: Segment, at_us: int) -> Segment:
    """Split ``seg`` at an absolute timeline instant; returns the right half."""
    if not seg.target_timerange.contains(at_us):
        raise ValueError("分割点不在片段内")
    left_dur = at_us - seg.target_timerange.start
    right_dur = seg.target_timerange.duration - left_dur
    right_src = seg.source_timerange.start + int(left_dur * seg.speed)
    right = Segment(
        material_id=seg.material_id,
        target_timerange=Timerange(at_us, right_dur),
        source_timerange=Timerange(right_src, int(right_dur * seg.speed)),
        speed=seg.speed,
        volume=seg.volume,
        clip=seg.clip.copy(),
        extra_material_refs=list(seg.extra_material_refs),
        visible=seg.visible,
    )
    seg.target_timerange.duration = left_dur
    seg.source_timerange.duration = int(left_dur * seg.speed)
    for track in project.tracks:
        if seg in track.segments:
            track.segments.append(right)
            break
    return right

def add_hsl_curves(project, segment, bands: dict, name: str = "HSL 曲线"):
    """Attach per-hue-band tone curves to ``segment`` (剪映 zorder 5018).

    ``bands`` maps a band name (``red`` … ``magenta``) to
    ``{"points": [[x, y], ...], "channel": "luma"|"r"|"g"|"b"}``.
    """
    from .model import Material

    mat = Material(id=new_id(), type="hsl_curves", name=name)
    mat.extra["bands"] = bands
    project.materials[mat.id] = mat
    target = project.segment(segment.id) if hasattr(project, "segment") else segment
    target.extra_material_refs.append(mat.id)
    target.enable["enable_hsl_curves"] = True
    return mat

def add_adjust_mask(project, seg, **mask_kwargs):
    """Add a mask that only limits *colour grading*, leaving the frame intact.

    This is Premiere-style "masked colour correction".  JianyingPro's own
    ``enable_adjust_mask`` field exists but its semantics are unverified, so we
    write it and warn on export.
    """
    mat = add_mask(project, seg, **mask_kwargs)
    seg.enable["enable_adjust_mask"] = True
    seg.enable["enable_video_mask"] = False      # 只限调色，不裁画面
    return mat

def set_color_migration(project, seg, reference, intensity: float = 1.0):
    """Colour-migrate a clip toward ``reference`` (剪映 zorder 5010).

    ``reference`` may be a path to an image or an RGB array.  We store the
    reference's per-channel mean/σ; the renderer bakes a LUT from them and
    applies it with ``intensity`` — matching what the extracted shader does
    (``colormigration.frag`` is trilinear LUT sampling + ``u_intensity``).
    """
    import numpy as np
    from . import color as color_mod
    from .model import Material

    if isinstance(reference, str):
        from PIL import Image

        arr = np.asarray(Image.open(reference).convert("RGB"), dtype=np.float32) / 255.0
    else:
        arr = np.asarray(reference, dtype=np.float32)
    stats = color_mod.image_stats(arr)
    mat = Material(type="color_migration", name="色彩迁移")
    mat.extra.update({
        "reference_mean": stats["mean"],
        "reference_std": stats["std"],
        "intensity": float(intensity),
    })
    project.add_material(mat)
    seg.extra_material_refs.append(mat.id)
    return mat
