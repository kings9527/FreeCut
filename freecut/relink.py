"""Missing-media detection and relinking.

Projects store **absolute** paths (both JianyingPro and Premiere do), so moving
a project or a media folder breaks every clip.  This module finds the broken
references and, given a folder to search, proposes replacements by file name
(and, when sizes match, prefers those), then rewrites the project.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence

from .model import Project


@dataclass
class MissingItem:
    material_id: str
    name: str
    path: str
    kind: str = ""
    candidates: List[str] = field(default_factory=list)


def _media_materials(project: Project) -> Iterable:
    for mat in project.materials.values():
        if getattr(mat, "type", "") in ("video", "audio", "image"):
            if str(mat.get("path") or ""):
                yield mat


def missing_media(project: Project) -> List[MissingItem]:
    """Every media material whose file is not on disk."""
    out: List[MissingItem] = []
    for mat in _media_materials(project):
        path = str(mat.get("path") or "")
        if path and not os.path.isfile(path):
            out.append(
                MissingItem(
                    material_id=mat.id,
                    name=str(mat.get("material_name") or os.path.basename(path)),
                    path=path,
                    kind=getattr(mat, "type", ""),
                )
            )
    return out


def index_media(roots: Sequence[str], limit: int = 20000) -> Dict[str, List[str]]:
    """Map basename -> list of full paths found under ``roots``."""
    index: Dict[str, List[str]] = {}
    seen = 0
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, _dirnames, filenames in os.walk(root):
            for fname in filenames:
                if fname.startswith("."):
                    continue
                full = os.path.join(dirpath, fname)
                index.setdefault(fname.lower(), []).append(full)
                seen += 1
                if seen > limit:
                    return index
    return index


def suggest(project: Project, roots: Sequence[str]) -> List[MissingItem]:
    """Attach candidate replacements to every missing item."""
    items = missing_media(project)
    if not items:
        return []
    index = index_media(roots)
    for item in items:
        key = os.path.basename(item.path).lower()
        found = list(index.get(key, []))
        if len(found) > 1:
            # prefer a candidate whose size matches the original file's sibling
            # (we cannot know the original size, so prefer shallower paths)
            found.sort(key=lambda p: (p.count(os.sep), len(p)))
        item.candidates = found[:8]
    return items


def apply_relink(project: Project, mapping: Dict[str, str]) -> int:
    """Rewrite material paths; ``mapping`` is material_id -> new path."""
    changed = 0
    for mid, new_path in mapping.items():
        mat = project.materials.get(mid)
        if mat is None or not new_path:
            continue
        if str(mat.get("path") or "") == new_path:
            continue
        mat["path"] = new_path
        changed += 1
    return changed


def auto_relink(project: Project, roots: Sequence[str]) -> int:
    """Relink everything that has exactly one candidate; returns the count."""
    mapping: Dict[str, str] = {}
    for item in suggest(project, roots):
        if len(item.candidates) == 1:
            mapping[item.material_id] = item.candidates[0]
    return apply_relink(project, mapping)


def format_report(items: Sequence[MissingItem]) -> str:
    if not items:
        return "所有素材都在线。"
    lines = [f"缺失素材 {len(items)} 个："]
    for item in items:
        lines.append(f"  [{item.kind}] {item.name}")
        lines.append(f"      原路径: {item.path}")
        if item.candidates:
            for c in item.candidates[:3]:
                lines.append(f"      候选  : {c}")
        else:
            lines.append("      候选  : （没找到同名文件）")
    return "\n".join(lines)
