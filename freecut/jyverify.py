"""剪映草稿"前后对比"：证明剪映到底认没认我们写的字段。

为什么单独一个模块：``jyprobe`` 是**跑剪映**（启动、扰动、观察），
这里不启动任何东西，只对比两次落盘的**磁盘快照**。

用法（人只需要做一件事）：

1. ``freecut jianying-diff <草稿目录> --save``   ← 导出草稿之后立刻拍一张
2. 在剪映里打开这个草稿，随便动一下并保存，关掉
3. ``freecut jianying-diff <草稿目录>``          ← 看剪映改了什么

第 3 步的输出才是证据：

* ``duration`` 从 0 变成真实时长 → 剪映解析了时间线
* ``color_curves`` / ``primary_color_wheels`` 还在且长度没变 → 剪映保留了我们的调色字段
* 这些键**消失** → 剪映没认，重写时丢掉了（这就是需要知道的坏消息）

和 ``jyprobe.load_record`` 的区别：那个记录会在结构变化时作废（它要防的是
"记录和当前草稿对不上"），而这里**就是要看变化**，所以专门另存一份。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

SNAPSHOT_FILE = "freecut_jianying_snapshot.json"

#: 我们写进去、需要剪映"保留下来"才说明生效的数组字段
GRADE_ARRAYS = ("color_curves", "hsl", "primary_color_wheels", "log_color_wheels")


@dataclass
class Change:
    field: str
    before: Any
    after: Any
    note: str = ""

    def as_line(self) -> str:
        note = f"   {self.note}" if self.note else ""
        return f"{self.field:28} {self.before!s:>18}  →  {self.after!s:<18}{note}"


def snapshot(draft_dir: str) -> Dict[str, Any]:
    """Read the facts we care about out of a draft directory."""
    path = Path(draft_dir) / "draft_content.json"
    if not path.is_file():
        raise FileNotFoundError(str(path))
    raw = path.read_text(encoding="utf-8", errors="replace")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # 剪映 6+ 的草稿是加密的：借 jianying.load_draft 解开再比对
        from . import jianying

        project = jianying.load_draft(draft_dir, decrypt=True)
        data = project.to_dict()
    meta = data.get("meta") or {}
    materials = data.get("materials") or {}
    tracks = data.get("tracks") or []

    arrays = {}
    for name in GRADE_ARRAYS:
        value = materials.get(name)
        arrays[name] = len(value) if isinstance(value, list) else None

    return {
        "captured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "duration": int(data.get("duration") or 0),
        "meta_keys": sorted(meta),
        "meta_tm_duration": int(meta.get("tm_duration") or 0),
        "meta_materials_size": int(meta.get("draft_timeline_materials_size_") or 0),
        "tracks": [{"type": t.get("type"), "segments": len(t.get("segments") or [])}
                   for t in tracks],
        "materials_total": sum(len(v) for v in materials.values() if isinstance(v, list)),
        "grade_arrays": arrays,
        "encrypt": Path(draft_dir, "draft_content.json").stat().st_size,
    }


def save_snapshot(draft_dir: str, data: Optional[Dict[str, Any]] = None) -> str:
    """Store a snapshot next to the draft; returns the file path."""
    payload = data if data is not None else snapshot(draft_dir)
    target = Path(draft_dir) / SNAPSHOT_FILE
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(target)


def load_snapshot(draft_dir: str) -> Optional[Dict[str, Any]]:
    target = Path(draft_dir) / SNAPSHOT_FILE
    if not target.is_file():
        return None
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return None


def diff(before: Dict[str, Any], after: Dict[str, Any]) -> List[Change]:
    """Every observable difference, in a stable order."""
    out: List[Change] = []

    if int(before.get("duration", 0)) != int(after.get("duration", 0)):
        note = ""
        if int(before.get("duration", 0)) == 0:
            note = "剪映算出了时长"
        out.append(Change("duration", before.get("duration"), after.get("duration"), note))

    for key, label in (("meta_tm_duration", "meta.tm_duration"),
                       ("meta_materials_size", "meta.素材体积")):
        if int(before.get(key) or 0) != int(after.get(key) or 0):
            note = "剪映重新统计了" if key == "meta_materials_size" else ""
            out.append(Change(label, before.get(key), after.get(key), note))

    old_keys = set(before.get("meta_keys") or [])
    new_keys = set(after.get("meta_keys") or [])
    for key in sorted(new_keys - old_keys):
        out.append(Change(f"meta+ {key}", "—", "有", "剪映新增"))
    for key in sorted(old_keys - new_keys):
        out.append(Change(f"meta- {key}", "有", "—", "剪映删掉了"))

    if (before.get("tracks") or []) != (after.get("tracks") or []):
        out.append(Change("tracks", _tracks(before), _tracks(after)))

    if int(before.get("materials_total") or 0) != int(after.get("materials_total") or 0):
        out.append(Change("materials 总数", before.get("materials_total"),
                          after.get("materials_total")))

    old_arrays = before.get("grade_arrays") or {}
    new_arrays = after.get("grade_arrays") or {}
    for name in GRADE_ARRAYS:
        old, new = old_arrays.get(name), new_arrays.get(name)
        if old == new:
            continue
        if new is None:
            note = "剪映没有保留这个字段"
        elif old in (None, 0):
            note = "剪映补上了"
        else:
            note = "长度变了"
        out.append(Change(f"materials.{name}", old, new, note))

    return out


def _tracks(side: Dict[str, Any]) -> str:
    tracks = side.get("tracks") or []
    return "、".join(f"{t.get('type')}×{t.get('segments')}" for t in tracks) or "无"


def verdict(changes: List[Change]) -> str:
    """One line saying whether 剪映 kept our writes."""
    if not changes:
        return "没有任何变化——剪映没有回写这个草稿（或者没打开过）"
    dropped = [c for c in changes if c.note == "剪映没有保留这个字段"]
    if dropped:
        names = "、".join(c.field.split(".", 1)[-1] for c in dropped)
        return f"剪映丢掉了我写的字段：{names}（这些效果在剪映里不生效）"
    kept = [c for c in changes if c.field.startswith("materials.")]
    if kept:
        return "剪映保留并处理了调色字段——写回格式得到验证"
    return "剪映回写了草稿，但没有动调色字段（可能这个片段没用到）"
