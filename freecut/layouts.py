"""Multi-picture layouts (画中画 / 分屏).

JianyingPro ships these as 画中画 presets; Premiere as Motion presets.  Both are
just per-clip transform values, so a layout is a table of
``(scale, tx, ty)`` slots that get written onto each clip's ``clip`` block.

Coordinates follow the draft schema: ``tx``/``ty`` are normalised offsets where
**1.0 equals one canvas width/height**, and ``scale`` is relative to the
"contain" fit (1.0 = source fills the canvas while keeping its aspect ratio).
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from .model import Project, Segment

Slot = Tuple[float, float, float, float]   # w_frac, h_frac, tx, ty

#: name -> (slot list, human description)
#:
#: ``w_frac``/``h_frac`` are the slot's share of the canvas; the source is
#: centre-cropped to that aspect so the slot is **filled** instead of letterboxed
#: (that is what a split-screen preset is expected to do).
LAYOUTS: Dict[str, Tuple[List[Slot], str]] = {
    "全屏": ([(1.0, 1.0, 0.0, 0.0)], "单个片段铺满画布"),
    "左右分屏": ([(0.5, 1.0, -0.5, 0.0), (0.5, 1.0, 0.5, 0.0)], "两段并排，左 / 右"),
    "上下分屏": ([(1.0, 0.5, 0.0, 0.5), (1.0, 0.5, 0.0, -0.5)], "两段上下，上 / 下"),
    "左大右小": ([(0.62, 1.0, -0.38, 0.0), (0.38, 1.0, 0.62, 0.0)], "主画面在左，副画面在右"),
    "上大下小": ([(1.0, 0.62, 0.0, 0.38), (1.0, 0.38, 0.0, -0.62)], "主画面在上，副画面在下"),
    "四宫格": (
        [(0.5, 0.5, -0.5, 0.5), (0.5, 0.5, 0.5, 0.5),
         (0.5, 0.5, -0.5, -0.5), (0.5, 0.5, 0.5, -0.5)],
        "2×2 田字格",
    ),
    "三栏": ([(0.34, 1.0, -0.66, 0.0), (0.34, 1.0, 0.0, 0.0), (0.34, 1.0, 0.66, 0.0)],
             "三等分竖排"),
    "画中画·右下": ([(1.0, 1.0, 0.0, 0.0), (0.34, 0.34, 0.62, -0.62)], "主画面全屏 + 右下角小窗"),
    "画中画·左上": ([(1.0, 1.0, 0.0, 0.0), (0.34, 0.34, -0.62, 0.62)], "主画面全屏 + 左上角小窗"),
    "对角双窗": ([(0.48, 0.48, -0.5, 0.5), (0.48, 0.48, 0.5, -0.5)], "左上 / 右下斜对角"),
}


def names() -> List[str]:
    return list(LAYOUTS)


def describe(name: str) -> str:
    return LAYOUTS.get(name, ([], ""))[1]


def slots(name: str) -> List[Slot]:
    return list(LAYOUTS.get(name, ([], ""))[0])


def video_segments(project: Project) -> List[Segment]:
    """Video-track segments in timeline order (the order layouts consume)."""
    segs: List[Segment] = []
    for track in project.tracks:
        if track.type != "video":
            continue
        segs.extend(sorted(track.segments, key=lambda s: s.target_timerange.start))
    return segs


def _source_aspect(seg: Segment, project: Project) -> Optional[float]:
    mat = project.material(seg.material_id)
    if mat is None:
        return None
    try:
        w = float(mat.get("width", 0) or 0)
        h = float(mat.get("height", 0) or 0)
    except (TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    return w / h


def _ensure_private_material(project: Project, seg: Segment) -> None:
    """Give ``seg`` its own material when several clips share one.

    Layouts write ``crop`` onto the material; sharing it would crop the other
    clips too, so we clone on demand (cheap: one dict copy).
    """
    shared = sum(1 for tr in project.tracks for s in tr.segments if s.material_id == seg.material_id)
    if shared <= 1:
        return
    mat = project.material(seg.material_id)
    if mat is None:
        return
    payload = {k: v for k, v in mat.extra.items()}
    clone = type(mat)(**payload)
    project.add_material(clone)
    seg.material_id = clone.id


def apply(
    project: Project,
    name: str,
    segments: Optional[Sequence[Segment]] = None,
) -> int:
    """Apply layout ``name``; returns how many clips were positioned.

    Extra clips are left untouched (a 2-up layout on 3 clips only places two),
    which matches how the app behaves when you pick a preset for a selection.
    """
    table = LAYOUTS.get(name)
    if table is None:
        raise KeyError(f"没有这个布局: {name}")
    slot_list = table[0]
    targets = list(segments) if segments is not None else video_segments(project)
    canvas_w = float(project.canvas.width or 1920)
    canvas_h = float(project.canvas.height or 1080)
    canvas_aspect = canvas_w / canvas_h

    placed = 0
    for seg, (w_frac, h_frac, tx, ty) in zip(targets, slot_list):
        _ensure_private_material(project, seg)
        mat = project.material(seg.material_id)

        slot_aspect = (w_frac * canvas_w) / max(1e-6, h_frac * canvas_h)
        src_aspect = _source_aspect(seg, project)
        if mat is not None and src_aspect:
            if src_aspect > slot_aspect:                 # 源更宽 → 左右裁
                keep = slot_aspect / src_aspect
                x0 = (1.0 - keep) / 2.0
                crop = {"upper_left_x": x0, "upper_left_y": 0.0,
                        "lower_right_x": 1.0 - x0, "lower_right_y": 1.0}
            elif src_aspect < slot_aspect:               # 源更高 → 上下裁
                keep = src_aspect / slot_aspect
                y0 = (1.0 - keep) / 2.0
                crop = {"upper_left_x": 0.0, "upper_left_y": y0,
                        "lower_right_x": 1.0, "lower_right_y": 1.0 - y0}
            else:
                crop = None
            if crop:
                mat["crop"] = crop
            elif "crop" in mat.extra:
                mat.extra.pop("crop", None)

        # 裁剪后"装进画布"的缩放系数才是基准：算出恰好等于槽位所需的倍数
        src_w = src_h = 0.0
        if mat is not None:
            try:
                src_w = float(mat.get("width", 0) or 0)
                src_h = float(mat.get("height", 0) or 0)
            except (TypeError, ValueError):
                src_w = src_h = 0.0
        if src_w > 0 and src_h > 0 and "crop" in (mat.extra if mat is not None else {}):
            crop = mat.extra.get("crop") or {}
            cw = max(1.0, (float(crop.get("lower_right_x", 1.0)) - float(crop.get("upper_left_x", 0.0))) * src_w)
            ch = max(1.0, (float(crop.get("lower_right_y", 1.0)) - float(crop.get("upper_left_y", 0.0))) * src_h)
            contain = min(canvas_w / cw, canvas_h / ch)
            scale = (w_frac * canvas_w) / max(1e-9, contain * cw)
        else:
            scale = w_frac

        seg.clip.scale_x = float(scale)
        seg.clip.scale_y = float(scale)
        seg.clip.tx = float(tx)
        seg.clip.ty = float(ty)
        placed += 1
    return placed


def fit_count(project: Project, name: str) -> int:
    """How many clips ``name`` can position."""
    return min(len(LAYOUTS.get(name, ([], ""))[0]), len(video_segments(project)))
