"""响度统一：用 ffmpeg 的 EBU R128 测量素材响度，再把片段音量补到目标 LUFS。

剪映的「响度统一」是逐段测量再加限幅；这里做同一件事的离线版：
- 测量：``ffmpeg -af ebur128`` 的汇总行（``I: -18.0 LUFS``）
- 应用：把片段的 ``volume`` 乘上 10^((target - measured)/20)，并夹在 ±limit_db 内
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from typing import List, Optional, Tuple

_LUFS_RE = re.compile(r"I:\s*(-?\d+(?:\.\d+)?)\s*LUFS")


def ffmpeg_path() -> Optional[str]:
    env = os.environ.get("FREECUT_FFMPEG")
    if env and os.path.isfile(env):
        return env
    return shutil.which("ffmpeg")


def measure_lufs(path: str, timeout: float = 30.0) -> Optional[float]:
    """返回整段素材的积分响度（LUFS）；测不出来返回 None。"""
    exe = ffmpeg_path()
    if not exe or not os.path.isfile(path):
        return None
    cmd = [exe, "-hide_banner", "-nostats", "-i", path,
           "-af", "ebur128=framelog=quiet", "-f", "null", "-"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = (out.stderr or "") + (out.stdout or "")
    matches = _LUFS_RE.findall(text)
    if not matches:
        return None
    try:
        return float(matches[-1])
    except ValueError:
        return None


def gain_db(target: float, measured: float, limit_db: float = 12.0) -> float:
    """计算需要补的增益（dB），夹在 ±limit_db。"""
    value = float(target) - float(measured)
    return max(-abs(limit_db), min(abs(limit_db), value))


def normalize_project(project, target: float = -14.0, limit_db: float = 12.0
                      ) -> List[Tuple[object, float]]:
    """把工程里所有音频片段补到目标响度，返回 [(片段, 实际增益 dB)]。

    只处理**有声素材**（磁盘上存在的文件）；测不出响度的跳过。
    """
    applied = []
    cache = {}
    for track in project.tracks:
        for seg in track.segments:
            mat = project.material(seg.material_id)
            if mat is None:
                continue
            if getattr(mat, "type", "") not in ("audio", "video"):
                continue
            path = ""
            try:
                path = mat.get("path") or ""
            except Exception:                          # noqa: BLE001
                path = getattr(mat, "path", "") or ""
            if not path or not os.path.isfile(str(path)):
                continue
            if str(path) not in cache:
                cache[str(path)] = measure_lufs(str(path))
            measured = cache[str(path)]
            if measured is None:
                continue
            delta = gain_db(target, measured, limit_db)
            if abs(delta) < 0.05:
                continue
            seg.volume = float(seg.volume or 1.0) * (10.0 ** (delta / 20.0))
            applied.append((seg, delta))
    return applied
