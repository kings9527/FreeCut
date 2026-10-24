"""字段审计：草稿里**写了的**字段，渲染器到底**读不读**？

第 82 轮的教训：`highlight_mode` 一直在草稿里写着，但渲染器根本不读它，
于是"导出的结构"和"渲染的行为"各说一套话，从外面看不出来。

这个模块把两者的差距列出来：把草稿里出现的字段名，拿去渲染相关模块里搜一遍，
出现=有人读，不出现=写了没用（可能是纯元数据，也可能是漏实现）。

```powershell
python -m freecut field-audit out/showcase.json
```
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

#: 决定画面/声音的模块——字段只有在这里出现才算"渲染器读了"
RENDERER_MODULES = (
    "render.py", "text.py", "color.py", "transform.py", "media.py",
    "chroma.py", "mask.py", "audio.py", "speed.py", "keyframe.py",
    "animation.py", "effects.py",
)

#: 纯元数据字段：写在草稿里但本来就不影响画面，不算缺口
METADATA = {
    "id", "material_id", "extra_material_refs", "keyframe_refs",
    "is_placeholder", "is_loop", "last_nonzero_volume", "hdr_settings",
    "reverse", "cartoon", "intensifies_audio", "path", "name",
    "material_name", "type", "duration", "roughcut_time_range",
    "source_timerange", "target_timerange", "render_index", "track_render_index",
    "visible", "volume", "speed", "source", "target", "clip",
    "uniform_scale", "scale", "position", "rotation",
    # 纯元数据：剪映自己用来排版/分类/标记版本的字段，不影响画面
    "render_timerange", "responsive_layout", "meta_type", "line_feed",
    "shadow_smoothing", "background_width", "background_height",
    "background_horizontal_offset", "background_vertical_offset",
    "position_info", "category", "category_id", "category_name",
    "is_old_version", "loader_work_space", "source_platform", "text_config",
    # 云素材/合集标记：剪映用来关联线上资源，不影响本地渲染
    "multi_language_current", "is_overlap", "effect_id", "third_resource_id",
}


@dataclass
class FieldAudit:
    used: Dict[str, int]
    unused: Dict[str, int]

    @property
    def coverage(self) -> float:
        total = len(self.used) + len(self.unused)
        return (len(self.used) / total) if total else 1.0

    def as_lines(self, limit: int = 12) -> List[str]:
        lines = [f"字段审计：写了 {len(self.used) + len(self.unused)} 个字段，"
                 f"{len(self.used)} 个渲染器会读（{self.coverage * 100:.0f}%）"]
        if self.unused:
            items = sorted(self.unused.items(), key=lambda kv: -kv[1])[:limit]
            lines.append("  只写不读（可能是元数据，也可能是漏实现）：" +
                         "、".join(f"{k}×{n}" for k, n in items))
        return lines


def _corpus(freecut_dir: Optional[Path] = None) -> str:
    root = freecut_dir or Path(__file__).resolve().parent
    blob = []
    for name in RENDERER_MODULES:
        path = root / name
        if path.is_file():
            blob.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(blob)


def _collect_fields(content: dict) -> Dict[str, int]:
    counts: Dict[str, int] = {}

    def bump(key: str) -> None:
        counts[key] = counts.get(key, 0) + 1

    for track in content.get("tracks") or []:
        for segment in track.get("segments") or []:
            for key in segment:
                bump(key)
    for group in (content.get("materials") or {}).values():
        if not isinstance(group, list):
            continue
        for material in group:
            for key in material:
                bump(key)
    return counts


def audit(content: dict, freecut_dir: Optional[Path] = None) -> FieldAudit:
    corpus = _corpus(freecut_dir)
    counts = _collect_fields(content)
    used: Dict[str, int] = {}
    unused: Dict[str, int] = {}
    for key, count in counts.items():
        if key in METADATA:
            continue
        # 字段名出现在渲染模块里就算"有人读"；带 enable_ 前缀的是开关，
        # 去掉前缀再找一次（渲染器不会写 `enable_color_curves` 这种名字）
        probe = key[len("enable_"):] if key.startswith("enable_") else key
        if probe and probe in corpus:
            used[key] = count
        else:
            unused[key] = count
    return FieldAudit(used=used, unused=unused)

#: 开关打开时，这一段的引用里**必须**出现哪种材质
#: 注意：这里比对的是**材质数组名**（草稿里的分类），不是材质内部的 type
SWITCH_TO_MATERIAL = {
    "enable_lut": ("effects",),
    "enable_hsl": ("hsl",),
    "enable_color_curves": ("color_curves",),
    "enable_hsl_curves": ("hsl_curves",),
    "enable_color_wheels": ("primary_color_wheels", "log_color_wheels"),
    "enable_adjust": ("material_colors",),
    "enable_video_mask": ("masks", "common_mask"),
    "enable_adjust_mask": ("masks", "common_mask"),
}


def _material_index(content: dict) -> Dict[str, str]:
    """material id -> 它在哪个数组里（用来判类型）。"""
    index: Dict[str, str] = {}
    for name, group in (content.get("materials") or {}).items():
        if not isinstance(group, list):
            continue
        for material in group:
            mid = material.get("id")
            if mid:
                index[mid] = name
    return index


def audit_values(content: dict) -> List[str]:
    """取值级审计：开关开了却没有对应素材，或者引用了不存在的材质。

    字段级审计（:func:`audit`）只能说明"这个字段有人读"，
    说明不了"读到的值是真的"。这一层查的就是后者。
    """
    problems: List[str] = []
    index = _material_index(content)
    for track in content.get("tracks") or []:
        for segment in track.get("segments") or []:
            sid = str(segment.get("id") or "")[:8]
            refs = list(segment.get("extra_material_refs") or []) + list(
                segment.get("keyframe_refs") or [])
            ref_kinds = {index.get(r) for r in refs if r in index}
            dangling = [r for r in refs if r not in index]
            if dangling:
                problems.append(f"片段 {sid} 引用了不存在的材质: {dangling[:3]}")
            for switch, expected in SWITCH_TO_MATERIAL.items():
                if not segment.get(switch):
                    continue
                if not (ref_kinds & set(expected)):
                    problems.append(
                        f"片段 {sid} 打开了 {switch}，但没有对应的 {'/'.join(expected)} 材质")
    return problems
