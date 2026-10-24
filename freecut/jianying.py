"""Import a JianyingPro / CapCut draft (``draft_content.json``).

The shipped application encrypts ``draft_content.json`` for newer drafts
(the first bytes of the sample are base64 ciphertext).  Anything that is still
plaintext - sub-drafts, templates, drafts written by older builds - imports
here, which is also how the reconstructed schema was validated.

Mapping
-------
=========================================  =============================
draft                                  FreeCut
=========================================  =============================
``tracks[].type``                      ``Track.type``
``tracks[].segments[]``                ``Segment``
``materials.videos[]`` / ``images``    ``VideoMaterial`` / ``ImageMaterial``
``materials.audios[]``                 ``AudioMaterial``
``materials.texts[]``                  ``TextMaterial``
``materials.transitions[]``            ``TransitionMaterial``
``materials.speeds[]``                 ``SpeedMaterial``
``materials.filters[]``                ``FilterMaterial`` (``.cube`` LUT)
``segment.clip``                       ``Clip``
``segment.common_keyframes[]``         ``Segment.keyframes``
=========================================  =============================
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from .model import (
    AnimationMaterial,
    AudioMaterial,
    Canvas,
    Clip,
    CurveMaterial,
    FilterMaterial,
    HslMaterial,
    ImageMaterial,
    Keyframe,
    MaskMaterial,
    Material,
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

_TRACK_TYPE_MAP = {
    "video": "video",
    "audio": "audio",
    "text": "text",
    "sticker": "sticker",
    "effect": "effect",
    "filter": "filter",
}

_MATERIAL_CLASSES = {
    "videos": VideoMaterial,
    "images": ImageMaterial,
    "audios": AudioMaterial,
    "texts": TextMaterial,
    "transitions": TransitionMaterial,
    "speeds": SpeedMaterial,
    "filters": FilterMaterial,
    "material_animations": AnimationMaterial,
}


def is_encrypted(path: str) -> bool:
    with open(path, "rb") as fh:
        head = fh.read(64)
    stripped = head.lstrip()
    return not stripped.startswith(b"{")


def load_draft(path: str, decrypt: bool = True) -> Project:
    """Load a draft directory, a ``draft_content.json`` path, or a plain JSON file.

    A draft written by JianyingPro 6+ is encrypted.  When ``decrypt`` is true
    and the local JianyingPro install is available, the file is decrypted
    through the app's own ``videoeditor.dll`` (see
    :mod:`freecut.jianying_crypto`); otherwise a clear error is raised.
    """
    json_path = path
    if os.path.isdir(path):
        json_path = os.path.join(path, "draft_content.json")
    if not os.path.isfile(json_path):
        raise FileNotFoundError(json_path)

    with open(json_path, "rb") as fh:
        raw = fh.read()
    if raw.lstrip()[:1] == b"{":
        data = json.loads(raw.decode("utf-8", "replace"))
    elif decrypt:
        from .jianying_crypto import DraftCryptoError, decrypt_bytes

        try:
            clear = decrypt_bytes(raw)
        except DraftCryptoError as exc:
            raise ValueError(
                "该草稿已被剪映加密，且解密失败。需要本机安装剪映专业版（含 videoeditor.dll）。\n"
                f"原因: {exc}"
            ) from exc
        data = json.loads(clear.decode("utf-8", "replace"))
    else:
        raise ValueError(
            "该草稿已被剪映加密（draft_content.json 不是明文 JSON）。"
            "请启用 decrypt，或改用未加密的子草稿/模板。"
        )
    return from_draft_dict(data, base_dir=os.path.dirname(os.path.abspath(json_path)))


def read_project_timelines(folder: str) -> list:
    """读剪映工程级的 ``project.json``，返回 [(id, name, 目录)]。

    真实结构（实测，见 ``docs/measurements/MULTI-TIMELINE-ID-MAPPING.md``）：
    ``project.json`` 里有 ``main_timeline_id`` + ``timelines[]``，每条时间线是
    ``Timelines/<uuid>/draft_content.json``。已标记删除（``is_marked_delete``）的跳过。
    """
    path = os.path.join(folder, "project.json")
    if not os.path.isfile(path):
        return []
    with open(path, "rb") as fh:
        raw = fh.read()
    if raw.lstrip()[:1] == b"{":
        data = json.loads(raw.decode("utf-8", "replace"))
    else:
        from .jianying_crypto import DraftCryptoError, decrypt_bytes

        try:
            data = json.loads(decrypt_bytes(raw).decode("utf-8", "replace"))
        except DraftCryptoError as exc:
            raise ValueError(f"project.json 解密失败：{exc}") from exc

    out = []
    for item in data.get("timelines") or []:
        if item.get("is_marked_delete"):
            continue
        timeline_id = str(item.get("id") or "")
        if not timeline_id:
            continue
        name = str(item.get("name") or "时间线")
        candidate = os.path.join(folder, "Timelines", timeline_id)
        if not os.path.isdir(candidate):
            candidate = folder
        out.append((timeline_id, name, candidate))
    return out


def _main_timeline_id(folder: str) -> str:
    path = os.path.join(folder, "project.json")
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
        if raw.lstrip()[:1] == b"{":
            return str(json.loads(raw.decode("utf-8", "replace")).get("main_timeline_id") or "")
    except Exception:                                  # noqa: BLE001
        return ""
    return ""


def load_project_draft(folder: str, decrypt: bool = True) -> Project:
    """读一个**工程目录**（含 project.json）里的所有时间线，装进 ``sequences``。

    只有一条时间线时等价于 :func:`load_draft`；多条时每条时间线一个 sequence，
    并把 ``main_timeline_id`` 对应那条设为当前。
    """
    timelines = read_project_timelines(folder)
    if not timelines:
        return load_draft(folder, decrypt=decrypt)

    main_id = _main_timeline_id(folder)
    first = load_draft(timelines[0][2], decrypt=decrypt)
    if len(timelines) == 1:
        first.sequences[0].name = timelines[0][1] or first.sequences[0].name
        return first

    from .model import Sequence

    sequences = []
    for _timeline_id, name, timeline_dir in timelines:
        try:
            project = load_draft(timeline_dir, decrypt=decrypt)
        except Exception:                              # noqa: BLE001
            continue
        # 材质挂在工程上、不在序列里：合并时间线时要把材质一起并进来，
        # 否则后面几条时间线的片段会指向不存在的材质（渲染成黑屏）。
        for mid, material in project.materials.items():
            first.materials.setdefault(mid, material)
        sequence = project.sequences[0] if project.sequences else Sequence(name=name)
        sequence.name = name or sequence.name
        sequences.append(sequence)
    if not sequences:
        return first
    first.sequences = sequences
    first.active_sequence = 0
    for index, (timeline_id, _name, _dir) in enumerate(timelines):
        if timeline_id == main_id and index < len(sequences):
            first.active_sequence = index
            break
    return first


_SINGULAR = {
    "canvases": "canvas",
    "drafts": "draft",
    "speeds": "speed",
    "audios": "audio",
    "texts": "text",
    "videos": "video",
    "images": "image",
    "stickers": "sticker",
    "effects": "effect",
    "filters": "filter",
    "transitions": "transition",
    "masks": "mask",
    "beats": "beat",
    "flowers": "flower",
    "handwrites": "handwrite",
    "shapes": "shape",
    "tail_leaders": "tail_leader",
    "placeholder_infos": "placeholder_info",
    "material_colors": "material_color",
    "sound_channel_mappings": "sound_channel_mapping",
    "vocal_separations": "vocal_separation",
    "material_animations": "material_animation",
    "audio_fades": "audio_fade",
}


def _singular(key: str) -> str:
    return _SINGULAR.get(key, key.rstrip("s"))


#: material types that only exist in FreeCut's sidecar (and in our own files)
_BUILTINS_EXT = {
    "filter": FilterMaterial,
    "mix_mode": MixModeMaterial,
    "mask": MaskMaterial,
    "curve": CurveMaterial,
    "hsl": HslMaterial,
    "color_wheel": WheelMaterial,
    "material_animation": AnimationMaterial,
    "text": TextMaterial,
    "video": VideoMaterial,
    "image": ImageMaterial,
    "audio": AudioMaterial,
    "transition": TransitionMaterial,
    "speed": SpeedMaterial,
}


def unsupported_transitions(data) -> list:
    """返回草稿里用到、但我们没有专门实现的转场名（排序去重）。

    剪映草稿的转场在 ``materials.transitions[]``，靠 ``name`` 找实现；
    我们实现的名单就是 ``freecut.render.TRANSITION_KINDS`` 的键。
    没实现的名字导入后会退化成交叉溶解——这个函数就是用来把这件事**说出来**的。
    """
    try:
        names = [str(m.get("name") or "")
                 for m in (data.get("materials") or {}).get("transitions") or []]
    except AttributeError:
        return []
    if not names:
        return []
    from .render import resolve_transition

    return sorted({n for n in names if n and resolve_transition(n) is None})


#: 这些键是元数据/组合模板，本来就不该当成"未导入的功能"来报
_IGNORED_MATERIAL_KEYS = {
    "canvases", "placeholder_infos", "sound_channel_mappings", "vocal_separations",
    "drafts", "common_mask", "masks", "material_colors",
}


def unknown_material_keys(data) -> dict:
    """返回 ``{材料键: 条数}``：我们没有导入、且内容非空的材料类型。

    只统计**非空列表**——空数组在剪映草稿里很常见（例如从未用过的变声），
    报出来只会是噪音。
    """
    try:
        materials = data.get("materials") or {}
    except AttributeError:
        return {}
    if not isinstance(materials, dict):
        return {}

    known = set(_MATERIAL_CLASSES) | set(_SINGULAR) | {"hsl", "color_curves"}
    out = {}
    for key, value in materials.items():
        if key in known or key in _IGNORED_MATERIAL_KEYS:
            continue
        if isinstance(value, list) and value:
            out[str(key)] = len(value)
    return out


def unsupported_animations(data) -> list:
    """返回草稿里用到、但我们没有实现的动画名（排序去重）。

    剪映的动画在 ``materials.material_animations[]``，每条有 ``name`` 与
    ``type``（in / out / loop）。解析逻辑在 :func:`freecut.animation.resolve_kind`。
    解析不了的绝大多数是云端资源包（AmazingFeature 场景），离线版做不到。
    """
    try:
        items = (data.get("materials") or {}).get("material_animations") or []
    except AttributeError:
        return []
    if not isinstance(items, list):
        return []

    from .animation import resolve_kind

    missing = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        if not name:
            continue
        if resolve_kind(name, str(item.get("type") or "")) is None:
            missing.add(name)
    return sorted(missing)


def _read_draft_json(path: str):
    """读草稿目录/文件的明文 dict（必要时解密）。"""
    json_path = path
    if os.path.isdir(path):
        json_path = os.path.join(path, "draft_content.json")
    with open(json_path, "rb") as fh:
        raw = fh.read()
    if raw.lstrip()[:1] == b"{":
        return json.loads(raw.decode("utf-8", "replace"))
    from .jianying_crypto import decrypt_bytes

    return json.loads(decrypt_bytes(raw).decode("utf-8", "replace"))


def unsupported_transitions_for_path(path: str) -> list:
    """按草稿路径算"没有实现的转场名"；读不了就返回空（不影响导入）。"""
    try:
        data = _read_draft_json(path)
    except Exception:                                  # noqa: BLE001
        return []
    return unsupported_transitions(data)


def from_draft_dict(data: Dict[str, Any], base_dir: str = "") -> Project:
    canvas_cfg = data.get("canvas_config") or {}
    w = int(canvas_cfg.get("width", 0) or 0)
    h = int(canvas_cfg.get("height", 0) or 0)

    project = Project(
        canvas=Canvas(w or 1920, h or 1080, str(canvas_cfg.get("ratio") or "original"), canvas_cfg.get("background")),
        fps=float(data.get("fps", 30.0) or 30.0),
        name=str(data.get("name") or "导入的草稿"),
        version=int(data.get("version", 360000) or 360000),
        new_version=str(data.get("new_version") or ""),
    )

    mats = data.get("materials") or {}
    unknown: List[str] = []
    for key, items in mats.items():
        for item in items or []:
            payload = dict(item)
            mid = payload.pop("id", None) or new_id()
            payload.pop("type", None)
            # `effects` is a mixed array: filters, blend modes and video effects
            if key == "effects":
                kind = str(item.get("type") or "")
                if kind == "filter":
                    project_material = FilterMaterial(id=mid, **payload)
                elif kind == "mix_mode":
                    project_material = MixModeMaterial(id=mid, **payload)
                else:
                    project_material = Material(id=mid, type=kind or "effect", **payload)
                project.materials[project_material.id] = project_material
                continue
            if key in ("common_mask", "masks"):
                project_material = MaskMaterial(id=mid, **payload)
                project.materials.setdefault(project_material.id, project_material)
                continue
            # 我们导出的调色数值按剪映的数组名写（color_curves / hsl / primary_color_wheels），
            # 读回来时要还原成对应的材质类，否则往返一圈就丢掉数值
            _BY_ARRAY = {"color_curves": CurveMaterial, "hsl": HslMaterial,
                         "primary_color_wheels": WheelMaterial,
                         "log_color_wheels": WheelMaterial}
            if key in _BY_ARRAY:
                mat = _BY_ARRAY[key](id=mid, **payload)
                project.materials[mat.id] = mat
                continue
            klass = _MATERIAL_CLASSES.get(key)
            if klass is None:
                mat = Material(id=mid, type=_singular(key), **payload)
                unknown.append(key)
            else:
                mat = klass(id=mid, **payload)
            project.materials[mat.id] = mat

    # nothing carries the canvas size when ratio == "original"
    if not w or not h:
        for mat in project.materials.values():
            if isinstance(mat, VideoMaterial) and mat.width and mat.height:
                project.canvas.width, project.canvas.height = mat.width, mat.height
                break

    for td in data.get("tracks") or []:
        ttype = _TRACK_TYPE_MAP.get(str(td.get("type") or "video"), "video")
        track = Track(
            id=td.get("id") or new_id(),
            type=ttype,
            name=str(td.get("name") or ""),
            flag=int(td.get("flag", 0) or 0),
            attribute=int(td.get("attribute", 0) or 0),
        )
        for sd in td.get("segments") or []:
            seg = Segment.from_dict(sd)
            _attach_speed_curve(project, seg)
            mat = project.materials.get(seg.material_id)
            if isinstance(mat, TextMaterial):
                _normalise_text(mat)
            if mat is not None and isinstance(mat, VideoMaterial):
                seg.visible = bool(sd.get("visible", True))
            track.segments.append(seg)
        project.tracks.append(track)

    _promote_filters(project)
    _expand_combinations(project, depth=0)
    _relink_missing_media(project, base_dir)
    _restore_freecut_extras(project, base_dir)
    return project


def _attach_speed_curve(project: Project, seg: Segment) -> None:
    """Rebuild ``seg.speed_curve`` from a ``mode: curve`` speed material."""
    from .speed import normalize

    if seg.speed_curve:
        return
    for ref in project.refs_of(seg):
        if ref.type != "speed":
            continue
        curve = ref.get("curve_speed")
        if isinstance(curve, dict) and curve.get("speed_points"):
            pts = normalize(curve["speed_points"])
            if len(pts) >= 2:
                seg.speed_curve = pts
        return


def _restore_freecut_extras(project: Project, base_dir: str) -> None:
    """Re-attach materials our exporter had to park in ``freecut_extras.json``."""
    if not base_dir:
        return
    sidecar = os.path.join(base_dir, "freecut_extras.json")
    if not os.path.isfile(sidecar):
        return
    try:
        with open(sidecar, "r", encoding="utf-8") as fh:
            extras = json.load(fh)
    except Exception:
        return
    for mid, payload in (extras.get("materials") or {}).items():
        if mid in project.materials:
            continue
        mtype = str(payload.get("type") or "material")
        klass = _BUILTINS_EXT.get(mtype)
        body = dict(payload)
        body.pop("id", None)
        body.pop("type", None)
        project.materials[mid] = klass(id=mid, **body) if klass else Material(id=mid, type=mtype, **body)
    refs = extras.get("segment_refs") or {}
    for track in project.tracks:
        for seg in track.segments:
            missing = [r for r in refs.get(seg.id, []) if r in project.materials]
            for r in missing:
                if r not in seg.extra_material_refs:
                    seg.extra_material_refs.append(r)


# --------------------------------------------------------------------------- #
# compound clips ("复合片段")
# --------------------------------------------------------------------------- #
def _comb_subproject(mat) -> Optional[Project]:
    """Build the nested timeline carried by a compound-clip material.

    JianyingPro stores it either inline under ``draft`` (an embedded
    ``draft_content`` object) or as a path in ``draft_file_path``.
    """
    inner = mat.get("draft")
    if isinstance(inner, dict) and inner.get("tracks"):
        try:
            return from_draft_dict(inner)
        except Exception:
            return None
    if isinstance(inner, str) and inner.strip().startswith("{"):
        try:
            import json as _json

            return from_draft_dict(_json.loads(inner))
        except Exception:
            return None
    if mat.get("tracks") and mat.get("materials"):
        try:
            return from_draft_dict(
                {
                    "canvas_config": mat.get("canvas_config") or {},
                    "fps": mat.get("fps") or 30.0,
                    "materials": mat.get("materials") or {},
                    "tracks": mat.get("tracks") or [],
                    "name": mat.get("name") or "复合片段",
                }
            )
        except Exception:
            return None
    path = mat.get("draft_file_path") or ""
    if path and os.path.isfile(path):
        try:
            return load_draft(path)
        except Exception:
            return None
    return None


def _expand_combinations(project: Project, depth: int = 0, max_depth: int = 4) -> None:
    """Inline compound clips.

    A compound clip is a segment whose material points at a *nested draft*
    (``draft`` / ``draft_file_path``).  The app plays that draft as a
    sub-timeline, so we splice its tracks into the parent, offset by the
    segment's start.
    """
    if depth >= max_depth:
        return
    for track in list(project.tracks):
        for seg in list(track.segments):
            mat = project.material(seg.material_id)
            if mat is None:
                continue
            sub = _comb_subproject(mat)
            if sub is None:
                for ref in project.refs_of(seg):
                    sub = _comb_subproject(ref)
                    if sub is not None:
                        break
            if sub is None or not sub.tracks:
                continue
            if sub.duration() <= 0:
                continue
            offset = seg.target_timerange.start
            scale = 1.0
            sub_dur = sub.duration()
            if sub_dur > 0 and seg.target_timerange.duration > 0:
                scale = seg.target_timerange.duration / float(sub_dur)
            # bring the sub-project's materials in
            for mid, m in sub.materials.items():
                project.materials.setdefault(mid, m)
            for sub_track in sub.tracks:
                host = track_of_type(project, sub_track.type)
                for sub_seg in sub_track.segments:
                    new_seg = Segment.from_dict(sub_seg.to_dict())
                    new_seg.id = new_id()
                    new_seg.material_id = sub_seg.material_id
                    new_seg.extra_material_refs = list(sub_seg.extra_material_refs)
                    new_seg.keyframes = {k: list(v) for k, v in sub_seg.keyframes.items()}
                    new_seg.target_timerange = Timerange(
                        offset + int(sub_seg.target_timerange.start * scale),
                        int(sub_seg.target_timerange.duration * scale),
                    )
                    new_seg.source_timerange = Timerange(
                        sub_seg.source_timerange.start,
                        int(sub_seg.source_timerange.duration * (1.0 if scale == 1.0 else scale)),
                    )
                    host.segments.append(new_seg)
            # drop the placeholder compound segment
            track.segments.remove(seg)
    # recursive pass for nested compounds
    if any(
        (project.material(s.material_id) or Material()).get("draft_file_path")
        for t in project.tracks
        for s in t.segments
    ):
        _expand_combinations(project, depth + 1, max_depth)


def track_of_type(project: Project, ttype: str) -> Track:
    for t in project.tracks:
        if t.type == ttype:
            return t
    return project.add_track(ttype)


# --------------------------------------------------------------------------- #
# normalisation
# --------------------------------------------------------------------------- #
def _normalise_text(mat: TextMaterial) -> None:
    """Map the app's text keys onto the ones FreeCut renders."""
    content = mat.get("content")
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict) and "text" in parsed:
                mat["content"] = str(parsed.get("text") or "")
                if parsed.get("styles"):
                    style = parsed["styles"][0]
                    if "size" in style:
                        mat["font_size"] = float(style["size"])
                    fill = (style.get("fill") or {}).get("content", {})
                    if isinstance(fill, dict) and fill.get("solid"):
                        rgb = fill["solid"].get("color")
                        if isinstance(rgb, (list, tuple)) and len(rgb) >= 3:
                            mat["text_color"] = "#%02X%02X%02X" % tuple(int(c) & 255 for c in rgb[:3])
        except Exception:
            pass
    if mat.get("font_size") in (None, 0, 0.0):
        mat["font_size"] = 15.0


def _promote_filters(project: Project) -> None:
    """Re-attach filter/adjust materials referenced by segments (already refs)."""
    return


def _relink_missing_media(project: Project, base_dir: str) -> None:
    """Try to fix absolute paths that no longer exist."""
    if not base_dir:
        return
    for mat in project.materials.values():
        p = mat.get("path")
        if not isinstance(p, str) or not p:
            continue
        if os.path.isfile(p):
            continue
        cand = os.path.join(base_dir, os.path.basename(p))
        if os.path.isfile(cand):
            mat["path"] = cand
