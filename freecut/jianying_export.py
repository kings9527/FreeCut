"""Write a JianyingPro / CapCut draft folder from a FreeCut project.

This is the reverse direction of :mod:`freecut.jianying`: it turns FreeCut's
in-memory timeline into the two files the app actually needs

    <draft>/draft_content.json     the timeline
    <draft>/draft_meta_info.json   the media registry

which is exactly the pair ``pyJianYingDraft`` writes (see
``docs/REPORT.md``), and which the shipped 11.3 build writes as well - only
encrypted.  When the local JianyingPro install is present we encrypt both files
through ``videoeditor.dll`` so the draft looks native to 11.x.

Optionally the draft is registered in the app's project list
(``User Data/Projects/com.lveditor.draft/root_meta_info.json``); that file is
backed up first.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import time
import uuid
from typing import Any, Dict, List, Optional

from .model import (
    AnimationMaterial,
    AudioMaterial,
    FilterMaterial,
    ImageMaterial,
    MaskMaterial,
    Material,
    MixModeMaterial,
    Project,
    Segment,
    SpeedMaterial,
    TextMaterial,
    TransitionMaterial,
    VideoMaterial,
    new_id,
)

#: The minimal, known-good skeleton the 5.9 .. 11.x builds accept.
CONTENT_TEMPLATE: Dict[str, Any] = {
    "canvas_config": {"height": 1080, "ratio": "original", "width": 1920},
    "color_space": 0,
    "config": {
        "adjust_max_index": 1,
        "attachment_info": [],
        "combination_max_index": 1,
        "export_range": None,
        "extract_audio_last_index": 1,
        "lyrics_recognition_id": "",
        "lyrics_sync": True,
        "lyrics_taskinfo": [],
        "maintrack_adsorb": True,
        "material_save_mode": 0,
        "multi_language_current": "none",
        "multi_language_list": [],
        "multi_language_main": "none",
        "multi_language_mode": "none",
        "original_sound_last_index": 1,
        "record_audio_last_index": 1,
        "sticker_max_index": 1,
        "subtitle_keywords_config": None,
        "subtitle_recognition_id": "",
        "subtitle_sync": True,
        "subtitle_taskinfo": [],
        "system_font_list": [],
        "video_mute": False,
        "zoom_info_params": None,
    },
    "cover": None,
    "create_time": 0,
    "duration": 0,
    "extra_info": None,
    "fps": 30.0,
    "free_render_index_mode_on": False,
    "function_assistant_info": {"fps": {}},
    "group_container": None,
    "id": "",
    "keyframe_graph_list": [],
    "keyframes": {
        "adjusts": [], "audios": [], "effects": [], "filters": [],
        "handwrites": [], "stickers": [], "texts": [], "videos": [],
    },
    "last_modified_platform": {"app_id": 3704, "app_source": "lv", "app_version": "11.3.0", "os": "windows"},
    "materials": {},
    "mutable_config": None,
    "name": "",
    "new_version": "177.0.0",
    "path": "",
    "platform": {"app_id": 3704, "app_source": "lv", "app_version": "11.3.0", "os": "windows"},
    "relationships": [],
    "render_index_track_mode_on": True,
    "retouch_cover": None,
    "smart_ads_info": {},
    "source": "default",
    "static_cover_image_path": "",
    "time_marks": None,
    "tracks": [],
    "uneven_animation_template_info": {},
    "update_time": 0,
    "version": 360000,
}

#: every material array the app knows about; unknown ones stay empty
MATERIAL_ARRAYS = (
    "ai_text_effects", "ai_translates", "audio_balances", "audio_effects", "audio_fades",
    "audio_pannings", "audio_pitch_shifts", "audio_track_indexes", "audio_volumes", "audios",
    "beats", "canvases", "chromas", "color_curves", "common_mask", "drafts", "effects",
    "flowers", "green_screens", "handwrites", "hsl", "hsl_curves", "images",
    "log_color_wheels", "loudnesses", "manual_beautys", "manual_deformations",
    "material_animations", "material_colors", "masks", "multi_language_refs",
    "placeholder_infos", "placeholders", "plugin_effects", "primary_color_wheels",
    "realtime_denoises", "shapes", "smart_crops", "smart_relights",
    "sound_channel_mappings", "speeds", "stickers", "tail_leaders", "text_templates",
    "texts", "time_marks", "transitions", "video_effects", "video_radius", "video_shadows",
    "video_strokes", "video_trackings", "videos", "vocal_beautifys", "vocal_separations",
)

META_TEMPLATE: Dict[str, Any] = {
    "cloud_package_completed_time": "",
    "draft_cloud_capcut_purchase_info": "",
    "draft_cloud_last_action_download": False,
    "draft_cloud_purchase_info": "",
    "draft_cloud_template_id": "",
    "draft_cloud_tutorial_info": "",
    "draft_cloud_videocut_purchase_info": "",
    "draft_cover": "draft_cover.jpg",
    "draft_deeplink_url": "",
    "draft_enterprise_info": {
        "draft_enterprise_extra": "", "draft_enterprise_id": "",
        "draft_enterprise_name": "", "enterprise_material": [],
    },
    "draft_fold_path": "",
    "draft_id": "",
    "draft_is_ai_packaging_used": False,
    "draft_is_ai_shorts": False,
    "draft_is_ai_translate": False,
    "draft_is_article_video_draft": False,
    "draft_is_from_deeplink": "false",
    "draft_is_invisible": False,
    "draft_materials": [],
    "draft_materials_copied_info": [],
    "draft_name": "",
    "draft_new_version": "",
    "draft_removable_storage_device": "",
    "draft_root_path": "",
    "draft_segment_extra_info": [],
    "draft_type": "",
    "tm_draft_cloud_completed": "",
    "tm_draft_cloud_modified": 0,
    "tm_draft_removed": 0,
    "tm_duration": 0,
    # ---- 以下字段是第 18 轮实测补的：把草稿注册进剪映并让它打开后，
    #      剪映会重写 draft_meta_info.json；这些是它自己补上的键与取值。
    #      预先写全可以让草稿看起来"原生"，也避免剪映首次打开时改写文件。
    "cloud_draft_cover": False,
    "cloud_draft_sync": False,
    "draft_cloud_package_type": "",
    "draft_is_ae_produce": False,
    "draft_is_cloud_temp_draft": False,
    "draft_is_infinite_canvas_draft": False,
    "draft_is_pippit_draft": False,
    "draft_is_web_article_video": False,
    "draft_need_rename_folder": False,
    "draft_timeline_materials_size_": 0,
    "draft_web_article_video_enter_from": "",
    "pippit_avatar_url": "",
    "pippit_extra_info": "",
    "pippit_id": "",
    "pippit_user_name": "",
    "tm_draft_cloud_entry_id": -1,
    "tm_draft_cloud_parent_entry_id": -1,
    "tm_draft_cloud_space_id": -1,
    "tm_draft_cloud_user_id": -1,
    "tm_draft_create": 0,
    "tm_draft_modified": 0,
}

#: draft_meta_info.draft_materials[].type buckets: 0 video, 1 audio, 2 image, 3 text, 6/7/8 others
_MEDIA_TYPE_BUCKET = {"video": 0, "audio": 1, "image": 2, "text": 3}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _now_us() -> int:
    return int(time.time() * 1_000_000)


def _canvas_ratio(w: int, h: int) -> str:
    if not w or not h:
        return "original"
    a = w / h
    for label, val in (("16:9", 16 / 9), ("9:16", 9 / 16), ("1:1", 1.0), ("4:3", 4 / 3), ("3:4", 3 / 4)):
        if abs(a - val) < 0.02:
            return label
    return "original"


def _plain(material: Material) -> Dict[str, Any]:
    """Serialise a material, dropping FreeCut-only bookkeeping."""
    d = material.to_dict()
    if isinstance(material, TextMaterial):
        # the app stores an empty style blob; make sure we emit sane defaults
        d.setdefault("text_color", "#FFFFFF")
    if isinstance(material, (VideoMaterial, ImageMaterial)):
        path = str(d.get("path") or "")
        if path:
            d["path"] = path.replace("\\", "/")
        d.setdefault("type", "video" if isinstance(material, VideoMaterial) else "image")
    if isinstance(material, AudioMaterial):
        path = str(d.get("path") or "")
        if path:
            d["path"] = path.replace("\\", "/")
    if isinstance(material, TransitionMaterial):
        d.setdefault("platform", "all")
        name = str(d.get("name") or "")
        # projects authored with the internal kind name ("cross_dissolve") must
        # be written with the app's resource name, otherwise the app ignores it
        try:
            from . import catalog
            from .builder import KIND_TO_NAME

            if name in KIND_TO_NAME:
                name = KIND_TO_NAME[name]
                d["name"] = name
            info = catalog.find_transition(name=name) if name else None
            if info:
                d.setdefault("resource_id", info["resource_id"])
                d.setdefault("effect_id", info["effect_id"])
        except Exception:
            pass
        if d.get("resource_id") and not d.get("third_resource_id"):
            d["third_resource_id"] = d["resource_id"]
    return d


def _material_color_json(mat) -> Dict[str, Any]:
    """The app's per-clip "basic correction" material (``material_colors[]``)."""
    params = dict(mat.get("adjust") or {})
    return {
        "id": new_id(),
        "type": "material_color",
        "name": "调节",
        "adjust_params": [{"key": k, "value": float(v)} for k, v in params.items()],
        "params": {k: float(v) for k, v in params.items()},
    }

def _has(refs, type_name: str) -> bool:
    """Does this segment carry a reference material of ``type_name``?"""
    for ref in refs or []:
        if getattr(ref, "type", None) == type_name:
            return True
    return False


def _companions(seg: Segment, project: Project) -> List[Dict[str, Any]]:
    """The per-segment helper materials the app writes for video/audio clips."""
    out: List[Dict[str, Any]] = []
    mat = project.material(seg.material_id)
    is_video = isinstance(mat, (VideoMaterial, ImageMaterial))
    if is_video:
        # 曲线变速：如果片段带了 SpeedMaterial（含 curve_speed），就用它，
        # 不要再造一个恒定速度的替身——那样曲线会被丢掉
        existing = None
        for ref in project.refs_of(seg):
            if ref.type == "speed":
                existing = ref
                break
        # 有 SpeedMaterial 时它已经在 extra_material_refs 里，会被 refs 循环带上；
        # 这里只补一个恒定速度的替身（否则会出现两份相同的 speed 素材）
        if existing is None:
            out.append({"id": new_id(), "type": "speed", "speed": float(seg.speed), "mode": 0})
        out.append({"id": new_id(), "type": "placeholder_info", "meta_type": "none"})
        out.append({"id": new_id(), "type": "canvas_color"})
        out.append({"id": new_id(), "type": ""})
        out.append({"id": new_id()})
        out.append({"id": new_id(), "type": "vocal_separation"})
    elif isinstance(mat, AudioMaterial):
        out.append({"id": new_id(), "type": "vocal_separation"})
        out.append({"id": new_id(), "type": ""})
    return out


def _keyframes(seg) -> list:
    """把片段的 keyframes 写成剪映的 common_keyframes（实测格式）。

    - 每个属性一组，`property_type` 用 KFType*；
    - `time_offset` 单位微秒、相对片段起点；**第一个关键帧不写**（与剪映一致）；
    - `values` 是数字数组；控制点给全 0（线性）。
    """
    out = []
    for name, frames in (getattr(seg, "keyframes", None) or {}).items():
        items = []
        for index, frame in enumerate(frames or []):
            item = {
                "id": new_id(),
                "left_control": {"x": 0.0, "y": 0.0},
                "right_control": {"x": 0.0, "y": 0.0},
                "values": [float(getattr(frame, "value", 0.0))],
            }
            if index > 0:
                item["time_offset"] = int(getattr(frame, "time_offset", 0))
            items.append(item)
        if items:
            out.append({
                "id": new_id(),
                "material_id": "",
                "property_type": name,
                "keyframe_list": items,
            })
    return out


def _segment_dict(seg: Segment, project: Project, render_index: int, duration_us: Optional[int] = None) -> tuple:
    """Return (segment dict, extra materials to append to ``materials``)."""
    from .keyframe import normalize_curve

    companions = _companions(seg, project)
    # 基础调节量（亮度/对比度/高光…）：剪映放在 material_colors[]，
    # 以前这些数值只活在 FreeCut 的内存模型里，草稿里什么都没有。
    own = project.material(seg.material_id)
    if own is not None and own.get("adjust"):
        color = _material_color_json(own)
        companions.append(color)
    ref_objs = [c.get("_ref") for c in companions if c.get("_ref") is not None]
    refs = [c["id"] for c in companions]

    # keep the refs that point at real materials we are writing, in the same order
    for rid in seg.extra_material_refs:
        mat = project.material(rid)
        if mat is not None and mat.id not in refs:
            refs.append(mat.id)
            ref_objs.append(mat)

    # keep source/target in the same ratio the app expects
    tgt_dur = int(duration_us if duration_us is not None else seg.target_timerange.duration)
    src_dur = int(seg.source_timerange.duration)
    if duration_us is not None and seg.target_timerange.duration > 0:
        ratio = duration_us / float(seg.target_timerange.duration)
        src_dur = max(1, int(round(src_dur * ratio)))

    d = {
        "id": seg.id,
        "material_id": seg.material_id,
        "target_timerange": {"start": int(seg.target_timerange.start), "duration": tgt_dur},
        "source_timerange": {"start": int(seg.source_timerange.start), "duration": src_dur},
        "render_timerange": {},
        "speed": float(seg.speed),
        "volume": float(seg.volume),
        "last_nonzero_volume": float(seg.volume) or 1.0,
        "visible": bool(seg.visible),
        "render_index": int(render_index),
        "track_render_index": int(seg.render_index),
        "clip": {
            "alpha": float(seg.clip.alpha),
            "flip": {"horizontal": bool(seg.clip.flip_h), "vertical": bool(seg.clip.flip_v)},
            "rotation": float(seg.clip.rotation),
            "scale": {"x": float(seg.clip.scale_x), "y": float(seg.clip.scale_y)},
            "transform": {"x": float(seg.clip.tx), "y": float(seg.clip.ty)},
        },
        "uniform_scale": {"on": True, "value": 1.0},
        "extra_material_refs": refs,
        "common_keyframes": _keyframes(seg),
        "keyframe_refs": [],
        "responsive_layout": {},
        "source": "segmentsourcenormal",
        "hdr_settings": {"intensity": 1.0, "mode": 1, "nits": 1000},
        # 开关按"实际有没有这个效果"写，不再一律 True：
        # 写了一个永远为真的开关，等于对外宣称一个不存在的行为
        "enable_adjust": bool(own is not None and own.get("adjust")),
        # 调色数值现在组成 material_colors，开关因此是真的
        "enable_adjust_mask": bool(seg.enable.get("enable_adjust_mask", False)),
        "enable_lut": _has(ref_objs, "filter") or bool(seg.enable.get("enable_lut", False)),
        "enable_hsl": _has(ref_objs, "hsl") or bool(seg.enable.get("enable_hsl", False)),
        "enable_color_curves": _has(ref_objs, "curve") or bool(seg.enable.get("enable_color_curves", False)),
        "enable_hsl_curves": _has(ref_objs, "hsl_curves") or bool(seg.enable.get("enable_hsl_curves", False)),
        "enable_color_wheels": _has(ref_objs, "color_wheel") or bool(seg.enable.get("enable_color_wheels", False)),
        "enable_video_mask": (_has(ref_objs, "mask")
                              and bool(seg.enable.get("enable_video_mask", True)))
                              or bool(seg.enable.get("enable_video_mask", False)),
        "is_loop": False,
        "is_placeholder": False,
        "reverse": False,
    }
    if seg.keyframes:
        for prop, items in seg.keyframes.items():
            if not items:
                continue
            d["common_keyframes"].append(
                {
                    "property_type": prop,
                    "keyframe_list": [
                        {
                            "time_offset": int(k.time_offset),
                            "values": [{"x": float(k.value), "y": 0.0}],
                            "curveType": normalize_curve(k.curve_type),
                            "graphID": "",
                            "string_value": "",
                            "left_control": {"x": 0.0, "y": 0.0},
                            "right_control": {"x": 0.0, "y": 0.0},
                        }
                        for k in items
                    ],
                }
            )
    return d, companions


def _filter_json(mat: FilterMaterial) -> Dict[str, Any]:
    """``materials.effects[]`` entry for a filter (field set from prior art)."""
    name = str(mat.get("name") or "")
    rid = str(mat.get("resource_id") or "")
    eid = str(mat.get("effect_id") or rid)
    if not rid and name:
        try:
            from . import catalog

            hit = catalog.find_filter(name=name)
            if hit:
                rid = hit["resource_id"]
                eid = hit["effect_id"]
        except Exception:
            pass
    return {
        "adjust_params": [],
        "algorithm_artifact_path": "",
        "apply_target_type": int(mat.get("apply_target_type", 0) or 0),
        "bloom_params": None,
        "category_id": "",
        "category_name": "",
        "color_match_info": {
            "source_feature_path": "",
            "target_feature_path": "",
            "target_image_path": "",
        },
        "effect_id": eid,
        "enable_skin_tone_correction": False,
        "exclusion_group": [],
        "face_adjust_params": [],
        "formula_id": "",
        "id": mat.id,
        "intensity_key": "",
        "multi_language_current": "",
        "name": name,
        "panel_id": "",
        "platform": "all",
        "resource_id": rid,
        "source_platform": 1,
        "sub_type": "none",
        "time_range": None,
        "type": "filter",
        "value": float(mat.get("value", mat.get("intensity_value", 1.0)) or 1.0),
        "version": "",
    }


def _mix_mode_json(mat: MixModeMaterial) -> Dict[str, Any]:
    name = str(mat.get("name") or "")
    rid = str(mat.get("resource_id") or "")
    eid = str(mat.get("effect_id") or "")
    if name and not rid:
        try:
            from . import catalog

            for item in catalog.mix_modes():
                if item["name"] == name:
                    rid, eid = item["resource_id"], item["effect_id"]
                    break
        except Exception:
            pass
    return {
        "type": "mix_mode",
        "name": name,
        "effect_id": eid,
        "resource_id": rid,
        "value": 1.0,
        "apply_target_type": 0,
        "platform": "all",
        "source_platform": 0,
        "category_id": "",
        "category_name": "",
        "sub_type": "none",
        "time_range": None,
        "id": mat.id,
    }


def _mask_json(mat: MaskMaterial, modern: bool) -> Dict[str, Any]:
    cfg = mat.config
    cfg_out = {
        "aspectRatio": float(cfg.get("aspectRatio", 1.0)),
        "centerX": float(cfg.get("centerX", 0.0)),
        "centerY": float(cfg.get("centerY", 0.0)),
        "feather": float(cfg.get("feather", 0.0)),
        "height": float(cfg.get("height", 1.0)),
        "invert": bool(cfg.get("invert", False)),
        "rotation": float(cfg.get("rotation", 0.0)),
        "roundCorner": float(cfg.get("roundCorner", 0.0)),
        "width": float(cfg.get("width", 1.0)),
    }
    # 保留额外参数（色度抠图的 key/similarity/…）：剪映会忽略不认识的键，
    # 但我们自己的导出/导入必须无损
    for extra_key, extra_val in cfg.items():
        if extra_key not in cfg_out:
            cfg_out[extra_key] = extra_val
    base = {
        "config": cfg_out,
        "id": mat.id,
        "name": str(mat.get("name") or "圆形"),
        "platform": "all",
        "position_info": "",
        "resource_type": str(mat.get("resource_type") or "circle"),
        "resource_id": str(mat.get("resource_id") or ""),
        "type": "mask",
    }
    if modern:
        base.update(
            {
                "category": "video",
                "category_id": "jichu",
                "category_name": "基础",
                "is_old_version": False,
                "loader_work_space": "",
                "panel": "",
                "source_platform": 0,
                "track_segment": "",
                "text_config": {
                    "align_type": 15,
                    "bold_width": 0.0,
                    "char_spacing": 0.0,
                    "content": "",
                    "font_name": "",
                    "font_path": "",
                    "font_resource_id": "",
                    "font_size": 0.0,
                    "font_title": "",
                    "letter_spacing": 0.0,
                    "line_spacing": 0.0,
                    "text_color": "",
                    "text_size": 0.0,
                },
            }
        )
    return base


def _segment_kind_for(project: Project, material_id: str) -> str:
    """Return "text" when the animation is attached to a text clip."""
    for track in project.tracks:
        for seg in track.segments:
            if material_id in seg.extra_material_refs:
                mat = project.material(seg.material_id)
                if mat is not None and getattr(mat, "type", "") == "text":
                    return "text"
    return "video"


def _animation_json(mat: AnimationMaterial, kind: str) -> Dict[str, Any]:
    """``materials.material_animations[]`` entry (shape from prior art)."""
    is_video = kind != "text"
    items = []
    for entry in mat.animations:
        anim_type = str(entry.get("type") or "in")
        name = str(entry.get("name") or "")
        rid = str(entry.get("resource_id") or "")
        eid = str(entry.get("effect_id") or rid)
        if not rid:
            try:
                from . import catalog

                cat = {"in": "video_in", "out": "video_out", "loop": "text_loop"}.get(anim_type, "video_in")
                if not is_video:
                    cat = {"in": "text_in", "out": "text_out", "loop": "text_loop"}.get(anim_type, "text_in")
                hit = catalog.find_animation(name, cat)
                if hit:
                    rid, eid = hit["resource_id"], hit["effect_id"]
            except Exception:
                pass
        items.append(
            {
                "anim_adjust_params": None,
                "platform": "all",
                "panel": "video" if is_video else "",
                "material_type": "video" if is_video else "sticker",
                "name": name,
                "id": eid,
                "type": anim_type,
                "resource_id": rid,
                "start": int(entry.get("start", 0) or 0),
                "duration": int(entry.get("duration", 0) or 0),
            }
        )
    return {
        "id": mat.id,
        "type": "sticker_animation",
        "multi_language_current": "none",
        "animations": items,
    }


# --------------------------------------------------------------------------- #
# content / meta
# --------------------------------------------------------------------------- #
def build_content(project: Project, warnings: Optional[List[str]] = None) -> Dict[str, Any]:
    content = copy.deepcopy(CONTENT_TEMPLATE)
    content["id"] = project.id or new_id()
    content["name"] = project.name
    content["fps"] = float(project.fps or 30.0)
    content["duration"] = int(project.duration())
    now = _now_us()
    content["create_time"] = now
    content["update_time"] = now
    content["canvas_config"] = {
        "width": int(project.canvas.width),
        "height": int(project.canvas.height),
        "ratio": project.canvas.ratio or _canvas_ratio(project.canvas.width, project.canvas.height),
    }
    if project.canvas.background:
        content["canvas_config"]["background"] = project.canvas.background

    materials: Dict[str, List[Dict[str, Any]]] = {k: [] for k in MATERIAL_ARRAYS}

    for track in project.tracks:
        segs = []
        ordered = track.sorted_segments()
        for i, seg in enumerate(ordered):
            # 调节图层是 Premiere 的概念，剪映草稿里没有对应物：直接跳过，
            # 而不是留下一条指向不存在素材的片段（那会让校验器报错）
            if seg.enable.get("enable_adjust_mask") and warnings is not None:
                warnings.append(
                    "调节蒙版（只限制调色、不裁画面）是我们的实现：草稿里会写 "
                    "enable_adjust_mask，但剪映把蒙版当作画面裁切，在剪映里看起来会不一样")
            if seg.is_adjustment or getattr(project.material(seg.material_id), "type", "") == "adjustment":
                if warnings is not None:
                    warnings.append(
                        f"调节图层 {seg.target_timerange.start/1e6:.2f}s (+"
                        f"{seg.target_timerange.duration/1e6:.2f}s) 剪映不支持，已跳过"
                    )
                continue
            # A JianyingPro track cannot hold overlapping clips: when FreeCut
            # uses an overlap to express a transition, trim the outgoing clip so
            # the pair becomes adjacent (the app then plays its own transition).
            dur = seg.target_timerange.duration
            if i + 1 < len(ordered):
                nxt = ordered[i + 1].target_timerange.start
                if nxt < seg.target_timerange.start + dur:
                    dur = max(1, nxt - seg.target_timerange.start)
            if warnings is not None:
                for ref in seg.extra_material_refs:
                    em = project.materials.get(ref)
                    if em is not None and getattr(em, "type", "") == "effect":
                        try:
                            from .effects import label as _fx_label

                            name = _fx_label(str(em.get("kind") or ""))
                        except Exception:
                            name = str(em.get("kind") or "特效")
                        warnings.append(
                            f"特效「{name}」剪映草稿没有对应资源，已跳过"
                            "（参数保存在 freecut_extras.json，可无损往返）"
                        )
            seg_dict, companions = _segment_dict(seg, project, i, duration_us=dur)
            segs.append(seg_dict)
            # companions go into their own arrays, keyed by their "type"
            for comp in companions:
                bucket = {
                    "speed": "speeds",
                    "placeholder_info": "placeholder_infos",
                    "canvas_color": "canvases",
                    "vocal_separation": "vocal_separations",
                }.get(comp.get("type", ""))
                if bucket:
                    materials[bucket].append(comp)
                elif comp.get("type") == "":
                    materials["sound_channel_mappings"].append(comp)
                else:
                    materials["material_colors"].append(comp)
        if not segs:
            continue  # 整条轨都是剪映表达不了的东西（例如只有调节图层）
        content["tracks"].append(
            {
                "id": track.id,
                "type": track.type,
                "is_default_name": True,
                "name": track.name or "",
                "attribute": 0,
                "flag": 0,
                "segments": segs,
            }
        )

    # real materials
    for mat in project.materials.values():
        if isinstance(mat, VideoMaterial):
            materials["videos"].append(_plain(mat))
        elif isinstance(mat, ImageMaterial):
            materials["images"].append(_plain(mat))
        elif isinstance(mat, AudioMaterial):
            materials["audios"].append(_plain(mat))
        elif isinstance(mat, TextMaterial):
            materials["texts"].append(_plain(mat))
        elif isinstance(mat, TransitionMaterial):
            materials["transitions"].append(_plain(mat))
        elif isinstance(mat, SpeedMaterial):
            materials["speeds"].append(_plain(mat))
        elif isinstance(mat, FilterMaterial):
            # the app keeps filters in `effects` with type "filter"
            materials["effects"].append(_filter_json(mat))
        elif isinstance(mat, MixModeMaterial):
            materials["effects"].append(_mix_mode_json(mat))
        elif isinstance(mat, MaskMaterial):
            materials["common_mask"].append(_mask_json(mat, modern=True))
            materials["masks"].append(_mask_json(mat, modern=False))
            if str(mat.get("resource_type")) == "chroma" and warnings is not None:
                warnings.append(
                    "色度抠图是 FreeCut 自有实现：参数会完整写进草稿，"
                    "但剪映没有这个蒙版类型，在剪映里不会生效")
        elif isinstance(mat, AnimationMaterial):
            materials["material_animations"].append(
                _animation_json(mat, _segment_kind_for(project, mat.id))
            )
        elif mat.type in ("audio_fade",):
            materials["audio_fades"].append(_plain(mat))
        elif mat.type in ("curve", "color_wheel", "hsl"):
            # 曲线 / 色轮 / HSL 分带的数值以前只留在旁路里，草稿里只剩开关，
            # 于是"开关说开了、草稿里没数据"。现在用剪映自己的数组名写进去：
            # 结构是我们在逆向里见过的（这些数组在真实草稿里存在但为空），
            # 剪映认不认这套字段形状未证实，导出时会提示。
            if mat.type == "color_wheel":
                # 两套色轮是两个独立效果：5019 primaryWheel / 5020 logWheel
                array = "log_color_wheels" if mat.get("log") else "primary_color_wheels"
            else:
                array = {"curve": "color_curves", "hsl": "hsl"}[mat.type]
            materials[array].append(_plain(mat))
            if warnings is not None:
                warnings.append(
                    f"{mat.type} 的数值已写进草稿的 {array}[]，但字段形状未证实，"
                    "剪映里可能不生效（在 FreeCut 里能完整往返）")
        elif mat.type == "hsl_curves":
            materials["hsl_curves"].append(_plain(mat))
            if warnings is not None:
                warnings.append(
                    "HSL 曲线是我们自己的数据结构（剪映有这个功能位，但字段形状未被证实）："
                    "参数会写进草稿，在剪映里可能不生效")
        elif mat.type in ("adjust", "material_color"):
            materials["material_colors"].append(_plain(mat))
        elif mat.type in MATERIAL_ARRAYS:
            materials.setdefault(mat.type, []).append(_plain(mat))
        # anything else is FreeCut-only and not representable

    content["materials"] = materials

    # A draft may only reference materials it actually contains.  Materials the
    # app cannot express (FreeCut tone curves / HSL bands / local LUT paths) are
    # moved into a sidecar so a FreeCut round-trip keeps them.
    written: Dict[str, str] = {}
    for category, items in materials.items():
        for item in items:
            mid = item.get("id")
            if mid:
                written[mid] = category

    extras: Dict[str, Any] = {"version": 1, "materials": {}, "segment_refs": {}}
    for track in content["tracks"]:
        for seg in track["segments"]:
            kept = []
            dropped = []
            for ref in seg.get("extra_material_refs") or []:
                if ref in written:
                    kept.append(ref)
                else:
                    dropped.append(ref)
            if dropped:
                seg["extra_material_refs"] = kept
                extras["segment_refs"][seg["id"]] = dropped

    for mat in project.materials.values():
        if mat.id in written:
            continue
        extras["materials"][mat.id] = _plain(mat)

    if extras["materials"]:
        content["_freecut_extras"] = extras

    # 开关校对：材质被移进旁路（剪映表达不了）之后，那些开关就不该再是 True。
    # 否则草稿里会出现"开着 LUT 但没有 LUT"这种自相矛盾的段落（第 86 轮审计抓到）。
    _truth_pass(content)
    return content


#: 开关 → 打开时必须有哪种材质真正写进草稿
_SWITCH_NEEDS = {
    "enable_lut": ("effects",),
    "enable_hsl": ("hsl",),
    "enable_color_curves": ("color_curves",),
    "enable_hsl_curves": ("hsl_curves",),
    "enable_color_wheels": ("primary_color_wheels", "log_color_wheels"),
    "enable_adjust": ("material_colors",),
    "enable_video_mask": ("masks", "common_mask"),
    "enable_adjust_mask": ("masks", "common_mask"),
}


def _truth_pass(content: Dict[str, Any]) -> None:
    """Turn off switches whose backing material did not survive the export."""
    index: Dict[str, str] = {}
    for category, items in (content.get("materials") or {}).items():
        if not isinstance(items, list):
            continue
        for item in items:
            mid = item.get("id")
            if mid:
                index[mid] = category
    for track in content.get("tracks") or []:
        for seg in track.get("segments") or []:
            kinds = {index.get(r) for r in (seg.get("extra_material_refs") or [])
                     if r in index}
            for switch, needed in _SWITCH_NEEDS.items():
                if seg.get(switch) and not (kinds & set(needed)):
                    seg[switch] = False


def write_cover(project: Project, draft_dir: str, size: int = 480) -> str:
    """Render frame 0 as ``draft_cover.jpg`` so the project list has a thumbnail.

    JianyingPro looks for this file (``draft_meta_info.draft_cover``); without it
    the app shows an empty square until the draft is opened once.
    """
    path = os.path.join(draft_dir, "draft_cover.jpg")
    try:
        from PIL import Image

        from .render import Renderer

        r = Renderer(project)
        try:
            frame = r.render_frame(0)
            h, w = frame.shape[:2]
            scale = size / float(max(1, max(w, h)))
            img = Image.fromarray(frame).resize((max(1, int(w * scale)), max(1, int(h * scale))))
            img.convert("RGB").save(path, quality=88)
        finally:
            r.close()
    except Exception:
        # a missing thumbnail must never fail an export
        try:
            from PIL import Image

            Image.new("RGB", (size, int(size * 9 / 16)), (32, 32, 40)).save(path, quality=80)
        except Exception:
            return ""
    return path


def build_meta(project: Project, draft_dir: str, draft_id: str) -> Dict[str, Any]:
    meta = copy.deepcopy(META_TEMPLATE)
    meta["draft_id"] = draft_id
    meta["draft_name"] = project.name
    meta["draft_fold_path"] = draft_dir.replace("\\", "/")
    meta["draft_root_path"] = os.path.dirname(draft_dir).replace("\\", "/")
    meta["draft_new_version"] = CONTENT_TEMPLATE["new_version"]
    now_us = _now_us()
    meta["tm_duration"] = int(project.duration())
    meta["tm_draft_create"] = now_us
    meta["tm_draft_modified"] = now_us
    drive, _ = os.path.splitdrive(os.path.abspath(draft_dir))
    meta["draft_removable_storage_device"] = (drive + ":") if drive else ""


    buckets: Dict[int, List[Dict[str, Any]]] = {0: [], 1: [], 2: [], 3: [], 6: [], 7: [], 8: []}
    seen = set()
    for mat in project.materials.values():
        bucket = _MEDIA_TYPE_BUCKET.get(mat.type)
        if bucket is None:
            continue
        path = str(mat.get("path") or "")
        if not path or path in seen:
            continue
        seen.add(path)
        buckets[bucket].append(
            {
                "duration": int(mat.get("duration", 0) or 0),
                "height": int(mat.get("height", 0) or 0),
                "width": int(mat.get("width", 0) or 0),
                "extra_info": os.path.basename(path),
                "file_Path": path.replace("\\", "/"),
                "file_name": os.path.basename(path),
                "import_time": int(time.time()),
                "import_time_ms": int(time.time() * 1000),
                "item_id": new_id(),
                "metetype": mat.type,
                "roughcut_time_range": {"duration": -1, "start": -1},
                "sub_time_range": {"duration": -1, "start": -1},
                "type": bucket,
            }
        )
    meta["draft_materials"] = [{"type": t, "value": buckets[t]} for t in (0, 1, 2, 3, 6, 7, 8)]
    return meta


# --------------------------------------------------------------------------- #
# writing
# --------------------------------------------------------------------------- #
def save_draft(
    project: Project,
    root_dir: str,
    name: Optional[str] = None,
    encrypt: Optional[bool] = None,
    overwrite: bool = True,
    warnings: Optional[List[str]] = None,
) -> str:
    """Write ``project`` as a JianyingPro draft folder and return its path.

    ``encrypt=None`` means "encrypt when the local JianyingPro can do it".
    ``warnings`` (optional list) receives notes about anything the 剪映 draft
    format cannot represent, e.g. adjustment layers.
    """
    draft_name = (name or project.name or "FreeCut 草稿").strip() or "FreeCut 草稿"
    draft_dir = os.path.join(root_dir, draft_name)
    # 必须在删除旧目录**之前**读：剪映探针的记录留在草稿目录里，
    # 内容没变时它仍然成立（读取时会比对 draft_content 的 md5）。
    probe_record = os.path.join(draft_dir, "freecut_jianying_probe.json")
    kept_probe = None
    if os.path.isfile(probe_record):
        try:
            with open(probe_record, encoding="utf-8") as fh:
                kept_probe = fh.read()
        except Exception:
            kept_probe = None
    if os.path.isdir(draft_dir):
        if not overwrite:
            raise FileExistsError(draft_dir)
        shutil.rmtree(draft_dir)
    os.makedirs(draft_dir, exist_ok=True)

    draft_id = project.id or new_id()
    collected: List[str] = []
    content = build_content(project, warnings=collected)
    if warnings is not None:
        warnings.extend(collected)
    extras = content.pop("_freecut_extras", None)
    meta = build_meta(project, draft_dir, draft_id)
    if not os.path.isfile(os.path.join(draft_dir, "draft_cover.jpg")):
        write_cover(project, draft_dir)

    content_bytes = json.dumps(content, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    # 剪映用这个字段显示草稿大小；必须在 meta 序列化之前填
    meta["draft_timeline_materials_size_"] = len(content_bytes)
    meta_bytes = json.dumps(meta, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    if encrypt is None:
        # 默认写明文：11.4.7 自己就把 draft_meta_info 写成明文，而且我们的
        # 加解密回环在新进程里不成立（见 tools 里的探针），所以"假装加密"只会
        # 让剪映读不了。需要加密时显式传 encrypt=True / --encrypt。
        encrypt = False

    if encrypt:
        from .jianying_crypto import DraftCryptoError, encrypt_bytes

        try:
            content_bytes = encrypt_bytes(content_bytes)
            meta_bytes = encrypt_bytes(meta_bytes)
        except DraftCryptoError as exc:
            encrypt = False
            if warnings is not None:
                warnings.append(f"加密不可用（{exc}），已改为写明文草稿")

    with open(os.path.join(draft_dir, "draft_content.json"), "wb") as fh:
        fh.write(content_bytes)
    with open(os.path.join(draft_dir, "draft_meta_info.json"), "wb") as fh:
        fh.write(meta_bytes)
    with open(os.path.join(draft_dir, "draft_settings"), "w", encoding="utf-8") as fh:
        now = int(time.time())
        fh.write("[General]\ndraft_create_time={0}\ndraft_last_edit_time={1}\n"
                 "real_edit_seconds=0\nreal_edit_keys=0\n".format(now, now))
    if extras:
        # FreeCut-only materials (tone curves, HSL bands, local LUT paths) that
        # the app has no representation for; kept beside the draft so a
        # FreeCut -> draft -> FreeCut round-trip is lossless.
        with open(os.path.join(draft_dir, "freecut_extras.json"), "w", encoding="utf-8") as fh:
            json.dump(extras, fh, ensure_ascii=False, indent=1)
    if kept_probe:
        try:
            with open(probe_record, "w", encoding="utf-8") as fh:
                fh.write(kept_probe)
        except Exception:
            pass

    return draft_dir


def _root_meta_path() -> Optional[str]:
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return None
    p = os.path.join(local, "JianyingPro", "User Data", "Projects",
                     "com.lveditor.draft", "root_meta_info.json")
    return p if os.path.isfile(p) else None


def register_draft(draft_dir: str, backup: bool = True) -> str:
    """Add ``draft_dir`` to the app's project list (``root_meta_info.json``).

    The file is copied to ``root_meta_info.json.freecut-backup`` first, because
    damaging it hides every existing project from the app's start screen.
    """
    root = _root_meta_path()
    if root is None:
        raise FileNotFoundError("找不到剪映的 root_meta_info.json（未安装或路径不同）")

    with open(root, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    if backup:
        shutil.copy2(root, root + ".freecut-backup")

    draft_dir = os.path.abspath(draft_dir)
    with open(os.path.join(draft_dir, "draft_meta_info.json"), "rb") as fh:
        raw = fh.read()
    if raw.lstrip()[:1] == b"{":
        meta = json.loads(raw.decode("utf-8", "replace"))
    else:
        from .jianying_crypto import decrypt_bytes

        meta = json.loads(decrypt_bytes(raw).decode("utf-8", "replace"))

    draft_id = meta.get("draft_id") or new_id()
    store = [d for d in data.get("all_draft_store", []) if d.get("draft_id") != draft_id]
    store.insert(
        0,
        {
            "cloud_draft_cover": False,
            "cloud_draft_sync": False,
            "draft_cloud_last_action_download": False,
            "draft_cloud_purchase_info": "",
            "draft_cloud_template_id": "",
            "draft_cloud_tutorial_info": "",
            "draft_cloud_videocut_purchase_info": "",
            "draft_cover": os.path.join(draft_dir, "draft_cover.jpg").replace("\\", "/"),
            "draft_fold_path": draft_dir.replace("\\", "/"),
            "draft_id": draft_id,
            "draft_is_ai_shorts": False,
            "draft_is_cloud_temp_draft": False,
            "draft_is_invisible": False,
            "draft_is_pippit_draft": False,
            "draft_is_web_article_video": False,
            "draft_json_file": os.path.join(draft_dir, "draft_content.json").replace("\\", "/"),
            "draft_name": meta.get("draft_name", ""),
            "draft_new_version": meta.get("draft_new_version", ""),
            "draft_root_path": os.path.dirname(draft_dir).replace("\\", "/"),
            "draft_timeline_materials_size": 0,
            "draft_type": "",
            "draft_web_article_video_enter_from": "",
            "pippit_avatar_url": "",
            "pippit_extra_info": "",
            "pippit_id": "",
            "pippit_user_name": "",
            "streaming_edit_draft_ready": True,
            "tm_draft_cloud_completed": "",
            "tm_draft_cloud_entry_id": -1,
            "tm_draft_cloud_modified": 0,
            "tm_draft_cloud_parent_entry_id": -1,
            "tm_draft_cloud_space_id": -1,
            "tm_draft_cloud_user_id": -1,
            "tm_draft_create": _now_us(),
            "tm_draft_modified": _now_us(),
            "tm_draft_removed": 0,
            "tm_duration": int(meta.get("tm_duration", 0) or 0),
        },
    )
    data["all_draft_store"] = store
    data["draft_ids"] = len(store)
    with open(root, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)

    mappings_path = os.path.join(os.path.dirname(root), "..", "Config", "LocalDraftFolder",
                                 "draft_folder_mappings.json")
    return root

# --------------------------------------------------------------------------- #
# flattened export: render once, hand JianyingPro a single clip
# --------------------------------------------------------------------------- #
def flatten_project(project: Project, video_path: str) -> Project:
    """A one-clip project that plays exactly like the render.

    Everything FreeCut does that JianyingPro cannot express (grading values,
    effects, chroma key, adjustment layers) is already baked into the pixels,
    so this is WYSIWYG at the cost of editability.  Used by
    ``export-draft --flatten``.
    """
    from . import builder
    from .media import probe

    mini = builder.new_project(project.canvas.width, project.canvas.height,
                               project.fps, name=(project.name or "FreeCut") + " (展平)")
    mini.canvas.background = project.canvas.background
    total = project.duration()
    info = probe(video_path)
    has_audio = bool(info.has_audio) if info is not None else True
    seg = builder.add_video(mini, video_path, start=0, duration=total, track_index=0)
    seg.source_timerange.start = 0
    seg.source_timerange.duration = total
    mini.material(seg.material_id).extra["has_audio"] = has_audio
    return mini
