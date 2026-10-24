"""Structural validation for JianyingPro draft folders.

The app silently drops a draft that references a missing material, so a
"looks right" JSON is not enough: every ``material_id`` and
``extra_material_refs`` entry has to resolve inside the same file.  This module
checks that, plus the timing invariants the timeline depends on.

    python -m freecut.jianying_validate "out/drafts/My Draft"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

TRACK_TYPES = {"video", "audio", "text", "sticker", "effect", "filter"}
KNOWN_CURVES = {"CurveIn", "CurveInOut", "FreeCurveIn", "FreeCurveOut", "FreeCurveInOut"}


@dataclass
class Report:
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_text(self) -> str:
        lines = []
        lines.append("统计: " + json.dumps(self.stats, ensure_ascii=False))
        for w in self.warnings:
            lines.append("  [警告] " + w)
        for e in self.errors:
            lines.append("  [错误] " + e)
        lines.append("结论: " + ("通过" if self.ok else f"失败（{len(self.errors)} 个错误）"))
        return "\n".join(lines)


def read_json(path: str) -> Dict[str, Any]:
    with open(path, "rb") as fh:
        raw = fh.read()
    if raw.lstrip()[:1] == b"{":
        return json.loads(raw.decode("utf-8", "replace"))
    from .jianying_crypto import decrypt_bytes

    return json.loads(decrypt_bytes(raw).decode("utf-8", "replace"))


def validate_content(content: Dict[str, Any]) -> Report:
    rep = Report()
    canvas = content.get("canvas_config") or {}
    w, h = int(canvas.get("width", 0) or 0), int(canvas.get("height", 0) or 0)
    if w <= 0 or h <= 0:
        rep.errors.append(f"canvas_config 宽高无效: {w}x{h}")

    materials = content.get("materials") or {}
    ids: Dict[str, str] = {}
    dupes = []
    # the app writes a mask into BOTH `masks` (old readers) and `common_mask`
    # (11.x readers) under the *same* id, so that pair is not a duplicate
    legacy_pair = {"masks", "common_mask"}
    for category, items in materials.items():
        for item in items or []:
            mid = item.get("id")
            if not mid:
                rep.errors.append(f"materials.{category} 有条目缺少 id")
                continue
            if mid in ids:
                if {ids[mid], category} <= legacy_pair:
                    continue
                dupes.append(mid)
            ids[mid] = category
    if dupes:
        rep.errors.append(f"重复的 material id: {dupes[:5]}")

    tracks = content.get("tracks") or []
    if not tracks:
        rep.warnings.append("草稿没有任何轨道")

    seg_count = 0
    kf_count = 0
    for ti, track in enumerate(tracks):
        ttype = track.get("type")
        if ttype not in TRACK_TYPES:
            rep.errors.append(f"轨道 {ti} 的 type 非法: {ttype!r}")
        segs = track.get("segments") or []
        if not segs:
            rep.warnings.append(f"轨道 {ti} ({ttype}) 没有任何片段")
        prev_end = None
        for si, seg in enumerate(sorted(segs, key=lambda s: (s.get("target_timerange") or {}).get("start", 0))):
            seg_count += 1
            where = f"track{ti}/{ttype}/seg{si}"
            tt = seg.get("target_timerange") or {}
            st = seg.get("source_timerange") or {}
            start, dur = int(tt.get("start", 0) or 0), int(tt.get("duration", 0) or 0)
            sstart, sdur = int(st.get("start", 0) or 0), int(st.get("duration", 0) or 0)
            if dur <= 0:
                rep.errors.append(f"{where}: target_timerange.duration={dur}")
            if start < 0:
                rep.errors.append(f"{where}: target_timerange.start={start}")
            if sstart < 0:
                rep.errors.append(f"{where}: source_timerange.start={sstart}")
            mid = seg.get("material_id")
            if not mid:
                rep.errors.append(f"{where}: 缺少 material_id")
            elif mid not in ids:
                rep.errors.append(f"{where}: material_id {mid} 不存在")
            for ref in seg.get("extra_material_refs") or []:
                if ref not in ids:
                    rep.errors.append(f"{where}: extra_material_refs 指向不存在的 {ref}")
            speed = float(seg.get("speed", 1.0) or 1.0)
            if speed <= 0:
                rep.errors.append(f"{where}: speed={speed}")
            elif sdur > 0 and dur > 0:
                implied = sdur / dur
                if abs(implied - speed) > 0.02 * max(1.0, speed):
                    rep.warnings.append(
                        f"{where}: source/target 比例 {implied:.4f} 与 speed {speed:.4f} 不一致"
                    )
            clip = seg.get("clip") or {}
            scale = clip.get("scale") or {}
            for axis in ("x", "y"):
                if float(scale.get(axis, 1.0) or 0.0) == 0.0:
                    rep.errors.append(f"{where}: scale.{axis}=0（片段将不可见）")
            for kf_group in seg.get("common_keyframes") or []:
                prop = kf_group.get("property_type")
                if not prop:
                    rep.errors.append(f"{where}: common_keyframes 缺少 property_type")
                items = kf_group.get("keyframe_list") or []
                if not items:
                    rep.warnings.append(f"{where}: {prop} 的关键帧列表为空")
                offsets = [int(k.get("time_offset", 0) or 0) for k in items]
                if offsets != sorted(offsets):
                    rep.warnings.append(f"{where}: {prop} 的关键帧未按时间排序")
                for k in items:
                    kf_count += 1
                    ct = k.get("curveType")
                    if ct and ct not in KNOWN_CURVES:
                        rep.warnings.append(f"{where}: 未知 curveType {ct!r}")
                    if not k.get("values"):
                        rep.errors.append(f"{where}: 关键帧缺少 values")
            if prev_end is not None and start < prev_end - 1:
                rep.warnings.append(
                    f"{where}: 与前一片段重叠 {prev_end - start} µs（剪映用重叠表示转场）"
                )
            prev_end = max(prev_end or 0, start + dur)

    for category in ("transitions",):
        for item in materials.get(category) or []:
            if not item.get("resource_id"):
                rep.warnings.append(f"transitions: {item.get('name')!r} 缺少 resource_id（剪映可能不识别）")
            if int(item.get("duration", 0) or 0) <= 0:
                rep.errors.append(f"transitions: {item.get('name')!r} duration<=0")

    rep.stats = {
        "canvas": f"{w}x{h}",
        "fps": content.get("fps"),
        "duration_us": content.get("duration"),
        "tracks": len(tracks),
        "segments": seg_count,
        "materials": len(ids),
        "non_empty_material_arrays": sum(1 for v in materials.values() if v),
        "keyframes": kf_count,
        "version": content.get("version"),
        "new_version": content.get("new_version"),
    }
    return rep


def validate_draft_dir(path: str) -> Report:
    json_path = os.path.join(path, "draft_content.json") if os.path.isdir(path) else path
    if not os.path.isfile(json_path):
        rep = Report()
        rep.errors.append(f"找不到 {json_path}")
        return rep
    return validate_content(read_json(json_path))


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser("freecut.jianying_validate", description="校验剪映草稿结构")
    ap.add_argument("path", help="草稿目录或 draft_content.json")
    ap.add_argument("--strict", action="store_true", help="把警告也当作失败")
    args = ap.parse_args(argv)
    rep = validate_draft_dir(args.path)
    print(rep.as_text())
    if not rep.ok:
        return 1
    if args.strict and rep.warnings:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
