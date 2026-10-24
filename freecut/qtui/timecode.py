"""时间码：剪映/PR 都用「时:分:秒:帧」而不是小数秒。

剪映专业版播放器显示 ``00:00:03:00 / 00:01:01:12``（HANDOFF §5.4）。
"""

from __future__ import annotations

from typing import Tuple


def frame_index(us: int, fps: float) -> int:
    """微秒 → 帧号（四舍五入，和播放头定位同一套算法）。"""
    fps = float(fps or 30.0)
    if fps <= 0:
        fps = 30.0
    return int(round(max(0, int(us)) / 1e6 * fps))


def split_timecode(us: int, fps: float) -> Tuple[int, int, int, int]:
    """微秒 → (时, 分, 秒, 帧)。"""
    fps_int = max(1, int(round(float(fps or 30.0))))
    total = frame_index(us, fps_int)
    frames = total % fps_int
    seconds = total // fps_int
    return seconds // 3600, (seconds // 60) % 60, seconds % 60, frames


def format_timecode(us: int, fps: float = 30.0) -> str:
    """``00:00:03:00`` 形式。"""
    h, m, s, f = split_timecode(us, fps)
    return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"


def format_short(us: int, fps: float = 30.0) -> str:
    """时长用的短形式 ``03:12``（分:秒，超过一小时带小时）。"""
    h, m, s, _f = split_timecode(us, fps)
    if h:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"
